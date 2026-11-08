"""Installer-side preflight and completion evidence; no iLO credentials."""
import json
import base64
import zlib
import shutil
import pathlib
import subprocess
import sys
import time
import urllib.request
import copy
import yaml


def bind_verified_disks(config, bindings):
    """Subiquity matches canonical paths; resolve only after checking stable IDs."""
    result = copy.deepcopy(config)
    settings = result.get('autoinstall', result)
    used = set()
    for action in settings['storage']['config']:
        if action.get('type') != 'disk':
            continue
        stable = action.get('path')
        if stable not in bindings or stable in used:
            raise ValueError('Unreviewed or duplicate disk in installation config')
        used.add(stable)
        action['path'] = bindings[stable]
    if used != set(bindings):
        raise ValueError('Reviewed disks and installation config differ')
    return result


def post(settings, event, **extra):
    body = json.dumps(dict(event=event, run_id=settings['run_id'], **extra)).encode()
    line = '\nPROVISION_ZLIB_BASE64=' + base64.b64encode(zlib.compress(body)).decode() + '\n'
    with open('/dev/ttyS1', 'w') as console:
        console.write(line)
    if settings.get('offline'):
        return
    for _ in range(6):
        try:
            urllib.request.urlopen(urllib.request.Request(settings['callback'], data=body,
                headers={'Content-Type': 'application/json'}), timeout=10).close()
            return
        except Exception:
            time.sleep(5)
    raise RuntimeError('Cannot report installer status to the provisioner')


def configure_root(target, settings):
    if settings['username'] != 'root':
        return
    port = int(settings['ssh_port'])
    if not 1 <= port <= 65535:
        raise ValueError('Invalid SSH port')
    shadow = target / 'etc/shadow'
    lines = shadow.read_text().splitlines()
    roots = [i for i, line in enumerate(lines) if line.startswith('root:')]
    if len(roots) != 1:
        raise RuntimeError('Target root account is missing or ambiguous')
    fields = lines[roots[0]].split(':')
    fields[1] = settings['root_password_hash']
    lines[roots[0]] = ':'.join(fields)
    shadow.write_text('\n'.join(lines) + '\n')
    ssh_dir = target / 'root/.ssh'
    ssh_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    ssh_dir.chmod(0o700)
    authorized = ssh_dir / 'authorized_keys'
    authorized.write_text(settings['ssh_public_key'].strip() + '\n')
    authorized.chmod(0o600)
    ssh_config = target / 'etc/ssh/sshd_config.d/00-provision-root.conf'
    ssh_config.parent.mkdir(parents=True, exist_ok=True)
    ssh_config.write_text(f'Port {port}\nPermitRootLogin yes\nPasswordAuthentication yes\n')


