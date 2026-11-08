"""Build a Debian installer CD with a reviewed, single-disk preseed."""
import hashlib
import io
import ipaddress
import re
from pathlib import Path

import pycdlib

from .network import validate as validate_network
from .storage import compile_plan, size_bytes

IMAGES = {
    '11.0.0': ('debian-11.0.0-amd64-DVD-1.iso', 3947823104,
               '89699678ac432ec8f43afc5fc1e1f088b1651f436520cc46dc79c1e14af6b816'),
    '12.0.0': ('debian-12.0.0-amd64-netinst.iso', 773849088,
               '3b0e9718e3653435f20d8c2124de6d363a51a1fd7f911b9ca0c6db6b3d30d53e'),
    '13.7.0': ('debian-13.7.0-amd64-netinst.iso', 792723456,
               'a7ef94ac2fb9a7fec454552abd629b7cc9d5155c886165a45649f5ce6167e355'),
}


def inspect_media(source, version):
    source = Path(source)
    name, length, digest = IMAGES[version]
    if source.name != name or source.stat().st_size != length:
        raise ValueError(f'Debian {version} ISO filename or size does not match the expected official image')
    with source.open('rb') as stream:
        if hashlib.file_digest(stream, 'sha256').hexdigest() != digest:
            raise ValueError(f'Debian {version} ISO SHA-256 does not match the published image')
    iso = pycdlib.PyCdlib()
    iso.open(str(source))
    try:
        volume = iso.pvd.volume_identifier.decode('ascii', errors='replace').strip()
        if not volume.startswith(f'Debian {version} amd64 '):
            raise ValueError('Unexpected Debian ISO volume identifier')
        for path in ('/boot/grub/grub.cfg', '/install.amd/vmlinuz', '/install.amd/initrd.gz'):
            iso.get_record(rr_path=path)
        if not iso.eltorito_boot_catalog or not any(
                section.platform_id == 0xef for section in iso.eltorito_boot_catalog.sections):
            raise ValueError('Debian ISO has no UEFI El Torito boot entry')
        return volume
    finally:
        iso.close()


