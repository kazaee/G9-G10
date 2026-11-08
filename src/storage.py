"""Inventory, editable plans and explicit curtin storage actions."""
import re
from .ilo import link


def inventory(redfish, live=None):
    resources = redfish['resources']
    physical, logical = [], []
    for path, data in resources.items():
        kind = data.get('@odata.type', data.get('Type', ''))
        if 'Collection' in kind:
            continue
        if 'DiskDrive.' in kind or kind.startswith('#Drive.'):
            physical.append({'resource': path, 'serial': data.get('SerialNumber'),
                'model': data.get('Model'), 'bus': data.get('InterfaceType', data.get('Protocol', 'unknown')),
                'media': data.get('MediaType', 'unknown'), 'size': data.get('CapacityBytes', data.get('CapacityMiB', 0) * 1024**2),
                'location': data.get('Location'), 'wwn': data.get('WWN')})
        if 'LogicalDrive.' in kind:
            members = resources.get(link(data.get('links', {}).get('DataDrives')), {}).get('Members', [])
            logical.append({'resource': path, 'wwn': data['VolumeUniqueIdentifier'].lower(),
                'size': data['CapacityMiB'] * 1024**2, 'raid': data.get('Raid'),
                'members': [link(m) for m in members], 'model': 'RAID logical disk ' + data['Id']})
    disks = []
    if live:
        for disk in live['lsblk']['blockdevices']:
            if disk['type'] != 'disk':
                continue
            wwn = (disk.get('wwn') or '').lower().removeprefix('0x')
            raid = next((d for d in logical if d['wwn'] == wwn), None)
            paths = [name for name, value in live['by_id'].items() if value == '/dev/' + disk['name'] and '-part' not in name]
            paths.sort(key=lambda p: (not p.startswith('wwn-'), p))
            if not paths:
                continue
            members = [p for p in physical if raid and p['resource'] in raid['members']]
            disks.append({'path': '/dev/disk/by-id/' + paths[0], 'device': '/dev/' + disk['name'],
                'serial': disk.get('serial'), 'wwn': disk.get('wwn'), 'size': disk['size'],
                'model': disk['model'], 'bus': disk.get('tran') or ('RAID' if raid else 'unknown'),
                'media': 'SSD' if (members and all(p['media'] == 'SSD' for p in members)) or disk.get('rota') is False else 'HDD',
                'raid': raid, 'members': members})
    else:
        for disk in logical:
            members = [p for p in physical if p['resource'] in disk['members']]
            disks.append(dict(disk, path='/dev/disk/by-id/wwn-0x' + disk['wwn'], device='not-yet-verified-in-Linux',
                bus='RAID', media='SSD' if members and all(p['media'] == 'SSD' for p in members) else 'HDD', members=members))
    return {'physical': physical, 'logical': logical, 'installable': disks, 'linux_verified': bool(live)}


def recommend(inv, uefi=True):
    choices = sorted(inv['installable'], key=lambda d: (d['bus'].lower() == 'nvme', d['media'] != 'SSD', d['size']))
    if not choices:
        raise ValueError('No stable installation disk identifiers found; run live-probe')
    chosen = choices[0]
    capacity = chosen['size'] // 1024**3
    if capacity < 32:
        raise ValueError('Installation disk must be at least 32 GiB')
    parts = ([{'name': 'efi', 'size': '1G', 'filesystem': 'fat32', 'mount': '/boot/efi', 'flag': 'boot'}]
        if uefi else [{'name': 'bios', 'size': '1M', 'flag': 'bios_grub'}])
    swap_gib = 16 if capacity >= 1024 else 8
    parts += [{'name': 'boot', 'size': '2G', 'filesystem': 'ext4', 'mount': '/boot'},
              {'name': 'swap', 'size': f'{swap_gib}G', 'filesystem': 'swap'},
              {'name': 'root', 'size': -1, 'filesystem': 'ext4', 'mount': '/'}]
    return {'version': 1, 'boot_mode': 'uefi' if uefi else 'bios', 'boot_disk': chosen['path'],
        'reason': 'Prefer non-NVMe SSD/RAID storage for this server; edit disk paths to override after checking firmware boot support.',
        'disks': [{'path': chosen['path'], 'ptable': 'gpt', 'partitions': parts}],
        'lvm': [],
        'untouched': [d['path'] for d in choices[1:]]}


