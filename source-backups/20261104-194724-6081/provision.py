"""Portable test-server Ubuntu provisioner."""
import argparse
import getpass
import json
import os
import secrets
import time
from pathlib import Path
from datetime import datetime
import yaml
from src.ilo import Ilo


def save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding='utf-8')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', nargs='?', default='interactive', choices=['interactive', 'discover', 'reset', 'mount-test', 'live-probe', 'plan', 'generate', 'install'])
    parser.add_argument('--config', default='config/test.yaml')
    parser.add_argument('--reset-type', default='ForceRestart')
    parser.add_argument('--accept-plan', help='Explicit operator confirmation; must be INSTALL')
    parser.add_argument('--prepare-only', action='store_true', help='Build the installation ISO for review without booting or erasing')
    parser.add_argument('--serial-log', type=Path, help='Optional iLO VSP capture for discovery when OS networking is unavailable')
    args = parser.parse_args(argv)
    config = yaml.safe_load(Path(args.config).read_text())
    if args.command == 'interactive':
        print('=== Bare Metal Ubuntu Provisioner ===')
        for key in ('address', 'username'):
            config['ilo'][key] = input(f"iLO {key} [{config['ilo'][key]}]: ").strip() or config['ilo'][key]
        os.environ['ILO_PASSWORD'] = os.environ.get('ILO_PASSWORD') or getpass.getpass('iLO password: ')
        for key in ('address', 'gateway', 'hostname', 'domain'):
            config['network'][key] = input(f"OS {key} [{config['network'].get(key, '')}]: ").strip() or config['network'].get(key, '')
        dns = input(f"DNS, comma separated [{','.join(config['network']['dns'])}]: ").strip()
        if dns:
            config['network']['dns'] = [s.strip() for s in dns.split(',')]
        config['media']['host'] = input(f"Media host address reachable FROM iLO [{config['media']['host']}]: ").strip() or config['media']['host']
        Path('generated').mkdir(exist_ok=True)
        Path('generated/operator-config.yaml').write_text(yaml.safe_dump(config, sort_keys=False))
        cfg = ['--config', 'generated/operator-config.yaml']
        if args.serial_log:
            cfg += ['--serial-log', str(args.serial_log)]
        if main(['live-probe'] + cfg):
            return 1
        live = json.loads(Path('generated/live-inventory.json').read_text())
        if not live.get('selected_nic') and not config['network'].get('mac'):
            candidates = live.get('candidates', [])
            if config.get('installation', {}).get('offline') and not candidates:
                candidates = live['nics']
            if not candidates:
                print('[FAIL] No NIC reached the configured gateway; inspect live-inventory.json')
                return 1
            for number, nic in enumerate(candidates, 1):
                print(number, nic['name'], nic['mac'])
            choice = int(input('Select installation NIC number: '))
            if choice < 1 or choice > len(candidates):
                raise ValueError('Invalid NIC selection')
            config['network']['mac'] = candidates[choice - 1]['mac']
            Path('generated/operator-config.yaml').write_text(yaml.safe_dump(config, sort_keys=False))
        if main(['plan'] + cfg):
            return 1
        input('Edit generated/partition-plan.yaml if desired, then press Enter to reload: ')
        return main(['install', '--config', 'generated/effective-config.yaml'])
    run = Path('logs') / datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
    run.mkdir(parents=True, exist_ok=True)
    try:
        if args.command in ('plan', 'generate'):
            from src.installer import prepare, artifacts
            discovery = json.loads(Path('generated/discovery.json').read_text())
            inv, plan = prepare(config, discovery)
            if args.command == 'generate':
                artifacts(config, inv, plan, run, secrets.token_hex(16), secrets.token_urlsafe(24))
                print('[OK] generated/autoinstall.yaml')
            save(run / 'final-result.json', {'status': 'prepared', 'installation': 'not-started'})
            return 0
        ilo = Ilo(config['ilo'], os.environ.get('ILO_PASSWORD') or getpass.getpass('iLO password: '), run / 'ilo.log')
        inventory = ilo.discover()
        save(run / 'discovery.json', inventory)
        save(Path('generated/discovery.json'), inventory)
        result = {'status': 'discovery-complete', 'installation': 'not-started'}
        if args.command == 'install':
            from src.installer import prepare, install
            inv, plan = prepare(config, inventory)
            result = install(ilo, inventory, config, inv, plan, run, confirmed=args.accept_plan == 'INSTALL', prepare_only=args.prepare_only)
        if args.command == 'reset':
            print(ilo.reset(args.reset_type))
        if args.command == 'mount-test':
            from src.virtual_media import MediaServer, mount, eject
            media = config['media']
            server = MediaServer(Path(media['source_iso']), media['host'], media['port'], secrets.token_urlsafe(18), run / 'installer.log')
            server.start()
            try:
                path, state = mount(ilo, inventory, server.url)
                save(run / 'virtual-media.json', state)
                print('[OK] HTTP requests from iLO:', server.downloads)
                eject(ilo, path)
                print('[OK] Mount test finished; original ISO ejected')
                result = {'status': 'media-mount-verified', 'installation': 'not-started'}
            finally:
                server.close()
        if args.command == 'live-probe':
            from src.progress import discovery_boot_failure, serial_console_argument
            from src.live_report import from_serial
            from src.iso import build
            from src.virtual_media import MediaServer, mount, eject, boot_once
            token = secrets.token_urlsafe(18)
            media = config['media']
            callback = f"http://{media['host']}:{media['port']}/{token}/event"
            probe_config = dict(config['network'], callback=callback, probe_id=token,
                                server_serial=inventory['system'].get('SerialNumber'))
            seed = '#cloud-config\n' + yaml.safe_dump({'autoinstall': {'version': 1,
                'early-commands': ['python3 /cdrom/probe.py'],
                'identity': {'hostname': 'live-probe', 'username': 'provision', 'password': '!'},
                'shutdown': 'poweroff'}}, sort_keys=False)
            iso_path = Path('generated/discovery.iso')
            build(Path(media['source_iso']), iso_path, seed, {
                'probe.py': Path('src/live_probe.py').read_text(), 'probe.json': json.dumps(probe_config),
                'nocloud/network-config': yaml.safe_dump({'version': 2, 'ethernets': {
                    'probe-ports': {'match': {'name': 'e*'}, 'dhcp4': False, 'dhcp6': False, 'optional': True}}})},
                kernel_args=serial_console_argument(args.serial_log))
            server = MediaServer(iso_path, media['host'], media['port'], token, run / 'installer.log')
            server.start()
            try:
                path, state = mount(ilo, inventory, server.url)
                save(run / 'virtual-media.json', state)
                boot_once(ilo, path, allow_post_power_cycle=True)
                boot_serial_offset = (args.serial_log.stat().st_size
                                      if args.serial_log and args.serial_log.exists() else 0)
                boot_started = time.monotonic()
                deadline = boot_started + config.get('probe_timeout', 3600)
                last = 0
                while time.monotonic() < deadline:
                    report = json.loads(server.events[-1]) if server.events else from_serial(args.serial_log, token)
                    if report:
                        result = report
                        save(run / 'live-inventory.json', result)
                        save(Path('generated/live-inventory.json'), result)
                        print('[OK] Live inventory received. Selected NIC:', result['selected_nic'])
                        if not result.get('candidates'):
                            print('[NETWORK] No NIC reached the gateway. Physical port states:', result['nics'])
                        for disk in result['lsblk']['blockdevices']:
                            if disk['type'] == 'disk':
                                print('[DISK]', disk['name'], disk['size'], disk['model'], disk['serial'], disk.get('wwn'))
                        break
                    failure = discovery_boot_failure(args.serial_log, time.monotonic() - boot_started,
                                                     server.iso_bytes_served,
                                                     config.get('probe_no_read_timeout', 900),
                                                     offset=boot_serial_offset)
                    if failure:
                        raise RuntimeError(failure)
                    if time.monotonic() - last > 30:
                        from src.progress import report as report_progress
                        report_progress(ilo, server.downloads, server.iso_bytes_served)
                        last = time.monotonic()
                    time.sleep(3)
                else:
                    raise RuntimeError('Live discovery timed out; inspect installer.log and iLO console')
                # Keep the live system powered on. The subsequent install can
                # replace the diagnostic ISO and use a warm restart.
                try:
                    eject(ilo, path)
                except Exception as exc:
                    print('[WARN] Live inventory is saved, but virtual-media ejection needs attention:', exc)
                result = {'status': 'live-discovery-complete', 'installation': 'not-started'}
            finally:
                server.close()
        save(run / 'final-result.json', result)
        print('[RESULT]', json.dumps(result, ensure_ascii=True), flush=True)
        print('Inventory:', run / 'discovery.json')
    except Exception as exc:
        save(run / 'final-result.json', {'status': 'failed', 'error': str(exc), 'installation': 'not-verified'})
        print('[FAIL]', exc)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
