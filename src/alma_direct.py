"""Read-only iLO disk choices, verified again inside Anaconda before erasure."""
import hashlib
import json
from .storage import inventory


def direct_inventory(discovery, live=None):
    result = inventory(discovery, live)
    result['server_serial'] = discovery['system'].get('SerialNumber')
    if live:
        return result
    # Physical members of a RAID are never offered as standalone install disks.
    for disk in result['installable']:
        disk['identity'] = {'wwn': disk['wwn'], 'size': disk['size']}
    for disk in result['physical']:
        if disk['bus'].lower() != 'nvme' or not disk.get('serial'):
            continue
        key = hashlib.sha256(disk['serial'].encode()).hexdigest()[:24]
        result['installable'].append(dict(disk, path='/dev/disk/by-id/ilo-serial-' + key,
            device='resolved-by-installer', members=[], raid=None,
            identity={'serial': disk['serial'], 'size': disk['size']}))
    return result


def storage_preflight(plan, inv, commands):
    known = {d['path']: d for d in inv['installable']}
    expected = []
    for disk in plan['disks']:
        item = known[disk['path']]
        identity = item.get('identity') or {'path': item['path'], 'size': item['size']}
        expected.append(dict(identity, token=disk['path'].removeprefix('/dev/')))
    payload = json.dumps({'expected': expected, 'commands': commands,
                          'server_serial': inv.get('server_serial')})
    return ['%pre --interpreter=/usr/bin/python3 --erroronfail --log=/tmp/provision-disk-check.log',
            'import json, os, re, subprocess',
            'cfg = json.loads(' + repr(payload) + ')',
            "if cfg['server_serial']:",
            "    actual = open('/sys/class/dmi/id/product_serial').read().strip()",
            "    assert actual == cfg['server_serial'], 'Target server identity changed'",
            "rows = json.loads(subprocess.check_output(['lsblk', '-J', '-b', '-d', '-o', 'NAME,TYPE,SERIAL,WWN,SIZE']))['blockdevices']",
            "rows = [d for d in rows if d['type'] == 'disk']",
            "norm = lambda s: (s or '').lower().removeprefix('0x')",
            'resolved = {}',
            "for item in cfg['expected']:",
            '    matches = []',
            '    for disk in rows:',
            "        device = '/dev/' + disk['name']",
            "        match = (item.get('wwn') and norm(disk.get('wwn')) == norm(item['wwn'])) or (item.get('serial') and (disk.get('serial') or '').strip() == item['serial']) or (item.get('path') and os.path.exists(item['path']) and os.path.realpath(item['path']) == device)",
            "        if match and abs(int(disk['size']) - item['size']) <= 1024**2:",
            "            matches.append(disk['name'])",
            "    assert len(matches) == 1, 'Disk identity missing or ambiguous: ' + item['token']",
            "    assert re.fullmatch(r'[A-Za-z0-9_!.-]+', matches[0]), 'Unsupported disk name'",
            "    assert matches[0] not in resolved.values(), 'Two selections resolve to the same disk'",
            "    resolved[item['token']] = matches[0]",
            "text = chr(10).join(cfg['commands']) + chr(10)",
            'for token in sorted(resolved, key=len, reverse=True):',
            '    text = text.replace(token, resolved[token])',
            "open('/tmp/provision-storage.ks', 'w').write(text)",
            '%end', '%include /tmp/provision-storage.ks']
