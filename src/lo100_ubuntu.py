"""Ubuntu media preparation shared by the LO100 panel and optional CLI."""
import json
import re
from pathlib import Path
import yaml


def prepare_ubuntu(source, destination, hostname, password, *, storage_plan=None, live_inventory=None, network_settings=None, system_guid=None):
    from src.credentials import password_hash
    from src.iso import build
    import pycdlib
    if not re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?', hostname):
        raise ValueError('Invalid hostname')
    destination = Path(destination)
    if destination.exists():
        raise ValueError('Use a new output directory; existing credentials and ISO are never replaced')
    source = Path(source).resolve()
    iso = pycdlib.PyCdlib(); iso.open(str(source))
    try:
        catalog = iso.eltorito_boot_catalog
        if not catalog or catalog.validation_entry.platform_id != 0 or catalog.initial_entry.boot_indicator != 0x88:
            raise ValueError('Source ISO lacks a bootable Legacy BIOS entry')
        volume = iso.pvd.volume_identifier.decode(errors='replace')
        if 'Ubuntu-Server 24.04' not in volume:
            raise ValueError('This legacy preparation path currently supports the inspected Ubuntu Server 24.04 media only')
    finally:
        iso.close()
    hashed = password_hash(password)
    seed = {'autoinstall': {'version': 1, 'refresh-installer': {'update': False},
            'interactive-sections': ['storage', 'network'],
            'identity': {'hostname': hostname, 'username': 'provision', 'password': hashed},
            'ssh': {'install-server': True, 'allow-pw': True},
            'apt': {'geoip': False, 'fallback': 'offline-install'},
            'shutdown': 'reboot'}}
    extra = None
    if storage_plan is not None:
        from .storage import compile_plan
        if not live_inventory or not system_guid:
            raise ValueError('Verified disk inventory and server GUID are required for automatic storage')
        expected_mode = 'uefi' if live_inventory.get('uefi') else 'bios'
        if storage_plan.get('boot_mode') != expected_mode:
            raise ValueError('Partition boot mode differs from the verified OS inventory')
        config = compile_plan(storage_plan, live_inventory)
        seed['autoinstall'].pop('interactive-sections', None)
        seed['autoinstall']['storage'] = {'config': config}
        if network_settings and not network_settings.get('deferred'):
            from .network import netplan
            seed['autoinstall']['network'] = netplan(network_settings)
        else:
            seed['autoinstall']['network'] = {'version': 2, 'ethernets': {}}
        known = {disk['path']: disk for disk in live_inventory['installable']}
        selected = [{'path': disk['path'], 'size': known[disk['path']]['size']} for disk in storage_plan['disks']]
        guard_data = json.dumps({'guid': system_guid, 'uefi': expected_mode == 'uefi', 'disks': selected})
        guard = """import json, os, uuid
cfg = json.loads(GUARD_PAYLOAD)
actual = open('/sys/class/dmi/id/product_uuid').read().strip()
assert str(uuid.UUID(actual)) == str(uuid.UUID(cfg['guid'])), 'Wrong physical server'
assert os.path.isdir('/sys/firmware/efi') == cfg['uefi'], 'Boot mode differs from reviewed plan'
for disk in cfg['disks']:
    path = disk['path']
    assert path.startswith('/dev/disk/by-id/') and os.path.exists(path), 'Reviewed disk is missing'
    name = os.path.basename(os.path.realpath(path))
    assert not os.path.exists('/sys/class/block/' + name + '/partition'), 'Reviewed disk resolved to a partition'
    size = int(open('/sys/class/block/' + name + '/size').read()) * 512
    assert abs(size - disk['size']) <= 1048576, 'Reviewed disk capacity changed'
""".replace('GUARD_PAYLOAD', repr(guard_data))
        extra = {'lo100-verify.py': guard}
        seed['autoinstall']['early-commands'] = ['python3 /cdrom/lo100-verify.py']
        # No verified one-time boot API is available on this LO100 path. Avoid
        # booting the still-attached destructive installer again after completion.
        seed['autoinstall']['shutdown'] = 'poweroff'

    destination.mkdir(parents=True)
    credentials = destination / 'login-credentials.json'
    credentials.write_text(json.dumps({'username': 'provision', 'password': password,
        'hostname': hostname, 'sudo': True, 'root_login': 'not configured'}, indent=2))
    credentials.chmod(0o600)
    config = '#cloud-config\n' + yaml.safe_dump(seed, sort_keys=False)
    (destination / 'user-data').write_text(config)
    output = destination / 'ubuntu-lo100-assisted.iso'
    build(source, output, config, extra=extra, graphical_console_only=True)
    check = pycdlib.PyCdlib(); check.open(str(output))
    try:
        catalog = check.eltorito_boot_catalog
        if catalog.validation_entry.platform_id != 0 or catalog.initial_entry.boot_indicator != 0x88:
            raise RuntimeError('Remastered ISO lost the BIOS boot entry')
    finally:
        check.close()
    return output

