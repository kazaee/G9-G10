"""Require installer evidence and authenticated SSH before reporting success."""
import ipaddress
import json
import re
import socket
import subprocess
import time
from .os_catalog import identity


REMOTE_CHECK = """python3 - <<'PY'
import json, pathlib, subprocess, socket
def command(*args):
    return subprocess.check_output(args, text=True)
print(json.dumps({'os': pathlib.Path('/etc/os-release').read_text(),
 'hostname': socket.gethostname(), 'ip': json.loads(command('ip','-j','addr')),
 'route': json.loads(command('ip','-j','route')), 'mounts': json.loads(command('findmnt','-J')),
 'disks': json.loads(command('lsblk','-J','-b','-o','NAME,SIZE,TYPE,FSTYPE,MOUNTPOINTS,WWN,SERIAL')),
 'mdstat': pathlib.Path('/proc/mdstat').read_text(),
 'marker': json.loads(pathlib.Path('/etc/provision-result.json').read_text()),
 'root_password_set': any(line.startswith('root:') and not line.split(':')[1].startswith(('!','*'))
    for line in pathlib.Path('/etc/shadow').read_text().splitlines()),
 'installer_account_absent': not any(line.startswith('provision-installer:')
    for line in pathlib.Path('/etc/passwd').read_text().splitlines()),
 'ssh_settings': pathlib.Path('/etc/ssh/sshd_config.d/00-provision-root.conf').read_text()
    if pathlib.Path('/etc/ssh/sshd_config.d/00-provision-root.conf').exists() else '',
 'boot_id': pathlib.Path('/proc/sys/kernel/random/boot_id').read_text().strip()}))
PY"""


def verify(config, key_path, known_hosts, completion, run_id, plan=None, timeout=900):
    ip = str(ipaddress.ip_interface(config['network']['address']).ip)
    port = int(config['identity'].get('ssh_port', 22))
    known_hosts.write_text((ip if port == 22 else f'[{ip}]:{port}') + ' ' + completion['host_key'] + '\n', encoding='utf-8')
    deadline = time.monotonic() + timeout
    last_error = 'SSH not reachable'
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((ip, port), 5):
                pass
            result = subprocess.run(['ssh', '-p', str(port), '-i', str(key_path), '-o', 'BatchMode=yes',
                '-o', 'ConnectTimeout=8', '-o', 'StrictHostKeyChecking=yes',
                '-o', 'UserKnownHostsFile=' + str(known_hosts.resolve()),
                config['identity']['username'] + '@' + ip, REMOTE_CHECK],
                text=True, capture_output=True, timeout=30)
            if result.returncode:
                last_error = result.stderr.strip()
                time.sleep(10)
                continue
            evidence = json.loads(result.stdout)
            os_name, os_version = identity(config)
            checks = {
                'operating_system': f'ID={os_name}' in evidence['os'] and f'VERSION_ID="{os_version}"' in evidence['os'],
                'hostname': evidence['hostname'] == config['network']['hostname'],
                'ip': any(a.get('local') == ip for nic in evidence['ip'] for a in nic.get('addr_info', [])),
                'gateway': any(r.get('dst') == 'default' and r.get('gateway') == config['network']['gateway'] for r in evidence['route']),
                'run_id': evidence['marker']['run_id'] == run_id,
                'rebooted': evidence['boot_id'] != completion['installer_boot_id'],
            }
            if config['identity']['username'] == 'root':
                ssh = evidence['ssh_settings']
                checks['root_access'] = (evidence['root_password_set']
                    and evidence['installer_account_absent']
                    and f"Port {config['identity'].get('ssh_port', 22)}\n" in ssh
                    and 'PermitRootLogin yes\n' in ssh and 'PasswordAuthentication yes\n' in ssh)
            evidence['checks'] = checks
            def flatten(items):
                for item in items:
                    yield item
                    yield from flatten(item.get('children', []))
            if plan:
                mounts = {m['target']: m for m in flatten(evidence['mounts']['filesystems'])}
                expected = [p for disk in plan['disks'] for p in disk['partitions']] + plan.get('lvm', [])
                checks['filesystems'] = all(p['mount'] in mounts and mounts[p['mount']]['fstype'] ==
                    {'fat32': 'vfat'}.get(p['filesystem'], p['filesystem'])
                    for p in expected if p.get('mount') and p.get('filesystem') != 'swap')
                if plan.get('software_raid'):
                    mdstat = evidence.get('mdstat', '')
                    level = plan['software_raid']['level']
                    checks['software_raid_active'] = (len(re.findall(r'\braid1\b', mdstat)) >= (2 if level == 1 else 1)
                        and len(re.findall(r'\braid0\b', mdstat)) >= (1 if level == 0 else 0)
                        and len(re.findall(r'\[UU\]', mdstat)) >= (2 if level == 1 else 1))
                    checks['mirrored_boot'] = mounts.get('/boot', {}).get('fstype') == 'ext4'
                # Confirm each planned mount descends from the selected physical/logical disk.
                descendants = list(evidence['disks']['blockdevices'])
                checks['disk_layout'] = True
                for disk in plan['disks']:
                    path = disk['path']
                    if '/wwn-' not in path:
                        continue
                    wwn = path.split('/wwn-', 1)[1].lower()
                    actual = next((d for d in descendants if (d.get('wwn') or '').lower() == wwn), None)
                    if actual is None:
                        checks['disk_layout'] = False
                        continue
                    actual_mounts = {m for d in flatten([actual]) for m in (d.get('mountpoints') or []) if m}
                    required = {p['mount'] for p in disk['partitions'] if p.get('mount')}
                    for pv in disk['partitions']:
                        required.update(lv['mount'] for lv in plan.get('lvm', []) if lv['vg'] == pv.get('vg') and lv.get('mount'))
                    checks['disk_layout'] &= required <= actual_mounts
            if not all(checks.values()):
                raise RuntimeError('Post-install checks failed: ' + json.dumps(checks))
            return evidence
        except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
            last_error = str(exc)
        time.sleep(10)
    raise RuntimeError('Post-install SSH verification timed out: ' + last_error)