def preseed(plan, inventory, network, identity, run_id):
    """Constrain partman to one reviewed GPT disk; other disks remain untouched."""
    if plan['boot_mode'] != 'uefi':
        raise ValueError('Debian automated installer currently requires UEFI boot mode.')
    if plan.get('software_raid'):
        raise ValueError('Debian automated installer does not support software RAID yet.')
    if plan.get('lvm') or any('vg' in part for disk in plan['disks'] for part in disk['partitions']):
        raise ValueError('Debian automated installer does not support LVM yet.')
    if len(plan['disks']) != 1:
        raise ValueError('This Debian installer backend cannot yet apply different direct-partition layouts to multiple disks. Your two-disk plan is not RAID or LVM; no disks were changed.')
    compile_plan(plan, inventory)
    validate_network(network)
    disk = plan['disks'][0]
    if disk['path'] != plan['boot_disk'] or disk.get('ptable') != 'gpt':
        raise ValueError('Debian boot disk must be the selected GPT installation disk')
    if not re.fullmatch(r'/dev/disk/by-id/[A-Za-z0-9_.+-]+', disk['path']):
        raise ValueError('Debian requires a stable /dev/disk/by-id installation target')
    if not re.fullmatch(r'\$6\$(?:rounds=\d+\$)?[./A-Za-z0-9]{1,16}\$[./A-Za-z0-9]{86}', identity.get('password_hash', '')):
        raise ValueError('Debian root needs a SHA-512 password hash')
    if not re.fullmatch(r'[0-9a-f]{32}', run_id):
        raise ValueError('Invalid installation run ID')
    port = int(identity.get('ssh_port', 22))
    if not 1 <= port <= 65535:
        raise ValueError('Invalid SSH port')
    address = ipaddress.IPv4Interface(network['address'])
    parts = disk['partitions']
    if not parts or parts[0].get('filesystem') != 'fat32' or parts[0].get('mount') != '/boot/efi':
        raise ValueError('Debian requires the EFI partition first on the selected disk')
    if sum(size_bytes(p['size']) == -1 for p in parts) != 1:
        raise ValueError('Debian requires exactly one partition using remaining space')
    recipe = []
    for part in parts:
        fs, mount = part.get('filesystem'), part.get('mount')
        size = size_bytes(part['size'])
        mb = (size + 1024**2 - 1) // 1024**2 if size != -1 else 1024
        limits = f'{mb} {mb} {mb}' if size != -1 else '1024 10000 1000000000'
        if fs == 'fat32' and mount == '/boot/efi':
            if size < 512 * 1024**2:
                raise ValueError('Debian EFI partition must be at least 512 MiB')
            entry = f'{limits} free $primary{{ }} $iflabel{{ gpt }} method{{ efi }} format{{ }}'
        elif fs == 'swap' and not mount:
            entry = f'{limits} linux-swap method{{ swap }} format{{ }}'
        elif fs == 'ext4' and isinstance(mount, str) and re.fullmatch(r'/[A-Za-z0-9_./-]*', mount):
            entry = (f'{limits} ext4 $primary{{ }} method{{ format }} format{{ }} '
                     f'use_filesystem{{ }} filesystem{{ ext4 }} mountpoint{{ {mount} }}')
        else:
            raise ValueError('Debian test backend supports EFI, ext4 mountpoints and swap only')
        recipe.append(entry + ' .')
    if not any(p.get('mount') == '/' for p in parts):
        raise ValueError('Debian requires a root partition')
    # Disk identifiers may not exist when preseed/early_command runs. Resolve
    # the reviewed by-id target immediately before partman starts instead.
    # netcfg itself supports selecting its interface by link-layer address.
    mac = network['mac'].lower().replace('-', ':')
    partman_early = ("disk=$(readlink -f " + disk['path'] + "); "
                     "test -b \"$disk\" || exit 1; "
                     "debconf-set partman-auto/disk \"$disk\"; "
                     "debconf-set grub-installer/bootdev \"$disk\"")
    lines = [
        'd-i debian-installer/locale string en_US.UTF-8',
        'd-i keyboard-configuration/xkb-keymap select us',
        'd-i netcfg/choose_interface select ' + mac,
        'd-i netcfg/disable_autoconfig boolean true',
        'd-i netcfg/get_ipaddress string ' + str(address.ip),
        'd-i netcfg/get_netmask string ' + str(address.netmask),
        'd-i netcfg/get_gateway string ' + network['gateway'],
        'd-i netcfg/get_nameservers string ' + ' '.join(network['dns']),
        'd-i netcfg/confirm_static boolean true',
        'd-i netcfg/get_hostname string ' + network['hostname'],
        'd-i netcfg/get_domain string ' + network.get('domain', 'local'),
        'd-i hw-detect/load_firmware boolean true',
        'd-i mirror/country string manual',
        'd-i mirror/http/hostname string deb.debian.org',
        'd-i mirror/http/directory string /debian',
        'd-i mirror/http/proxy string',
        'd-i apt-setup/use_mirror boolean false',
        'd-i passwd/root-login boolean true',
        'd-i passwd/make-user boolean false',
        'd-i passwd/root-password-crypted password ' + identity['password_hash'],
        'd-i clock-setup/utc boolean true',
        'd-i time/zone string Asia/Tehran',
        'd-i partman/early_command string ' + partman_early,
        'd-i partman-auto/method string regular',
        'd-i partman-partitioning/choose_label select gpt',
        'd-i partman-partitioning/default_label string gpt',
        'd-i partman-auto/expert_recipe string provision :: ' + ' '.join(recipe),
        'd-i partman-auto/choose_recipe select provision',
        'd-i partman-lvm/device_remove_lvm boolean true',
        'd-i partman-md/device_remove_md boolean true',
        'd-i partman-partitioning/confirm_write_new_label boolean true',
        'd-i partman/choose_partition select finish',
        'd-i partman/confirm boolean true',
        'd-i partman/confirm_nooverwrite boolean true',
        'd-i base-installer/install-recommends boolean false',
        'd-i apt-setup/non-free-firmware boolean true',
        'tasksel tasksel/first multiselect',
        'd-i pkgsel/include string openssh-server python3',
        'd-i pkgsel/upgrade select none',
        'd-i grub-installer/only_debian boolean true',
        'd-i grub-installer/bootdev string ' + disk['path'],
        'd-i preseed/late_command string cp /cdrom/report.sh /target/usr/local/sbin/provision-report; '
          'chmod 700 /target/usr/local/sbin/provision-report; '
          "mkdir -p /target/etc/ssh/sshd_config.d /target/etc/systemd/system/multi-user.target.wants; "
          "printf 'Port " + str(port) + "\\nPermitRootLogin yes\\nPasswordAuthentication yes\\n' "
          '> /target/etc/ssh/sshd_config.d/00-provision-root.conf; '
          'cp /cdrom/report.srv /target/etc/systemd/system/provision-report.service; '
          'ln -sf ../provision-report.service '
          '/target/etc/systemd/system/multi-user.target.wants/provision-report.service',
        'd-i finish-install/reboot_in_progress note',
        'd-i cdrom-detect/eject boolean true',
    ]
    answers = '\n'.join(lines) + '\n'
    # cdebconf expands ${...} in question values as its own substitutions;
    # shell parameter expansion in preseed values can crash its frontend.
    if '${' in answers:
        raise ValueError('Debian preseed contains an unsafe cdebconf substitution')
    return answers


