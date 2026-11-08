import json
import secrets
import shutil
import subprocess
import time
from pathlib import Path
import yaml
from .storage import inventory, recommend, compile_plan
from .autoinstall import generate
from .iso import build
from .virtual_media import MediaServer, mount, boot_once, eject
from .verifier import verify
from .network import netplan, ram_iso_arguments
from .live_report import from_serial
from .credentials import ensure_login
from .install_diagnostics import analyze, kernel_crashes


def prepare(config, discovery, generated=Path('generated'), overwrite_plan=False):
    generated.mkdir(exist_ok=True)
    live_path = generated / 'live-inventory.json'
    live = json.loads(live_path.read_text()) if live_path.exists() else None
    if live and live.get('server_serial') and live['server_serial'] != discovery['system'].get('SerialNumber'):
        raise ValueError('Live inventory belongs to a different server; repeat discovery for this target')
    inv = inventory(discovery, live)
    (generated / 'storage-inventory.json').write_text(json.dumps(inv, indent=2))
    if live and not config['network'].get('mac') and live.get('selected_nic'):
        config['network']['mac'] = live['selected_nic']['mac']
    if live and not config['network'].get('mac') and not live.get('selected_nic'):
        if config.get('installation', {}).get('offline') and not any(n.get('carrier') for n in live['nics']):
            config['network']['deferred'] = True
            config['network']['allowed_macs'] = [n['mac'].lower() for n in live['nics']]
            print('[NETWORK] No connected port: install offline; retry automatic port detection on the installed system.')
        else:
            raise ValueError('Multiple or no verified NICs; select a NIC from the live inventory')
    if config['network'].get('mac'):
        config['network'].pop('deferred', None)
        config['network'].pop('allowed_macs', None)
    plan_path = generated / 'partition-plan.yaml'
    if overwrite_plan or not plan_path.exists():
        plan_path.write_text(yaml.safe_dump(recommend(inv, live['uefi'] if live else True), sort_keys=False))
    plan = yaml.safe_load(plan_path.read_text())
    compile_plan(plan, inv)
    ensure_login(config, generated)
    key_path = generated / 'provision.key'
    if not config['identity'].get('ssh_public_key'):
        if not key_path.exists():
            subprocess.run(['ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-f', str(key_path), '-C', 'ilo-test-provisioner'], check=True)
        config['identity']['ssh_public_key'] = Path(str(key_path) + '.pub').read_text().strip()
    config['identity'].setdefault('ssh_private_key', str(key_path))
    (generated / 'effective-config.yaml').write_text(yaml.safe_dump(config, sort_keys=False))
    print('Physical drives:', len(inv['physical']), '| Installation candidates:', len(inv['installable']), '| Linux verified:', inv['linux_verified'])
    print(plan_path.read_text())
    return inv, plan


def artifacts(config, inv, plan, run, run_id, token):
    media = config['media']
    callback = f"http://{media['host']}:{media['port']}/{token}/event"
    seed = generate(config, plan, inv, run_id, callback)
    generated = Path('generated')
    (generated / 'autoinstall.yaml').write_text(seed)
    (generated / 'meta-data').write_text('instance-id: ' + run_id + '\n')
    manifest = {'run_id': run_id, 'network': config['network'], 'username': config['identity']['username'],
        'root_password_hash': config['identity']['password_hash'],
        'ssh_public_key': config['identity']['ssh_public_key'],
        'ssh_port': config['identity'].get('ssh_port', 22), 'boot_mode': plan['boot_mode'],
        'disks': [d for d in inv['installable'] if d['path'] in [p['path'] for p in plan['disks']]],
        'callback': callback, 'offline': config.get('installation', {}).get('offline', False),
        'installation': 'prepared-not-started'}
    (generated / 'installation-manifest.json').write_text(json.dumps(manifest, indent=2))
    for filename in ('autoinstall.yaml', 'partition-plan.yaml', 'installation-manifest.json'):
        shutil.copyfile(generated / filename, run / filename)
    (run / 'network.json').write_text(json.dumps(config['network'], indent=2))
    return seed, manifest