def size_bytes(value):
    if value == -1:
        return -1
    if isinstance(value, int) and value > 0:
        return value
    match = re.fullmatch(r'([1-9][0-9]*)([KMGT])', str(value))
    if not match:
        raise ValueError(f'Invalid size: {value}; use e.g. 80G or -1 for final partition')
    return int(match[1]) * 1024 ** ('KMGT'.index(match[2]) + 1)


def compile_plan(plan, inv):
    if plan.get('software_raid'):
        return compile_nvme_raid(plan, inv)
    known = {d['path']: d for d in inv['installable']}
    selected = [d['path'] for d in plan['disks']]
    if len(selected) != len(set(selected)) or plan['boot_disk'] not in selected:
        raise ValueError('Duplicate disk or boot disk missing from plan')
    if set(selected) & set(plan.get('untouched', [])):
        raise ValueError('A disk is listed as both selected and untouched')
    mode = plan['boot_mode']
    if mode not in ('uefi', 'bios'):
        raise ValueError('boot_mode must be uefi or bios')
    config, vgs, vg_sizes, mounts, used_names = [], {}, {}, set(), set()

    def filesystem(item, volume):
        fs = item.get('filesystem')
        if not fs:
            return
        if fs not in ('ext4', 'xfs', 'fat32', 'swap'):
            raise ValueError('Unsupported filesystem: ' + fs)
        fmt = volume + '-format'
        config.append({'id': fmt, 'type': 'format', 'volume': volume, 'fstype': fs, 'preserve': False})
        mount = item.get('mount')
        if fs == 'swap':
            config.append({'id': volume + '-mount', 'type': 'mount', 'device': fmt, 'path': ''})
        elif mount:
            if not valid_mount(mount) or mount in mounts:
                raise ValueError('Invalid or duplicate mount point: ' + mount)
            mounts.add(mount)
            config.append({'id': volume + '-mount', 'type': 'mount', 'device': fmt, 'path': mount})

    boot_ok = False
    for number, disk in enumerate(plan['disks']):
        path = disk['path']
        if path not in known or not path.startswith('/dev/disk/by-id/'):
            raise ValueError('Disk must match a discovered stable by-id path: ' + path)
        if disk.get('ptable') != 'gpt':
            raise ValueError('This MVP supports GPT only')
        boot = path == plan['boot_disk']
        disk_id = f'disk-{number}'
        config.append({'id': disk_id, 'type': 'disk', 'path': path, 'ptable': 'gpt',
            'wipe': 'superblock-recursive', 'preserve': False, 'grub_device': boot and mode == 'bios'})
        remaining = known[path]['size'] - 8 * 1024**2
        for idx, part in enumerate(disk['partitions'], 1):
            name = part['name']
            if not re.fullmatch(r'[a-zA-Z][a-zA-Z0-9_-]*', name) or (disk_id, name) in used_names:
                raise ValueError('Invalid or duplicate partition name')
            used_names.add((disk_id, name))
            size = size_bytes(part['size'])
            if size == -1 and idx != len(disk['partitions']):
                raise ValueError('Only the final partition can consume remaining space')
            actual = remaining if size == -1 else size
            remaining -= actual
            if actual <= 0 or remaining < 0:
                raise ValueError('Partitions exceed disk capacity')
            pid = disk_id + '-' + name
            item = {'id': pid, 'type': 'partition', 'device': disk_id, 'number': idx,
                'size': size, 'preserve': False, 'wipe': 'superblock'}
            if part.get('flag'):
                if part['flag'] not in ('boot', 'bios_grub', 'linux'):
                    raise ValueError('Invalid partition flag')
                item['flag'] = part['flag']
            if boot and mode == 'uefi' and part.get('mount') == '/boot/efi':
                if part.get('filesystem') != 'fat32' or part.get('flag') != 'boot':
                    raise ValueError('EFI partition must use FAT32 and boot flag')
                item['grub_device'] = True
                boot_ok = True
            if boot and mode == 'bios' and part.get('flag') == 'bios_grub':
                boot_ok = True
            config.append(item)
            if 'vg' in part:
                if 'filesystem' in part or 'mount' in part:
                    raise ValueError('An LVM PV cannot also hold a filesystem directly')
                vg = part['vg']
                if not re.fullmatch(r'[a-zA-Z][a-zA-Z0-9_-]*', vg):
                    raise ValueError('Invalid VG name')
                vgs.setdefault(vg, []).append(pid)
                vg_sizes[vg] = vg_sizes.get(vg, 0) + actual - 8 * 1024**2
            filesystem(part, pid)
    for vg, devices in vgs.items():
        config.append({'id': 'vg-' + vg, 'type': 'lvm_volgroup', 'name': vg, 'devices': devices, 'preserve': False})
    lv_names = set()
    lv_entries = plan.get('lvm', [])
    for idx, lv in enumerate(lv_entries):
        vg = lv['vg']
        size = size_bytes(lv['size'])
        if vg not in vgs or size == 0 or size < -1:
            raise ValueError('LVM volumes require a defined VG and valid size')
        if size == -1:
            if any(later['vg'] == vg for later in lv_entries[idx + 1:]) or vg_sizes[vg] <= 0:
                raise ValueError('Only the final LV in a VG can consume remaining space')
            vg_sizes[vg] = 0
        else:
            vg_sizes[vg] -= size
            if vg_sizes[vg] < 0:
                raise ValueError('Logical volumes exceed VG capacity')
        name = lv['name']
        if not re.fullmatch(r'[a-zA-Z][a-zA-Z0-9_-]*', name) or (vg, name) in lv_names:
            raise ValueError('Invalid or duplicate LV name')
        lv_names.add((vg, name))
        lid = 'lv-' + vg + '-' + name
        action = {'id': lid, 'type': 'lvm_partition', 'name': name, 'volgroup': 'vg-' + vg, 'preserve': False}
        if size != -1:
            action['size'] = size
        config.append(action)
        filesystem(lv, lid)
    if '/' not in mounts or not boot_ok:
        raise ValueError('Plan needs root filesystem and a boot-mode-compatible boot partition')
    # Curtin requires parent mounts before nested mounts regardless of YAML edit order.
    actions = [a for a in config if a['type'] != 'mount']
    actions += sorted((a for a in config if a['type'] == 'mount'), key=lambda a: a['path'])
    return actions


