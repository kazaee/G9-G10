"""Runs inside the Ubuntu live environment. Never writes to server disks."""
import json
import base64
import zlib
import pathlib
import subprocess
import sys
import time
import urllib.request
import yaml


def existing_systems(blockdevices, mountpoint=None):
    """Inspect ext4 installations read-only, without journal replay or password disclosure."""
    def flatten(items):
        for item in items:
            yield item
            yield from flatten(item.get('children', []))
    found = []
    mountpoint = pathlib.Path(mountpoint or '/run/provision-existing')
    mountpoint.mkdir(exist_ok=True)
    for disk in flatten(blockdevices):
        if disk.get('fstype') != 'ext4' or any(disk.get('mountpoints') or []):
            continue
        device = '/dev/' + disk['name']
        result = run('mount', '-t', 'ext4', '-o', 'ro,noload', device, str(mountpoint))
        if result.returncode:
            found.append({'device': device, 'read_error': result.stderr})
            continue
        try:
            release = mountpoint / 'usr/lib/os-release'
            if not release.exists():
                continue
            entry = {'device': device, 'os': release.read_text(),
                     'hostname': (mountpoint / 'etc/hostname').read_text().strip(), 'accounts': [], 'network': {}}
            shadow = {}
            for line in (mountpoint / 'etc/shadow').read_text().splitlines():
                fields = line.split(':')
                shadow[fields[0]] = 'locked' if fields[1].startswith(('!', '*')) else ('empty' if not fields[1] else 'password-set')
            for line in (mountpoint / 'etc/passwd').read_text().splitlines():
                fields = line.split(':')
                if fields[0] == 'root' or 1000 <= int(fields[2]) < 65534:
                    entry['accounts'].append({'name': fields[0], 'uid': int(fields[2]), 'shell': fields[-1],
                                              'password_state': shadow.get(fields[0], 'unknown')})
            for file in (mountpoint / 'etc/netplan').glob('*.yaml'):
                config = (yaml.safe_load(file.read_text()) or {}).get('network', {})
                entry['network'][file.name] = {group: {name: {key: value for key, value in values.items()
                    if key in ('match', 'set-name', 'addresses', 'routes', 'gateway4', 'nameservers', 'dhcp4', 'interfaces', 'id', 'link')}
                    for name, values in config.get(group, {}).items()} for group in ('ethernets', 'bonds', 'vlans')}
            marker = mountpoint / 'etc/provision-result.json'
            entry['provision_marker'] = json.loads(marker.read_text()) if marker.exists() else None
            entry['installer_log_times'] = {p.name: p.stat().st_mtime for p in (mountpoint / 'var/log/installer').glob('*') if p.is_file()}
            found.append(entry)
        except Exception as exc:
            found.append({'device': device, 'inspection_error': type(exc).__name__})
        finally:
            run('umount', str(mountpoint))
    return found


def run(*args):
    return subprocess.run(args, capture_output=True, text=True, timeout=30)


def select_nic(nics, candidates, preferred=''):
    if len(candidates) == 1:
        return dict(candidates[0], gateway_verified=True)
    if preferred:
        match = next((n for n in candidates if n['mac'].lower() == preferred.lower()), None)
        if match:
            return dict(match, gateway_verified=True)
    if not candidates:
        active = [n for n in nics if n.get('carrier')]
        if len(active) == 1:
            return {'name': active[0]['name'], 'mac': active[0]['mac'], 'gateway_verified': False}
    return None


def console(message):
    print(message, flush=True)
    for name in ('/dev/console', '/dev/ttyS1'):
        try:
            with open(name, 'w') as handle:
                handle.write('\n[PROBE] ' + message + '\n')
        except OSError:
            pass


