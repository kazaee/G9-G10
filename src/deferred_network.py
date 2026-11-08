"""Retry port selection after an offline installation with no physical link."""
import json
from pathlib import Path
import subprocess
import time

ROOT = Path('/etc/provision-network')


def run(*args):
    return subprocess.run(args, capture_output=True, text=True, timeout=20)


def choose(active, candidates):
    if len(candidates) == 1:
        return candidates[0]
    if not candidates and len(active) == 1:
        return active[0]
    return None


def main():
    if (ROOT / 'selected.json').exists():
        return
    settings = json.loads((ROOT / 'intent.json').read_text())
    allowed = set(settings['allowed_macs'])
    links = json.loads(run('ip', '-j', 'link').stdout)
    ports = [n for n in links if n.get('address', '').lower() in allowed
             and (Path('/sys/class/net') / n['ifname'] / 'device').exists()]
    for nic in ports:
        run('ip', 'link', 'set', nic['ifname'], 'up')
    time.sleep(3)
    active = [n for n in ports if (Path('/sys/class/net') / n['ifname'] / 'carrier').read_text().strip() == '1']
    candidates = []
    for nic in active:
        name = nic['ifname']
        addresses = json.loads(run('ip', '-j', 'addr', 'show', 'dev', name).stdout)
        if any(a.get('family') == 'inet' and a.get('scope') == 'global' for n in addresses for a in n.get('addr_info', [])):
            # Leave later administrator configuration alone.
            return
        added = run('ip', 'addr', 'add', settings['address'], 'dev', name)
        if added.returncode:
            continue
        try:
            ping = run('ping', '-I', name, '-c', '2', '-W', '2', settings['gateway'])
            neighbors = json.loads(run('ip', '-j', 'neigh', 'show', settings['gateway'], 'dev', name).stdout)
            if ping.returncode == 0 or any(n.get('lladdr') and 'FAILED' not in n.get('state', []) for n in neighbors):
                candidates.append(nic)
        finally:
            run('ip', 'addr', 'del', settings['address'], 'dev', name)
    selected = choose(active, candidates)
    if not selected:
        print('Network pending: no unique connected/gateway-verified port', flush=True)
        return
    nameservers = {'addresses': settings['dns']}
    if settings.get('domain'):
        nameservers['search'] = [settings['domain']]
    data = {'network': {'version': 2, 'ethernets': {'management': {
        'match': {'macaddress': selected['address'].lower()}, 'optional': True,
        'dhcp4': False, 'dhcp6': False, 'addresses': [settings['address']],
        'routes': [{'to': 'default', 'via': settings['gateway']}], 'nameservers': nameservers}}}}
    path = Path('/etc/netplan/90-provision-management.yaml')
    path.write_text(json.dumps(data, indent=2))
    path.chmod(0o600)
    subprocess.run(['netplan', 'apply'], timeout=60, check=True)
    (ROOT / 'selected.json').write_text(json.dumps({'mac': selected['address'], 'interface': selected['ifname']}))
    print('Network configured on ' + selected['ifname'], flush=True)


if __name__ == '__main__':
    main()