def report_script(run_id, report_url, port):
    if not re.fullmatch(r'[0-9a-f]{32}', run_id) or not 1 <= int(port) <= 65535:
        raise ValueError('Invalid first-boot run ID or SSH port')
    if not re.fullmatch(r'http://[A-Za-z0-9.-]+:[0-9]+/[A-Za-z0-9_-]+/event', report_url):
        raise ValueError('Invalid first-boot report URL')
    return ("#!/bin/sh\n"
            "grep -q '^ID=debian$' /etc/os-release || exit 1\n"
            "systemctl is-active --quiet ssh || exit 1\n"
            f"ss -ltn | grep -q ':{port} ' || exit 1\n"
            "ip -4 route get " + report_url.split('/')[2].split(':')[0] + " >/dev/null 2>&1 || exit 1\n"
            "python3 - <<'PY'\n"
            "import json, time, urllib.request\n"
            f"url = {report_url!r}\n"
            f"body = json.dumps({{'event': 'DEBIAN_FIRSTBOOT', 'run_id': {run_id!r}, 'success': True}}).encode()\n"
            "for attempt in range(10):\n"
            "    try:\n"
            "        request = urllib.request.Request(url, data=body, headers={'Content-Type': 'application/json'})\n"
            "        with urllib.request.urlopen(request, timeout=15) as response:\n"
            "            if response.status == 200:\n"
            "                raise SystemExit(0)\n"
            "    except Exception:\n"
            "        pass\n"
            "    time.sleep(30)\n"
            "raise SystemExit(1)\n"
            "PY\n")


SERVICE = """[Unit]
Description=Report verified Debian first boot
Wants=network-online.target
After=network-online.target ssh.service
[Service]
Type=oneshot
ExecStart=/usr/local/sbin/provision-report
[Install]
WantedBy=multi-user.target
"""


def build(source, destination, answers, report):
    """Copy the official optical ISO, retaining its El Torito BIOS/UEFI entries."""
    iso = pycdlib.PyCdlib()
    iso.open(str(source))
    buffers = []
    def put(path, data, replace=False):
        if replace:
            record = iso.get_record(rr_path=path)
            iso.rm_file(iso_path=iso.full_path_from_dirrecord(record))
        raw = data.encode() if isinstance(data, str) else data
        fp = io.BytesIO(raw)
        buffers.append(fp)
        iso.add_fp(fp, len(raw), iso_path='/' + path.lstrip('/').upper().replace('-', '_') + ';1',
                   rr_name=path.rsplit('/', 1)[-1])
    # Optical-media preseeding is loaded after the first localization prompts.
    # Supply those answers on the kernel command line as well.
    grub = ('set timeout=3\nset default=0\n'
            'menuentry "Automated Debian install" {\n'
            '  linux /install.amd/vmlinuz auto=true priority=critical '
            'locale=en_US.UTF-8 language=en country=US keymap=us '
            'preseed/file=/cdrom/preseed.cfg ---\n'
            '  initrd /install.amd/initrd.gz\n}\n')
    manifest = io.BytesIO()
    iso.get_file_from_iso_fp(manifest, rr_path='/md5sum.txt')
    checksums = [line for line in manifest.getvalue().decode('ascii').splitlines()
                 if './boot/grub/grub.cfg' not in line]
    for name, content in (('boot/grub/grub.cfg', grub), ('preseed.cfg', answers),
                          ('report.sh', report), ('report.srv', SERVICE)):
        checksums.append(hashlib.md5(content.encode()).hexdigest() + '  ./' + name)
    put('/boot/grub/grub.cfg', grub, True)
    put('/preseed.cfg', answers)
    put('/report.sh', report)
    put('/report.srv', SERVICE)
    put('/md5sum.txt', '\n'.join(checksums) + '\n', True)
    iso.write(str(destination))
    iso.close()
    check = pycdlib.PyCdlib()
    check.open(str(destination))
    try:
        result = io.BytesIO()
        check.get_file_from_iso_fp(result, rr_path='/preseed.cfg')
        boot = io.BytesIO()
        check.get_file_from_iso_fp(boot, rr_path='/boot/grub/grub.cfg')
        if (result.getvalue() != answers.encode()
                or b'preseed/file=/cdrom/preseed.cfg' not in boot.getvalue()
                or not any(section.platform_id == 0xef
                           for section in check.eltorito_boot_catalog.sections)):
            raise RuntimeError('Remastered Debian ISO failed preseed or UEFI boot verification')
    finally:
        check.close()