def valid_mount(path):
    return (isinstance(path, str) and path.startswith('/') and path != '/boot/efi/'
        and '//' not in path and all(p not in ('.', '..') for p in path.split('/'))
        and (path == '/' or not path.endswith('/')) and not any(c.isspace() for c in path))


def compile_nvme_raid(plan, inv):
    """Two-NVMe RAID1: ESP on each disk, mirrored /boot and LVM root."""
    raid = plan['software_raid']
    paths = raid.get('members', [])
    known = {d['path']: d for d in inv['installable']}
    selected = [d['path'] for d in plan['disks']]
    level = raid.get('level')
    if level not in (0, 1) or len(paths) != 2 or len(set(paths)) != 2:
        raise ValueError('Software RAID requires exactly two distinct NVMe members at RAID0 or RAID1')
    if set(paths) != set(selected) or plan.get('boot_disk') not in paths:
        raise ValueError('RAID members must exactly match reviewed installation disks')
    if plan.get('boot_mode') != 'uefi':
        raise ValueError('NVMe RAID1 boot currently requires UEFI')
    if set(paths) & set(plan.get('untouched', [])):
        raise ValueError('A RAID member is also marked untouched')
    disks = [known.get(path) for path in paths]
    if any(d is None or not path.startswith('/dev/disk/by-id/') or
           not (d.get('bus', '').lower() == 'nvme' or '/nvme-' in path) or
           d['size'] < 32 * 1024**3 for path, d in zip(paths, disks)):
        raise ValueError('RAID1 members must be live-verified NVMe disks of at least 32 GiB')
    actions, remaining, efi = [], [], []
    for index, (path, disk) in enumerate(zip(paths, disks)):
        did = f'disk-{index}'
        actions.append({'id': did, 'type': 'disk', 'path': path, 'ptable': 'gpt',
                        'wipe': 'superblock-recursive', 'preserve': False})
        actions.append({'id': did + '-efi', 'type': 'partition', 'device': did,
                        'number': 1, 'size': 1024**3, 'flag': 'boot',
                        'grub_device': True, 'wipe': 'superblock', 'preserve': False})
        actions.append({'id': did + '-efi-format', 'type': 'format',
                        'volume': did + '-efi', 'fstype': 'fat32', 'preserve': False})
        efi.append(did + '-efi-format')
        actions.append({'id': did + '-boot', 'type': 'partition', 'device': did,
                        'number': 2, 'size': 2 * 1024**3, 'wipe': 'superblock', 'preserve': False})
        actions.append({'id': did + '-data', 'type': 'partition', 'device': did,
                        'number': 3, 'size': -1, 'wipe': 'superblock', 'preserve': False})
        remaining.append(disk['size'] - 3 * 1024**3 - 32 * 1024**2)
    actions.append({'id': 'md-boot', 'type': 'raid', 'name': 'mdboot', 'raidlevel': 1,
                    'devices': ['disk-0-boot', 'disk-1-boot'], 'metadata': '1.0', 'preserve': False})
    actions.append({'id': 'md-root', 'type': 'raid', 'name': 'mdroot', 'raidlevel': level,
                    'devices': ['disk-0-data', 'disk-1-data'], 'metadata': '1.2', 'preserve': False})
    actions.append({'id': 'md-boot-format', 'type': 'format', 'volume': 'md-boot',
                    'fstype': 'ext4', 'preserve': False})
    actions.append({'id': 'vg-vg0', 'type': 'lvm_volgroup', 'name': 'vg0',
                    'devices': ['md-root'], 'preserve': False})
    space = (min(remaining) if level == 1 else sum(remaining)) - 64 * 1024**2
    mounts = {'/boot', '/boot/efi'}
    volumes = plan.get('lvm', [])
    if not volumes or not any(v.get('mount') == '/' for v in volumes):
        raise ValueError('RAID1 plan requires a root logical volume')
    names = set()
    for index, volume in enumerate(volumes):
        if volume.get('vg') != 'vg0':
            raise ValueError('RAID1 logical volumes must use vg0')
        name = volume.get('name', '')
        if not re.fullmatch(r'[a-zA-Z][a-zA-Z0-9_-]*', name) or name in names:
            raise ValueError('Invalid or duplicate RAID1 logical volume name')
        names.add(name)
        size = size_bytes(volume['size'])
        if size == -1:
            if index != len(volumes) - 1 or space <= 0:
                raise ValueError('Only the final RAID1 logical volume can consume remaining space')
            space = 0
        else:
            space -= size
            if space < 0:
                raise ValueError('RAID1 logical volumes exceed member capacity')
        lid = 'lv-vg0-' + name
        item = {'id': lid, 'type': 'lvm_partition', 'name': name,
                'volgroup': 'vg-vg0', 'preserve': False}
        if size != -1:
            item['size'] = size
        actions.append(item)
        fs = volume.get('filesystem')
        if fs not in ('ext4', 'xfs', 'swap'):
            raise ValueError('Unsupported RAID1 logical volume filesystem')
        actions.append({'id': lid + '-format', 'type': 'format', 'volume': lid,
                        'fstype': fs, 'preserve': False})
        mount = volume.get('mount')
        if fs == 'swap':
            actions.append({'id': lid + '-mount', 'type': 'mount',
                            'device': lid + '-format', 'path': ''})
        elif mount:
            if not valid_mount(mount) or mount in mounts:
                raise ValueError('Invalid or duplicate RAID1 mount point')
            mounts.add(mount)
            actions.append({'id': lid + '-mount', 'type': 'mount',
                            'device': lid + '-format', 'path': mount})
    actions.append({'id': 'md-boot-mount', 'type': 'mount',
                    'device': 'md-boot-format', 'path': '/boot'})
    actions.append({'id': 'disk-0-efi-mount', 'type': 'mount',
                    'device': efi[0], 'path': '/boot/efi'})
    mounts_actions = sorted((a for a in actions if a['type'] == 'mount'), key=lambda a: a['path'])
    return [a for a in actions if a['type'] != 'mount'] + mounts_actions