def main():
    console('Linux discovery started; disks will not be modified')
    settings = json.loads(pathlib.Path('/cdrom/probe.json').read_text())
    links = json.loads(run('ip', '-j', 'link').stdout)
    nics = []
    selected = None
    candidates = []
    diagnostics = {}
    for nic in links:
        name = nic['ifname']
        if name == 'lo' or not pathlib.Path('/sys/class/net', name, 'device').exists():
            continue
        up = run('ip', 'link', 'set', name, 'up')
        diagnostics[name] = {'activate_returncode': up.returncode, 'activate_error': up.stderr}
    time.sleep(8)
    for nic in links:
        name = nic['ifname']
        path = pathlib.Path('/sys/class/net', name)
        if name == 'lo' or not (path / 'device').exists():
            continue
        carrier = (path / 'carrier').read_text().strip() == '1'
        console(f'NIC {name} MAC={nic["address"]} carrier={carrier}')
        nics.append({'name': name, 'mac': nic['address'], 'carrier': carrier})
        try:
            diagnostics[name]['ethtool'] = run('ethtool', name).stdout
        except FileNotFoundError:
            diagnostics[name]['ethtool'] = 'not available'
        if not carrier:
            continue
        # Test one physical port at a time; only RAM-resident live networking changes.
        run('ip', 'addr', 'add', settings['address'], 'dev', name)
        run('ip', 'route', 'replace', 'default', 'via', settings['gateway'], 'dev', name)
        test = run('ping', '-I', name, '-c', '3', '-W', '2', settings['gateway'])
        diagnostics[name]['gateway_ping'] = {'returncode': test.returncode, 'stdout': test.stdout, 'stderr': test.stderr}
        neighbors = json.loads(run('ip', '-j', 'neigh', 'show', settings['gateway'], 'dev', name).stdout)
        diagnostics[name]['neighbors'] = neighbors
        if test.returncode == 0 or any(n.get('lladdr') and 'FAILED' not in n.get('state', []) for n in neighbors):
            candidates.append({'name': name, 'mac': nic['address']})
        run('ip', 'addr', 'del', settings['address'], 'dev', name)
    selected = select_nic(nics, candidates, settings.get('mac', ''))
    if selected or candidates:
        transport = selected or candidates[0]
        run('ip', 'addr', 'add', settings['address'], 'dev', transport['name'])
        run('ip', 'route', 'replace', 'default', 'via', settings['gateway'], 'dev', transport['name'])
    inventory = {'event': 'live-inventory', 'probe_id': settings.get('probe_id'),
        'server_serial': settings.get('server_serial'),
        'selected_nic': selected, 'candidates': candidates, 'nics': nics,
        'uefi': pathlib.Path('/sys/firmware/efi').exists(),
        'lsblk': json.loads(run('lsblk', '-J', '-b', '-O').stdout),
        'by_id': {p.name: str(p.resolve()) for p in pathlib.Path('/dev/disk/by-id').glob('*')},
        'ip': json.loads(run('ip', '-j', 'addr').stdout), 'route': run('ip', 'route').stdout}
    inventory['nic_diagnostics'] = diagnostics
    inventory['existing_systems'] = existing_systems(inventory['lsblk']['blockdevices'])
    pathlib.Path('/tmp/live-inventory.json').write_text(json.dumps(inventory, indent=2))
    console('Gateway candidates: ' + json.dumps(candidates))
    console('Disks: ' + json.dumps([{k: d.get(k) for k in ('name', 'size', 'type', 'model', 'serial', 'wwn')}
        for d in inventory['lsblk']['blockdevices'] if d['type'] == 'disk']))
    body = json.dumps(inventory).encode()
    # iLO serial remains available even when all OS network ports are down.
    console('INVENTORY_ZLIB_BASE64=' + base64.b64encode(zlib.compress(body)).decode())
    while True:
        try:
            req = urllib.request.Request(settings['callback'], data=body, headers={'Content-Type': 'application/json'})
            urllib.request.urlopen(req, timeout=15).close()
            break
        except Exception as exc:
            console('Inventory callback pending: ' + str(exc))
            time.sleep(10)
    # Stay in early-commands. There is deliberately no path to disk installation.
    while True:
        time.sleep(30)


if __name__ == '__main__':
    main()
