"""AlmaLinux minimal installation from original iLO CD plus an OEMDRV USB."""
import io
import hashlib
import ipaddress
import re
from pathlib import Path

import pycdlib

from .network import validate as validate_network
from .storage import compile_plan, size_bytes

MINIMAL_IMAGES = {
    '9.8': (2838495232, '7762a4b45a66235726db145a573658964bf77bf7b9bc1c018afe86a4cf37cc2e'),
    '10.2': (1651376128, '1b532f534231da0d1cd0ccae622bea6cd588d8a0d7b259f1f131501a6eed41a4'),
}


def inspect_media(source, expected_version):
    """Reject a mislabeled image before presenting any destructive action."""
    source = Path(source)
    size, digest = MINIMAL_IMAGES[expected_version]
    if source.stat().st_size != size:
        raise ValueError(f'AlmaLinux {expected_version} minimal ISO has an unexpected size')
    with source.open('rb') as stream:
        checksum = hashlib.file_digest(stream, 'sha256').hexdigest()
    if checksum != digest:
        raise ValueError(f'AlmaLinux {expected_version} minimal ISO failed the official SHA-256 check')
    iso = pycdlib.PyCdlib()
    iso.open(str(source))
    try:
        volume = iso.pvd.volume_identifier.decode('ascii', errors='replace').strip()
        disc = io.BytesIO()
        iso.get_file_from_iso_fp(disc, rr_path='/.discinfo')
        lines = disc.getvalue().decode('ascii', errors='replace').splitlines()
        uefi = bool(iso.eltorito_boot_catalog and
            any(section.platform_id == 0xef for section in iso.eltorito_boot_catalog.sections))
        for path in ('/Minimal/Packages', '/Minimal/repodata', '/EFI/BOOT/grub.cfg'):
            iso.get_record(rr_path=path)
        if (len(lines) < 3 or lines[1] != f'AlmaLinux {expected_version}'
                or lines[2] != 'x86_64' or not uefi):
            raise ValueError(f'Expected an AlmaLinux {expected_version} x86_64 minimal ISO with UEFI boot')
        return volume
    finally:
        iso.close()