def main():
    settings = json.loads(pathlib.Path('/cdrom/install.json').read_text())
    stage = sys.argv[1]
    boot_id = pathlib.Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    if stage == 'preflight':
        if pathlib.Path('/sys/firmware/efi').exists() != (settings['boot_mode'] == 'uefi'):
            raise RuntimeError('Installer firmware mode differs from reviewed plan')
        resolved = set()
        bindings = {}
        for disk in settings['disks']:
            path = pathlib.Path(disk['path'])
            if not path.exists() or str(path.resolve()) in resolved:
                raise RuntimeError('Selected disk missing or ambiguous: ' + str(path))
            resolved.add(str(path.resolve()))
            raw = subprocess.check_output(['lsblk', '-J', '-b', '-d', '-o', 'PATH,SIZE,TYPE,WWN,SERIAL', str(path)], text=True)
            actual = json.loads(raw)['blockdevices'][0]
            if actual['type'] != 'disk' or abs(actual['size'] - disk['size']) > 8 * 1024**2:
                raise RuntimeError('Disk type or capacity changed since review: ' + str(path))
            if disk.get('wwn') and actual.get('wwn') != disk['wwn']:
                raise RuntimeError('Disk WWN changed since review')
            bindings[disk['path']] = str(path.resolve())
        # Subiquity rereads this file after early-commands. A by-id path is not
        # equal to its internal /dev/sdX path, despite referring to the same disk.
        autoinstall = pathlib.Path('/autoinstall.yaml')
        bound = bind_verified_disks(yaml.safe_load(autoinstall.read_text()), bindings)
        autoinstall.write_text(yaml.safe_dump(bound, sort_keys=False))
        pathlib.Path('/tmp/provision-disk-bindings.json').write_text(json.dumps(bindings))
        # Start report may precede static-network apply; do not block preflight on network.
        pathlib.Path('/tmp/provision-installer-boot-id').write_text(boot_id)
    elif stage == 'complete':
        if settings['username'] == 'root':
            configure_root(pathlib.Path('/target'), settings)
            pathlib.Path('/target/run/sshd').mkdir(parents=True, exist_ok=True)
            subprocess.run(['curtin', 'in-target', '--target=/target', '--', '/usr/sbin/sshd', '-t'], check=True)
            subprocess.run(['curtin', 'in-target', '--target=/target', '--', 'userdel', '-r', 'provision-installer'], check=True)
        marker = {'run_id': settings['run_id'], 'installer_boot_id': boot_id, 'username': settings['username']}
        pathlib.Path('/target/etc/provision-result.json').write_text(json.dumps(marker))
        subprocess.run(['curtin', 'in-target', '--target=/target', '--', 'ssh-keygen', '-A'], check=True)
        subprocess.run(['curtin', 'in-target', '--target=/target', '--', 'systemctl', 'enable', 'serial-getty@ttyS1.service'], check=True)
        host_key = pathlib.Path('/target/etc/ssh/ssh_host_ed25519_key.pub').read_text().strip()
        if settings['network'].get('deferred'):
            root = pathlib.Path('/target/etc/provision-network')
            root.mkdir(exist_ok=True)
            (root / 'intent.json').write_text(json.dumps(settings['network']))
            shutil.copyfile('/cdrom/deferred_network.py', '/target/usr/local/sbin/provision-network.py')
            pathlib.Path('/target/etc/cloud/cloud.cfg.d/99-provision-network.cfg').write_text('network: {config: disabled}\n')
            systemd = pathlib.Path('/target/etc/systemd/system')
            (systemd / 'provision-network.service').write_text('[Unit]\nDescription=Detect provisioning network port\nAfter=network.target\n'
                '[Service]\nType=oneshot\nExecStart=/usr/bin/python3 /usr/local/sbin/provision-network.py\nTimeoutStartSec=180\n')
            (systemd / 'provision-network.timer').write_text('[Unit]\nDescription=Retry provisioning network detection\n'
                '[Timer]\nOnBootSec=15\nOnUnitInactiveSec=30\n[Install]\nWantedBy=timers.target\n')
            wants = systemd / 'timers.target.wants'
            wants.mkdir(exist_ok=True)
            (wants / 'provision-network.timer').symlink_to('../provision-network.timer')
        if settings.get('offline'):
            shutil.copyfile('/cdrom/postboot_report.py', '/target/usr/local/sbin/provision-boot-report.py')
            service = pathlib.Path('/target/etc/systemd/system/provision-boot-report.service')
            service.write_text('[Unit]\nDescription=Provisioning boot evidence over iLO serial\nAfter=systemd-user-sessions.service network.target\n'
                '[Service]\nType=oneshot\nExecStart=/usr/bin/python3 /usr/local/sbin/provision-boot-report.py\n'
                '[Install]\nWantedBy=multi-user.target\n')
            wants = pathlib.Path('/target/etc/systemd/system/multi-user.target.wants')
            wants.mkdir(exist_ok=True)
            (wants / service.name).symlink_to('../' + service.name)
        # Preserve installer logs on the target; callback carries concise evidence.
        post(settings, 'install-complete', installer_boot_id=boot_id, host_key=host_key)
    else:
        logs = {}
        for name in ('subiquity-server-debug.log', 'curtin-install.log'):
            file = pathlib.Path('/var/log/installer') / name
            if file.exists():
                logs[name] = file.read_text(errors='replace')[-100000:]
        post(settings, 'install-failed', logs=logs)


if __name__ == '__main__':
    main()
