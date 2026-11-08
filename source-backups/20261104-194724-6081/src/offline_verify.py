"""Verify installed disk boot through iLO; keep network reachability separate."""
import yaml
import re
from .os_catalog import identity


def validate(evidence, completion, config, plan, run_id):
    def flatten(items):
        for item in items:
            yield item
            yield from flatten(item.get('children', []))
    mounts = {m['target']: m for m in flatten(evidence['mounts']['filesystems'])}
    expected = [p for d in plan['disks'] for p in d['partitions']] + plan.get('lvm', [])
    interfaces = []
    for content in evidence['netplan_files'].values():
        data = yaml.safe_load(content) or {}
        interfaces.extend(data.get('network', {}).get('ethernets', {}).values())
    network = config['network']
    os_name, os_version = identity(config)
    checks = {
        'operating_system': f'ID={os_name}' in evidence['os'] and f'VERSION_ID="{os_version}"' in evidence['os'],
        'hostname': evidence['hostname'] == network['hostname'],
        'run_id': evidence['run_id'] == run_id and evidence['marker']['run_id'] == run_id,
        'rebooted': evidence['boot_id'] != completion['installer_boot_id'],
        'installer_marker': evidence['marker']['installer_boot_id'] == completion['installer_boot_id'],
        'disk_root': mounts.get('/', {}).get('fstype') in ('ext4', 'xfs'),
        'filesystems': all(p['mount'] in mounts and mounts[p['mount']]['fstype'] ==
            {'fat32': 'vfat'}.get(p['filesystem'], p['filesystem'])
            for p in expected if p.get('mount') and p.get('filesystem') != 'swap'),
        'network_configured': any(
            n.get('match', {}).get('macaddress', '').lower() == network['mac'].lower()
            and network['address'] in n.get('addresses', [])
            and any(r.get('to') in ('default', '0.0.0.0/0') and r.get('via') == network['gateway'] for r in n.get('routes', []))
            and n.get('nameservers', {}).get('addresses') == network['dns'] for n in interfaces),
        'disk_layout': True,
    }
    for disk in plan['disks']:
        wwn = (disk.get('wwn') or (disk['path'].split('/wwn-', 1)[-1] if '/wwn-' in disk['path'] else '')).lower()
        if wwn:
            actual = next((d for d in evidence['disks']['blockdevices']
                if (d.get('wwn') or '').lower().removeprefix('0x') == wwn.removeprefix('0x')), None)
        else:
            key = disk['path'].rsplit('/', 1)[-1].lower()
            actual = next((d for d in evidence['disks']['blockdevices']
                if ((d.get('wwn') and (d['wwn'].lower().removeprefix('0x') in key))
                    or (d.get('serial') and d['serial'].lower() in key))), None)
        required = {p['mount'] for p in disk['partitions'] if p.get('mount')}
        for p in disk['partitions']:
            required.update(lv['mount'] for lv in plan.get('lvm', []) if lv['vg'] == p.get('vg') and lv.get('mount'))
        actual_mounts = {m for d in flatten([actual] if actual else []) for m in d.get('mountpoints', []) or [] if m}
        checks['disk_layout'] &= actual is not None and required <= actual_mounts
    if plan.get('software_raid'):
        mdstat = evidence.get('mdstat', '')
        level = plan['software_raid']['level']
        checks['software_raid_active'] = (len(re.findall(r'\braid1\b', mdstat)) >= (2 if level == 1 else 1)
            and len(re.findall(r'\braid0\b', mdstat)) >= (1 if level == 0 else 0)
            and len(re.findall(r'\[UU\]', mdstat)) >= (2 if level == 1 else 1))
        checks['mirrored_boot'] = mounts.get('/boot', {}).get('fstype') == 'ext4'
    if network.get('deferred'):
        checks.pop('network_configured')
        checks['network_intent_saved'] = evidence.get('network_intent') == network
        checks['network_retry_enabled'] = evidence.get('network_retry_enabled') is True
    if config.get('identity', {}).get('ssh_password_login'):
        account = evidence.get('administrator', {})
        checks['administrator_ready'] = (account.get('username') == config['identity']['username']
            and account.get('password_set') is True
            and (account.get('username') == 'root' or account.get('sudo_member') is True))
        if account.get('username') == 'root':
            ssh = evidence.get('ssh_settings', '')
            checks['root_ssh_configured'] = (f"Port {config['identity'].get('ssh_port', 22)}\n" in ssh
                and 'PermitRootLogin yes\n' in ssh and 'PasswordAuthentication yes\n' in ssh)
            checks['installer_account_removed'] = evidence.get('installer_account_absent') is True
    evidence['checks'] = checks
    if not all(checks.values()):
        raise RuntimeError('Installed-system serial verification failed: ' + str(checks))
    return evidence