def install(ilo, discovery, config, inv, plan, run, confirmed=False, prepare_only=False):
    if not inv['linux_verified']:
        raise ValueError('Run live-probe before destructive installation to verify disk paths and NIC')
    run_id, token = secrets.token_hex(16), secrets.token_urlsafe(24)
    offline = config.get('installation', {}).get('offline', False)
    serial_log = Path(config.get('installation', {}).get('serial_log', '.tools/serial-debug.log'))
    if offline and config['media'].get('ram_iso'):
        raise ValueError('Offline installation requires media.ram_iso=false')
    if offline and not prepare_only and not serial_log.exists():
        raise ValueError('Start iLO VSP capture before offline installation')
    seed, manifest = artifacts(config, inv, plan, run, run_id, token)
    iso_path = Path('generated/ubuntu-autoinstall.iso')
    kernel_args = ''
    if config['media'].get('ram_iso', False):
        memory = discovery['system'].get('MemorySummary', {}).get('TotalSystemMemoryGiB', 0)
        required = 4 + 2 * Path(config['media']['source_iso']).stat().st_size / 1024**3
        if memory < required:
            raise ValueError(f'RAM ISO boot needs at least {required:.1f} GiB RAM; set media.ram_iso=false')
        live = json.loads(Path('generated/live-inventory.json').read_text())
        iso_url = manifest['callback'].rsplit('/', 1)[0] + '/ubuntu.iso'
        kernel_args = ram_iso_arguments(config['network'], live, iso_url)
        print('[INFO] After iLO boot, Linux will download this same ISO into RAM over the selected NIC')
    build(Path(config['media']['source_iso']), iso_path, seed, {
        'install_hook.py': Path('src/install_hook.py').read_text(), 'install.json': json.dumps(manifest),
        'postboot_report.py': Path('src/postboot_report.py').read_text(),
        'deferred_network.py': Path('src/deferred_network.py').read_text(),
        'nocloud/network-config': yaml.safe_dump(netplan(config['network']), sort_keys=False)}, kernel_args=kernel_args)
    print('WARNING: The following disks WILL BE ERASED:')
    for selected in plan['disks']:
        disk = next(d for d in inv['installable'] if d['path'] == selected['path'])
        print(disk['path'], disk['model'], round(disk['size'] / 1024**3, 2), 'GiB')
        for member in disk.get('members', []):
            print('  RAID member:', member['model'], member['serial'], member['location'])
    print('Untouched:', ', '.join(plan.get('untouched', [])))
    if prepare_only:
        return {'status': 'prepared-awaiting-disk-confirmation', 'installation': 'not-started', 'run_id': run_id}
    if not confirmed and input('Type INSTALL to accept the displayed plan: ').strip() != 'INSTALL':
        raise RuntimeError('Installation cancelled')
    server = MediaServer(iso_path, config['media']['host'], config['media']['port'], token, run / 'installer.log')
    server.start()
    path = None
    verified = False
    try:
        path, state = mount(ilo, discovery, server.url)
        (run / 'virtual-media.json').write_text(json.dumps(state, indent=2))
        boot_once(ilo, path)
        deadline = time.monotonic() + config.get('install_timeout', 7200)
        processed = 0
        last = 0
        completion = None
        while time.monotonic() < deadline:
            if offline:
                crashes = kernel_crashes(serial_log)
                if crashes:
                    diagnostics = analyze(serial_log, run / 'installer.log')
                    (run / 'failure-diagnostics.json').write_text(json.dumps(diagnostics, indent=2))
                    print('[FAIL] Installer live kernel crashed:', crashes[0]['detail'][0], flush=True)
                    raise RuntimeError('Ubuntu installer live kernel crashed; see failure-diagnostics.json. Installation is NOT verified')
                failure = from_serial(serial_log, run_id, 'PROVISION_ZLIB_BASE64', 'run_id', 'install-failed')
                if failure:
                    (run / 'installer-failure.json').write_text(json.dumps(failure, indent=2))
                    debug = failure.get('logs', {}).get('subiquity-server-debug.log', '')
                    curtin = failure.get('logs', {}).get('curtin-install.log', '')
                    if 'read errors mapping' in curtin and 'Input/output error (5)' in curtin:
                        detail = 'Virtual ISO read I/O errors during OS extraction (rsync code 23); check iLO media delivery and retry from the saved hardware inventory'
                    else:
                        detail = next((line.strip() for line in reversed(debug.splitlines())
                            if 'AutoinstallError:' in line and 'password' not in line.lower()),
                            'see installer-failure.json for installer diagnostics')
                    raise RuntimeError('Ubuntu installer failed: ' + detail)
                completion = from_serial(serial_log, run_id, 'PROVISION_ZLIB_BASE64', 'run_id', 'install-complete')
            for raw in server.events[processed:]:
                processed += 1
                try:
                    event = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if event.get('run_id') != run_id:
                    continue
                if event.get('event') == 'install-failed':
                    raise RuntimeError('Ubuntu installer reported failure; see installer.log')
                if event.get('event') == 'install-complete':
                    completion = event
                    break
            if completion:
                break
            if time.monotonic() - last > 30:
                from .progress import report as report_progress
                report_progress(ilo, server.downloads, server.bytes_served)
                last = time.monotonic()
            time.sleep(3)
        if not completion:
            (run / 'failure-diagnostics.json').write_text(json.dumps(analyze(serial_log, run / 'installer.log'), indent=2))
            raise RuntimeError('No installation completion evidence before timeout; installation is NOT verified')
        (run / 'completion.json').write_text(json.dumps(completion, indent=2))
        if offline:
            from .offline_verify import validate
            print('[OK] Installer completed; waiting for installed-system boot evidence over iLO', flush=True)
            deadline = time.monotonic() + 1200
            while time.monotonic() < deadline:
                evidence = from_serial(serial_log, run_id, 'PROVISION_ZLIB_BASE64', 'run_id', 'installed-boot')
                if evidence:
                    evidence = validate(evidence, completion, config, plan, run_id)
                    break
                time.sleep(3)
            else:
                raise RuntimeError('No verified installed-system boot evidence over iLO')
        else:
            print('[OK] Installer completed; waiting for reboot and authenticated SSH', flush=True)
            evidence = verify(config, Path(config['identity']['ssh_private_key']), run / 'known_hosts', completion, run_id, plan)
        (run / 'verification.json').write_text(json.dumps(evidence, indent=2))
        warning = None
        try:
            eject(ilo, path)
        except Exception as exc:
            # Ejection is cleanup after the installed OS has booted and passed
            # validation. It cannot revoke the already verified installation.
            warning = 'Virtual ISO could not be ejected through iLO: ' + str(exc)
            print('[WARN]', warning, flush=True)
        result = {'status': 'OS_INSTALLED_NETWORK_PENDING' if offline else 'SUCCESS',
            'installation': 'verified', 'network_reachability': 'not-verified' if offline else 'verified',
            'run_id': run_id, 'checks': evidence['checks']}
        if warning:
            result['warning'] = warning
        verified = True
        return result
    finally:
        if path and not verified:
            try:
                eject(ilo, path)
                print('[INFO] Ejected virtual ISO after unverified installation', flush=True)
            except Exception as exc:
                print('[WARN] Could not eject virtual ISO after unverified installation:', exc, flush=True)
        server.close()