def kickstart(plan, inventory, network, identity, run_id, report_url):
    """Translate only reviewed, direct GPT partitions; reject advanced layouts safely."""
    if plan['boot_mode'] != 'uefi' or plan.get('software_raid') or plan.get('lvm'):
        raise ValueError('AlmaLinux 9.8 test backend currently supports direct UEFI partitions only; remove LVM/RAID from the plan')
    compile_plan(plan, inventory)
    deferred = bool(network.get("deferred"))
    if not deferred:
        validate_network(network)
    if not re.fullmatch(r'[0-9a-f]{32}', run_id):
        raise ValueError('Invalid installation run ID')
    if not re.fullmatch(r'http://[A-Za-z0-9.-]+:[0-9]+/[A-Za-z0-9_-]+/event', report_url):
        raise ValueError('Invalid installation report URL')
    hashed = identity.get('password_hash', '')
    if not re.fullmatch(r'\$6\$(?:rounds=\d+\$)?[./A-Za-z0-9]{1,16}\$[./A-Za-z0-9]{86}', hashed):
        raise ValueError('AlmaLinux root needs a SHA-512 password hash')
    port = int(identity.get('ssh_port', 22))
    if not 1 <= port <= 65535:
        raise ValueError('Invalid SSH port')
    disks = [disk['path'].removeprefix('/dev/') for disk in plan['disks']]
    if any(not disk.startswith('disk/by-id/') or not re.fullmatch(r'disk/by-id/[A-Za-z0-9_.+-]+', disk) for disk in disks):
        raise ValueError('Kickstart requires stable by-id disk paths')
    selected = ','.join(disks)
    boot = plan['boot_disk'].removeprefix('/dev/')
    address = ipaddress.IPv4Interface(network['address']) if not deferred else None
    lines = [
        '# Generated for a single reviewed server; all listed disks are erased.',
        'cmdline', 'eula --agreed', 'reboot --eject', 'firstboot --disable',
        'lang en_US.UTF-8', 'keyboard --xlayouts=us', 'timezone Asia/Tehran --utc',
        'cdrom', 'rootpw --iscrypted ' + hashed,
        ('network --bootproto=dhcp --device=link --no-activate --onboot=off' if deferred else 'network --device=' + network['mac'].lower() + ' --bootproto=static --ip=' + str(address.ip)
            + ' --netmask=' + str(address.netmask) + ' --gateway=' + network['gateway']
            + ' --nameserver=' + ','.join(network['dns']) + ' --hostname=' + network['hostname'] + ' --onboot=on'),
        'services --enabled=sshd', 'selinux --enforcing',
        'ignoredisk --only-use=' + selected,
        'clearpart --all --initlabel --drives=' + selected,
        'bootloader --location=mbr --boot-drive=' + boot,
    ]
    for disk in plan['disks']:
        drive = disk['path'].removeprefix('/dev/')
        for part in disk['partitions']:
            if part.get('vg') or part.get('flag') == 'bios_grub':
                raise ValueError('AlmaLinux test backend cannot translate LVM PV or BIOS boot partitions')
            fs = part.get('filesystem')
            mount = part.get('mount')
            if fs == 'fat32' and mount == '/boot/efi':
                target, filesystem = '/boot/efi', 'efi'
            elif fs == 'swap' and not mount:
                target, filesystem = 'swap', 'swap'
            elif fs in ('ext4', 'xfs') and mount:
                target, filesystem = mount, fs
            else:
                raise ValueError('Unsupported AlmaLinux partition filesystem or mount: ' + part['name'])
            value = size_bytes(part['size'])
            size = ' --size=1 --grow' if value == -1 else f' --size={(value + 1024**2 - 1) // 1024**2}'
            lines.append(f'part {target} --fstype={filesystem}{size} --ondisk={drive}')
    lines += [
        '%packages', '@^minimal-environment', 'openssh-server', 'curl',
        'policycoreutils-python-utils', '%end',
        '%post --log=/root/provision-post.log --erroronfail',
        'mkdir -p /etc/ssh/sshd_config.d',
        "printf 'Port " + str(port) + "\\nPermitRootLogin yes\\nPasswordAuthentication yes\\n' > /etc/ssh/sshd_config.d/00-provision-root.conf",
        *(([f'semanage port -a -t ssh_port_t -p tcp {port} || semanage port -m -t ssh_port_t -p tcp {port}']
            if port != 22 else [])),
        'systemctl enable sshd',
        'if command -v firewall-offline-cmd >/dev/null 2>&1; then',
        f'  firewall-offline-cmd --add-port={port}/tcp',
        '  ' + ('firewall-offline-cmd --remove-service=ssh' if port != 22 else ':'),
        'fi',
        "printf '%s\\n' '" + run_id + "' > /etc/provision-run-id",
        'cat > /usr/local/sbin/provision-alma-boot-report <<\'REPORT\'',
        '#!/bin/sh',
        'test "$(cat /etc/provision-run-id)" = "' + run_id + '" || exit 1',
        'systemctl is-active --quiet sshd || exit 1',
        "ss -ltn | grep -q ':" + str(port) + " ' || exit 1",
        *([] if deferred else ['ip -4 route get ' + network['gateway'] + ' >/dev/null 2>&1 || exit 1']),
        'for console in /dev/ttyS0 /dev/ttyS1; do',
        '  if test -c "$console"; then',
        "    printf 'PROVISION_ALMA_BOOT=" + run_id + "\\n' > \"$console\" 2>/dev/null || :",
        '  fi',
        'done',
        *(['exit 0'] if deferred else []),
        'for attempt in 1 2 3 4 5 6 7 8 9 10; do',
        "  curl -fsS --connect-timeout 5 --max-time 15 -X POST -H 'Content-Type: application/json' " +
            "--data '{\"event\":\"ALMA_FIRSTBOOT\",\"run_id\":\"" + run_id +
            "\",\"success\":true}' '" + report_url + "' && exit 0",
        '  sleep 30',
        'done',
        'exit 1',
        'REPORT',
        'chmod 700 /usr/local/sbin/provision-alma-boot-report',
        'cat > /etc/systemd/system/provision-alma-boot-report.service <<\'UNIT\'',
        '[Unit]', 'Description=Report first AlmaLinux boot to iLO serial',
        *(['After=network.target'] if deferred else ['Wants=network-online.target', 'After=network-online.target']),
        '[Service]', 'Type=oneshot', 'ExecStart=/usr/local/sbin/provision-alma-boot-report',
        '[Install]', 'WantedBy=multi-user.target',
        'UNIT',
        'systemctl enable provision-alma-boot-report.service',
        '%end',
    ]
    if deferred:
        lines = [line for line in lines if not line.startswith('network ')]
    from .alma_direct import storage_preflight
    prefixes = ('ignoredisk ', 'clearpart ', 'bootloader ', 'part ')
    storage = [line for line in lines if line.startswith(prefixes)]
    lines = [line for line in lines if not line.startswith(prefixes)]
    lines += storage_preflight(plan, inventory, storage)
    return '\n'.join(lines) + '\n'
