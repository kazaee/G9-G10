"""Installed-system boot evidence, sent over iLO serial without an OS network."""
import base64
import json
import pathlib
import socket
import subprocess
import time
import zlib


def command(*args):
    return subprocess.check_output(args, text=True, timeout=30)


def main():
    subprocess.run(['stty', '-F', '/dev/ttyS1', '115200'], check=True)
    marker = json.loads(pathlib.Path('/etc/provision-result.json').read_text())
    account = {'username': marker['username'], 'password_set': False, 'sudo_member': False}
    for line in pathlib.Path('/etc/shadow').read_text().splitlines():
        fields = line.split(':')
        if fields[0] == marker['username']:
            account['password_set'] = bool(fields[1]) and not fields[1].startswith(('!', '*'))
    for line in pathlib.Path('/etc/group').read_text().splitlines():
        fields = line.split(':')
        if fields[0] == 'sudo':
            account['sudo_member'] = marker['username'] in fields[3].split(',')
    ssh_config = pathlib.Path('/etc/ssh/sshd_config.d/00-provision-root.conf')
    ssh_settings = ssh_config.read_text() if ssh_config.exists() else ''
    evidence = {
        'event': 'installed-boot', 'run_id': marker['run_id'], 'marker': marker,
        'administrator': account,
        'ssh_settings': ssh_settings,
        'installer_account_absent': not any(line.startswith('provision-installer:')
            for line in pathlib.Path('/etc/passwd').read_text().splitlines()),
        'boot_id': pathlib.Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
        'os': pathlib.Path('/etc/os-release').read_text(), 'hostname': socket.gethostname(),
        'ip': json.loads(command('ip', '-j', 'addr')),
        'route': json.loads(command('ip', '-j', 'route')),
        'mounts': json.loads(command('findmnt', '-J')),
        'disks': json.loads(command('lsblk', '-J', '-b', '-o', 'NAME,SIZE,TYPE,FSTYPE,MOUNTPOINTS,WWN,SERIAL')),
        'netplan_files': {p.name: p.read_text() for p in pathlib.Path('/etc/netplan').glob('*.yaml')},
        'network_intent': json.loads(pathlib.Path('/etc/provision-network/intent.json').read_text()) if pathlib.Path('/etc/provision-network/intent.json').exists() else None,
        'network_retry_enabled': pathlib.Path('/etc/systemd/system/timers.target.wants/provision-network.timer').exists(),
        'mdstat': pathlib.Path('/proc/mdstat').read_text(),
    }
    pathlib.Path('/var/log/provision-boot-evidence.json').write_text(json.dumps(evidence, indent=2))
    line = '\nPROVISION_ZLIB_BASE64=' + base64.b64encode(zlib.compress(json.dumps(evidence).encode())).decode() + '\n'
    for _ in range(6):
        with open('/dev/ttyS1', 'w') as console:
            console.write(line)
        time.sleep(10)


if __name__ == '__main__':
    main()
