"""Local browser UI for the tested provisioning engine. Bind to loopback only."""
import argparse
import copy
import http.server
import importlib
import json
import os
import re
import secrets
import socket
import ssl
import subprocess
import sys
import threading
import time
import webbrowser
import urllib.request
import urllib.error
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit, parse_qs, urlencode
import yaml
from src.ilo import Ilo
from src.storage import inventory, recommend, compile_plan
from src.os_catalog import source_for, available, detected_media, OTHER_MEDIA
from src.credentials import password_hash
from src.serial_capture import SerialCapture
from src.windows_verification import rdp_responds
from src.network import validate as validate_network
from src.virtual_media import MediaServer, mount, mount_device, eject, boot_once, prefer_hard_disk_on_next_boot, cd_device, floppy_device

ROOT = Path(__file__).resolve().parent
SESSION_FILE = ROOT / 'generated' / 'panel-session.json'


def running_session_url(port, session_file=SESSION_FILE):
    """Reuse only this app's authenticated local session, never a foreign port."""
    try:
        session = json.loads(session_file.read_text(encoding='utf-8'))
        if session.get('port') != port or not isinstance(session.get('token'), str):
            return None
        url = f'http://127.0.0.1:{port}/?token={session["token"]}'
        request = urllib.request.Request(f'http://127.0.0.1:{port}/api/state',
                                         headers={'X-App-Token': session['token']})
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request, timeout=2) as response:
            state = json.load(response)
        return url if isinstance(state, dict) and 'stage' in state else None
    except (OSError, ValueError, TypeError, KeyError, urllib.error.URLError):
        return None


def saved_session_token(port, session_file=SESSION_FILE):
    """Keep an existing local tab usable after restarting this panel on the same port."""
    try:
        saved = json.loads(session_file.read_text(encoding='utf-8'))
        token = saved.get('token')
        if saved.get('port') == port and isinstance(token, str) and re.fullmatch(r'[A-Za-z0-9_-]{32,128}', token):
            return token
    except (OSError, ValueError, TypeError):
        pass
    return None


class LocalServer(http.server.ThreadingHTTPServer):
    allow_reuse_address = False

    def server_bind(self):
        if hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


class Application:
    def __init__(self):
        self.token = secrets.token_urlsafe(32)
        local_config = ROOT / 'config/test.yaml'
        self.config = yaml.safe_load((local_config if local_config.exists()
                                      else ROOT / 'config/example.yaml').read_text())
        self.password = None
        self.discovery = self.live = None
        self.state = {'busy': False, 'stage': 'disconnected', 'message': '', 'log': []}
        self.lock = threading.Lock()
        self.reviewed = None
        self.media_server = None
        self.windows_floppy_path = None
        self.windows_boot_patch = None
        self.windows_media_token = None
        self.console = None
        self.console_log = None
        self.linux_password = ''
        self.prepared_credentials = None
        self.completed_credentials = None

    def completed_access(self):
        if self.state.get('stage') != 'completed' or not self.completed_credentials:
            raise ValueError('Access details are available only after this installation is verified')
        return self.completed_credentials.copy()

    def public_state(self):
        with self.lock:
            data = copy.deepcopy(self.state)
            data['network'] = self.config['network']
            data['media_host'] = self.config['media']['host']
            data['ilo_address'] = self.config['ilo']['address'] if self.discovery else ''
            data['username'] = self.config['identity']['username']
            data['ssh_port'] = self.config['identity'].get('ssh_port', 22)
            data['rdp_port'] = self.config['installation'].get('rdp_port', 33096)
            data['available_os'] = available(ROOT / 'ISO')
            data['detected_media'] = detected_media(ROOT / 'ISO')
            if self.media_server:
                data['media_activity'] = {'requests': self.media_server.downloads,
                                          'bytes': self.media_server.bytes_served,
                                          'iso_bytes': self.media_server.iso_bytes_served}
            data['os_media'] = {os_id: source_for(os_id, ROOT / 'ISO').name
                for os_id in data['available_os']}
            if self.discovery:
                system = self.discovery['system']
                data['server'] = {k: system.get(k) for k in ('Model', 'SerialNumber', 'PowerState', 'MemorySummary')}
                data['storage'] = inventory(self.discovery, self.live)
            if self.live:
                data['nics'] = self.live['nics']
                data['selected_nic'] = self.live.get('selected_nic')
                data['existing_systems'] = self.live.get('existing_systems', [])
            if self.reviewed:
                data['review'] = self.reviewed
            if data.get('plan'):
                data['plan_data'] = yaml.safe_load(data['plan'])
            return data

    def launch(self, task):
        with self.lock:
            if self.state['busy']:
                raise ValueError('An operation is already running')
            self.state.update(busy=True, message='', log=[], started_at=time.time())
        def work():
            try:
                task()
            except Exception as exc:
                self.state.update(stage='error', message=str(exc))
                with self.lock:
                    self.state['log'] = (self.state['log'] + ['[FAIL] ' + str(exc)])[-150:]
            finally:
                self.state['busy'] = False
        threading.Thread(target=work, daemon=True).start()

    def watch_installer_media(self, server, baseline=0, windows=False):
        """Distinguish a reset request from actual virtual-CD boot traffic."""
        def watch():
            deadline = time.monotonic() + 600
            while time.monotonic() < deadline:
                if self.media_server is not server or self.state.get('stage') != 'boot-requested':
                    return
                if server.bytes_served - baseline >= 1024 * 1024:
                    self.state.update(stage='awaiting-verification', message=(
                        'iLO has read at least 1 MiB from the installer ISO. Boot media is active; '
                        'installation and final OS boot are still unverified. Inspect the iLO console.'))
                    if not windows:
                        self.watch_unreported_media(server)
                        return
                    break
                time.sleep(5)
            else:
                if self.media_server is server and self.state.get('stage') == 'boot-requested':
                    self.state.update(stage='error', message=(
                        'No substantial installer-ISO read within 10 minutes after reset. '
                        'The server may have skipped virtual media; inspect iLO boot menu and do not assume installation.'))
                return
            # A full GiB of Windows media traffic means WinPE has progressed
            # well beyond firmware boot. Keep the ISO attached while DISM reads
            # install.wim, but direct its next reboot to the installed disk.
            deadline = time.monotonic() + 4 * 3600
            while time.monotonic() < deadline and self.media_server is server:
                if server.bytes_served - baseline >= 1024**3:
                    try:
                        prefer_hard_disk_on_next_boot(self.management_ilo())
                        self.state.update(message=(
                            'Windows installer is reading the ISO; next boot is set to hard disk. '
                            'Waiting for the installed Windows post-install report.'))
                    except Exception as exc:
                        self.state.update(message='Could not secure the next hard-disk boot: ' + str(exc))
                    self.watch_windows_result(server)
                    return
                time.sleep(5)
            if self.media_server is server:
                self.state.update(stage='error', message=(
                    'Windows media did not reach the safe boot-switch threshold. '
                    'Inspect iLO before rebooting; installation is not verified.'))
        threading.Thread(target=watch, daemon=True).start()

    def watch_unreported_media(self, server):
        """Do not leave ESXi or custom-ISO jobs looking active indefinitely."""
        def watch():
            deadline = time.monotonic() + 6 * 3600
            while time.monotonic() < deadline and self.media_server is server:
                time.sleep(30)
            if self.media_server is server and self.state.get('stage') == 'awaiting-verification':
                self.state.update(stage='unverified', message=(
                    'No installed-system completion evidence was received within six hours. '
                    'The installer might have succeeded or failed; inspect the graphical console.'))
        threading.Thread(target=watch, daemon=True).start()

    def watch_debian_result(self, server, baseline, run_id):
        def watch():
            deadline = time.monotonic() + 6 * 3600
            boot_deadline = time.monotonic() + 1800
            checked = 0
            while self.media_server is server:
                for raw in server.events[checked:]:
                    checked += 1
                    try:
                        report = json.loads(raw)
                    except (TypeError, ValueError):
                        continue
                    if (report.get('event') == 'DEBIAN_FIRSTBOOT' and report.get('run_id') == run_id
                            and report.get('success') is True):
                        self.finish_debian(server, run_id)
                        return
                if self.state.get('stage') == 'boot-requested' and server.iso_bytes_served - baseline >= 64 * 1024 * 1024:
                    self.state.update(stage='awaiting-verification', message='Debian boot media was read, but installer progress is unknown. Check the graphical console for prompts; waiting for a verified first-boot report.')
                if self.state.get('stage') == 'boot-requested' and time.monotonic() >= boot_deadline:
                    self.state.update(stage='error', message='Debian did not read 64 MiB from the ISO within 30 minutes. Inspect the graphical console; installation is not verified.')
                if time.monotonic() >= deadline:
                    if self.state.get('stage') in ('boot-requested', 'awaiting-verification'):
                        self.state.update(stage='unverified', message='No Debian first-boot report within six hours. Inspect the graphical console; installation is not verified.')
                    return
                time.sleep(5)
        threading.Thread(target=watch, daemon=True).start()

    def finish_debian(self, server, run_id):
        warning = ''
        try:
            ilo = self.management_ilo()
            cd_path, _ = cd_device(self.discovery)
            if ilo.get(cd_path).get('Image') == server.url:
                eject(ilo, cd_path)
        except Exception as exc:
            warning = ' Media ejection needs review: ' + str(exc)
        server.close()
        self.media_server = None
        self.completed_credentials = self.prepared_credentials
        self.state.update(stage='completed', verification={'method': 'debian-firstboot-report',
            'run_id': run_id, 'timestamp': datetime.now().isoformat()},
            message='Debian first boot reported active SSH and a network route. External SSH reachability was not tested.' + warning)

    def watch_alma_result(self, server, baseline, run_id):
        """Only a first-boot report for this run verifies the installed OS."""
        def watch():
            deadline = time.monotonic() + 6 * 3600
            boot_deadline = time.monotonic() + 1800
            checked = 0
            while self.media_server is server:
                for raw in server.events[checked:]:
                    checked += 1
                    try:
                        report = json.loads(raw)
                    except (TypeError, ValueError):
                        continue
                    if report.get('event') == 'ALMA_FIRSTBOOT' and report.get('run_id') == run_id and report.get('success') is True:
                        self.finish_alma(server, run_id)
                        return
                if self.state.get('stage') == 'boot-requested' and server.iso_bytes_served - baseline >= 64 * 1024 * 1024:
                    self.state.update(stage='awaiting-verification', message='AlmaLinux read at least 64 MiB from the ISO. Waiting for the installed OS first-boot report; installation is not yet verified.')
                if self.state.get('stage') == 'boot-requested' and time.monotonic() >= boot_deadline:
                    self.state.update(stage='error', message='AlmaLinux did not read 64 MiB from the ISO within 30 minutes. The bootloader may have stopped before loading the installer. Inspect the graphical console; installation is not verified.')
                if time.monotonic() >= deadline:
                    if self.state.get('stage') in ('boot-requested', 'awaiting-verification'):
                        self.state.update(stage='unverified', message='No AlmaLinux first-boot report within six hours. Inspect the graphical console; installation is not verified.')
                    return
                time.sleep(5)
        threading.Thread(target=watch, daemon=True).start()

    def finish_alma(self, server, run_id):
        warning = ''
        try:
            ilo = self.management_ilo()
            cd_path, _ = cd_device(self.discovery)
            if ilo.get(cd_path).get('Image') == server.url:
                eject(ilo, cd_path)
            if self.windows_floppy_path and ilo.get(self.windows_floppy_path).get('Image') == server.extra_urls.get('answer.img'):
                eject(ilo, self.windows_floppy_path)
        except Exception as exc:
            warning = ' Media ejection needs review: ' + str(exc)
        self.windows_floppy_path = None
        server.close()
        self.media_server = None
        self.completed_credentials = self.prepared_credentials
        self.state.update(stage='completed', verification={'method': 'alma-firstboot-report',
            'run_id': run_id, 'timestamp': datetime.now().isoformat()},
            message='AlmaLinux first boot reported active SSH and a route to the gateway. External SSH reachability was not tested.' + warning)

    def watch_windows_result(self, server):
        """Only a post-install report from the tokenized media server verifies setup."""
        deadline = time.monotonic() + 6 * 3600
        checked = 0
        timed_out = False
        while self.media_server is server:
            events = server.events[checked:]
            checked += len(events)
            for raw in events:
                try:
                    report = json.loads(raw)
                except (ValueError, TypeError):
                    continue
                if report.get('event') != 'WINDOWS_POSTINSTALL':
                    continue
                if report.get('success') and report.get('address') == self.config['network']['address'].split('/')[0]:
                    self.finish_windows(server, report)
                    return
                else:
                    self.state.update(stage='error', message='Windows post-install reported a failure: ' +
                        str(report.get('error', 'address mismatch')) +
                        '. Waiting for a later post-install retry; inspect the graphical console.')
            if not timed_out and time.monotonic() >= deadline:
                timed_out = True
                if self.state.get('stage') == 'awaiting-verification':
                    self.state.update(stage='unverified', message=(
                        'Windows installation completion was not reported within six hours. '
                        'This does not prove failure. Check the graphical console or verify a logged-in Windows session.'))
            time.sleep(5)

    def finish_windows(self, server, report):
        warning = ''
        try:
            ilo = self.management_ilo()
            cd_path, _ = cd_device(self.discovery)
            if ilo.get(cd_path).get('Image') == server.url:
                eject(ilo, cd_path)
            if self.windows_floppy_path and ilo.get(self.windows_floppy_path).get('Image') == server.extra_urls.get('answer.img'):
                eject(ilo, self.windows_floppy_path)
        except Exception as exc:
            warning = ' Media ejection needs review: ' + str(exc)
        self.windows_floppy_path = None
        server.close()
        self.media_server = None
        self.completed_credentials = self.prepared_credentials
        self.state.update(stage='completed', verification={
            'method': 'windows-postinstall-report', 'address': report['address'],
            'adapter_mac': report.get('adapter_mac'), 'rdp_port': report.get('rdp_port'),
            'timestamp': report.get('timestamp')},
            message='Windows post-install configured networking and RDP and reported success.' + warning)

    def verify_windows_current(self, body):
        """Recovery path for a run started before the report callback existed."""
        if body.get('confirmation') != 'VERIFIED':
            raise ValueError('Log in through the graphical console, then type VERIFIED')
        if not self.discovery:
            raise ValueError('Connect to this server iLO first')
        address = self.config['network']['address'].split('/')[0]
        def task():
            ilo = self.management_ilo()
            if ilo.system.get('PowerState') != 'On':
                raise ValueError('iLO reports that the server is not powered on')
            port = int(self.config['installation'].get('rdp_port', 33096))
            if not rdp_responds(address, port):
                raise ValueError(f'Windows RDP did not answer on the configured address and port {port}')
            self.completed_credentials = self.prepared_credentials
            self.state.update(stage='completed', verification={
                'method': 'operator-login-and-rdp', 'address': address, 'rdp_port': port,
                'timestamp': datetime.now().isoformat()},
                message=f'Operator confirmed Windows login; RDP handshake on port {port} also succeeded. '
                        'This is a manual verification of the current installation.')
        self.launch(task)

    def connect(self, body):
        settings = {'address': body['address'].strip(), 'username': body['username'].strip(),
                    'verify_tls': bool(body.get('verify_tls', False))}
        if not settings['address'] or not settings['username'] or not body['password']:
            raise ValueError('Enter iLO address, username and password')
        def task():
            self.state['stage'] = 'connecting'
            run = self.run_directory()
            old_discovery_file = Path('generated/discovery.json')
            old_live_file = Path('generated/live-inventory.json')
            old_discovery = json.loads(old_discovery_file.read_text()) if old_discovery_file.exists() else None
            old_live = json.loads(old_live_file.read_text()) if old_live_file.exists() else None
            connection = Ilo(settings, body['password'], run / 'ilo.log')
            discovery = connection.discover()
            profile = discovery.get('ilo_profile', {})
            settings['generation'] = profile.get('generation')
            self.state['ilo_profile'] = profile
            self.state['log'].append('[INFO] iLO generation: ' + str(profile.get('generation') or 'unknown')
                                     + '; firmware: ' + str(profile.get('firmware') or 'unknown'))
            self.config['ilo'], self.password = settings, body['password']
            self.discovery, self.live, self.reviewed = discovery, None, None
            self.prepared_credentials = self.completed_credentials = None
            self.config['identity'].pop('password_hash', None)
            self.config['identity']['generate_password'] = True
            self.state.pop('plan', None)
            Path('generated').mkdir(exist_ok=True)
            Path('generated/discovery.json').write_text(json.dumps(discovery, indent=2))
            self.config['network']['mac'] = ''
            self.state.update(stage='connected', message='اتصال برقرار شد. برای پیشنهاد پارتیشن و تشخیص کارت شبکه، گزینهٔ ریبوت را علامت بزنید و دکمهٔ «شناسایی سرور و پیشنهاد پارتیشن» را بزنید.')
            if self.valid_live_cache(discovery, old_discovery, old_live):
                self.live = old_live
                self.config['network']['mac'] = self.selected_mac(self.live)
                inv = inventory(discovery, self.live)
                plan = yaml.safe_dump(recommend(inv, self.live['uefi']), sort_keys=False)
                self.state.update(stage='discovered', plan=plan,
                    message='اطلاعات سخت‌افزار همین سرور از شناسایی قبلی بازیابی شد. طرح را بررسی و ISO نصب را آماده کنید؛ بوت تشخیصی دوباره لازم نیست. وضعیت لینک شبکه ممکن است تغییر کرده باشد.')
                if not any(n.get('carrier') for n in self.live['nics']):
                    self.state['message'] += ' هیچ لینک فعالی دیده نشده است؛ کارت شبکهٔ اول فقط پیشنهاد است. MAC را قبل از ساخت ISO بررسی کنید. IP، Gateway و DNS حتی بدون پینگ در netplan نوشته می‌شوند.'
        self.launch(task)

    @staticmethod
    def valid_live_cache(discovery, old_discovery, live):
        if not old_discovery or not live or not live.get('uefi'):
            return False
        serial = discovery.get('system', {}).get('SerialNumber')
        if not serial or serial != old_discovery.get('system', {}).get('SerialNumber') or serial != live.get('server_serial'):
            return False
        try:
            old = inventory(old_discovery, live)
            current = inventory(discovery, live)
            signature = lambda inv: sorted((d['wwn'], d['size']) for d in inv['installable'])
            return bool(signature(current)) and signature(current) == signature(old)
        except (KeyError, ValueError, TypeError):
            return False

    @staticmethod
    def proposed_mac(live):
        if live.get('selected_nic'):
            return live['selected_nic']['mac'].lower()
        connected = [n for n in live.get('nics', []) if n.get('carrier')]
        candidates = connected if len(connected) == 1 else live.get('nics', [])
        return candidates[0]['mac'].lower() if candidates else ''

    def selected_mac(self, live):
        choice = Path('generated/nic-selection.json')
        if choice.exists():
            saved = json.loads(choice.read_text())
            if saved.get('server_serial') == self.discovery['system'].get('SerialNumber'):
                for nic in live.get('nics', []):
                    if nic['mac'].lower() == saved.get('mac', '').lower():
                        return nic['mac'].lower()
        return self.proposed_mac(live)

    def run_directory(self):
        run = Path('logs') / ('ui-' + datetime.now().strftime('%Y-%m-%d_%H-%M-%S') + '-' + secrets.token_hex(2))
        run.mkdir(parents=True)
        self.state['log_directory'] = str(run)
        return run

    def settings(self, body):
        if not self.discovery:
            raise ValueError('Connect to iLO first')
        os_id = body.get('os', 'ubuntu24')
        # Discovery still uses the existing Linux diagnostic boot media.
        diagnostic_os = 'ubuntu24' if os_id in OTHER_MEDIA else os_id
        self.config['media']['source_iso'] = str(source_for(diagnostic_os, ROOT / 'ISO'))
        self.config['installation']['os'] = os_id
        for key in ('address', 'gateway', 'hostname', 'domain'):
            self.config['network'][key] = str(body.get(key, self.config['network'].get(key, ''))).strip()
        self.config['network']['dns'] = [d.strip() for d in body.get('dns', '4.2.2.4').split(',') if d.strip()]
        self.config['media']['host'] = body.get('media_host', self.config['media']['host']).strip()
        self.config['identity']['username'] = 'root' if os_id.startswith(('ubuntu', 'debian', 'alma')) else body.get('os_username', 'provision').strip()
        password = body.get('os_password', '')
        self.linux_password = password if os_id.startswith(('ubuntu', 'debian', 'alma')) else ''
        def access_port(name, default):
            try:
                value = int(body.get(name, default))
            except (TypeError, ValueError):
                raise ValueError(f'{name} must be a valid TCP port')
            minimum = 1024 if name == 'rdp_port' else 1
            if not minimum <= value <= 65535:
                raise ValueError(f'{name} must be between {minimum} and 65535')
            return value
        self.config['identity']['ssh_port'] = access_port('ssh_port', 22)
        self.config['installation']['rdp_port'] = access_port('rdp_port', 33096)
        if password:
            self.config['identity']['password_hash'] = password_hash(password)
            self.config['identity']['generate_password'] = False
        else:
            self.config['identity'].pop('password_hash', None)
            self.config['identity']['generate_password'] = True
        self.config['identity']['ssh_password_login'] = True
        if 'mac' in body:
            self.config['network']['mac'] = ''
        if body.get('mac'):
            if not self.live or body['mac'].lower() not in [n['mac'].lower() for n in self.live['nics']]:
                raise ValueError('Select a NIC from this server live inventory')
            self.config['network']['mac'] = body['mac'].lower()
        self.config['installation']['offline'] = bool(body.get('offline', True))
        self.config['media']['ram_iso'] = False
        validate_network(self.config['network'], require_mac=False)

    def command(self, command, run, extra=(), capture=False):
        cfg = run / 'config.yaml'
        serial = None
        try:
            if capture:
                if self.console:
                    self.console_stop({})
                serial_path = run / 'serial.log'
                serial = SerialCapture(self.config['ilo'], self.password, serial_path).start()
                self.config['installation']['serial_log'] = str(serial_path)
                if command == 'live-probe':
                    extra = list(extra) + ['--serial-log', str(serial_path)]
            cfg.write_text(yaml.safe_dump(self.config, sort_keys=False))
            env = dict(os.environ, ILO_PASSWORD=self.password)
            with (run / 'process.log').open('w') as log:
                process = subprocess.Popen([sys.executable, '-u', 'provision.py', command, '--config', str(cfg), *extra],
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env, cwd=ROOT)
                for line in process.stdout:
                    log.write(line)
                    log.flush()
                    with self.lock:
                        self.state['log'] = (self.state['log'] + [line.rstrip()])[-150:]
                if process.wait() != 0:
                    failures = [line for line in self.state['log'] if line.startswith('[FAIL]')]
                    detail = failures[-1][7:] if failures else 'Inspect the displayed log'
                    if 'password' in detail.lower():
                        detail = 'Inspect the displayed log'
                    raise RuntimeError('Operation failed: ' + detail)
        finally:
            if serial:
                serial.close()

    def probe(self, body):
        if self.live and not body.get('confirm_reboot'):
            self.settings(body)
            inv = inventory(self.discovery, self.live)
            self.state.update(stage='discovered', plan=yaml.safe_dump(recommend(inv, self.live['uefi']), sort_keys=False),
                message='از شناسایی زندهٔ معتبر همین سرور استفاده شد؛ ریبوت و بارگذاری دوبارهٔ ISO لازم نبود. برای شناسایی تازه، گزینهٔ ریبوت را علامت بزنید.')
            return
        if not body.get('confirm_reboot'):
            raise ValueError('Confirm the diagnostic reboot; disks will only be read')
        self.settings(body)
        def task():
            self.reviewed = None
            self.prepared_credentials = self.completed_credentials = None
            self.state.update(stage='discovering', message='سرور از ISO تشخیصی بوت می‌شود؛ این مرحله از طریق iLO ممکن است حدود ۲۰ تا ۳۰ دقیقه طول بکشد. پیشرفت در پایین صفحه نمایش داده می‌شود.')
            self.command('live-probe', self.run_directory(), capture=True)
            self.live = json.loads(Path('generated/live-inventory.json').read_text())
            self.discovery = json.loads(Path('generated/discovery.json').read_text())
            self.live['server_serial'] = self.discovery['system']['SerialNumber']
            Path('generated/live-inventory.json').write_text(json.dumps(self.live, indent=2))
            self.config['network']['mac'] = self.selected_mac(self.live)
            inv = inventory(self.discovery, self.live)
            plan = yaml.safe_dump(recommend(inv, self.live['uefi']), sort_keys=False)
            message = 'شناسایی تمام شد. طرح پارتیشن، شبکه و حساب مدیر را بررسی کنید؛ سپس دکمهٔ ساخت ISO را بزنید.'
            if not any(n.get('carrier') for n in self.live['nics']):
                message += ' هیچ پورتی لینک ندارد؛ در حالت آفلاین نصب ادامه می‌یابد و تشخیص کارت پس از نصب تکرار می‌شود.'
            self.state.update(stage='discovered', plan=plan, message=message)
            if not any(n.get('carrier') for n in self.live['nics']):
                self.state['message'] += ' کارت اول فقط پیشنهاد است؛ MAC را قبل از ساخت ISO بررسی کنید. فایل netplan با اطلاعات واردشده ساخته می‌شود.'
        self.launch(task)

    def prepare(self, body):
        if body.get('os') in ('debian11dvd', 'debian12netinst', 'debian13netinst'):
            return self.prepare_debian(body)
        if body.get('os') in ('alma98minimal', 'alma102minimal'):
            return self.prepare_alma(body)
        if body.get('os') == 'windows2022':
            return self.prepare_windows(body)
        if body.get('os') in ('esxi803', 'esxi801', 'esxi800', 'esxi703'):
            return self.prepare_esxi(body)
        if not self.live:
            raise ValueError('Complete live discovery for this target first')
        self.settings(body)
        plan = yaml.safe_load(body['plan'])
        inv = inventory(self.discovery, self.live)
        compile_plan(plan, inv)
        if self.config['network'].get('mac'):
            selected = next(n for n in self.live['nics'] if n['mac'].lower() == self.config['network']['mac'])
            Path('generated/nic-selection.json').write_text(json.dumps({
                'server_serial': self.discovery['system']['SerialNumber'],
                'name': selected['name'], 'mac': selected['mac'].lower(), 'source': 'installation-review'}, indent=2))
        def task():
            self.reviewed = None
            self.state['stage'] = 'preparing'
            Path('generated/partition-plan.yaml').write_text(yaml.safe_dump(plan, sort_keys=False))
            self.command('install', self.run_directory(), ['--prepare-only'])
            self.config = yaml.safe_load(Path('generated/effective-config.yaml').read_text())
            saved = json.loads(Path('generated/login-credentials.json').read_text()) if not self.linux_password else None
            secret = self.linux_password or saved['password']
            self.prepared_credentials = {'username': 'root', 'password': secret,
                'address': self.config['network']['address'].split('/')[0],
                'port': self.config['identity']['ssh_port'], 'protocol': 'SSH'}
            selected = {d['path'] for d in plan['disks']}
            self.reviewed = {'disks_to_erase': [d for d in inv['installable'] if d['path'] in selected],
                             'untouched': [d for d in inv['installable'] if d['path'] not in selected],
                             'network': self.config['network'], 'username': self.config['identity']['username'],
                             'plan': plan, 'credentials_file': 'generated/login-credentials.json' if self.config['identity'].get('generate_password') else None}
            self.state.update(stage='ready', message='ISO آماده شد. دیسک‌های مشخص‌شده را بازبینی کنید و برای شروع نصب INSTALL را وارد کنید.')
        self.launch(task)

    def prepare_debian(self, body):
        if not self.live or not self.discovery or not self.live.get('uefi'):
            raise ValueError('Debian installation requires live disk discovery and UEFI boot')
        self.settings(body)
        if not self.config['network'].get('mac'):
            raise ValueError('Select the Debian network adapter by MAC')
        # ISO preparation can be repeated in a long-lived panel. Load the current
        # Debian builder so a previous failed attempt cannot reuse cached code.
        from src import debian_media
        importlib.reload(debian_media)
        inspect_media = debian_media.inspect_media
        preseed = debian_media.preseed
        report_script = debian_media.report_script
        build = debian_media.build
        plan = yaml.safe_load(body['plan'])
        inv = inventory(self.discovery, self.live)
        os_id = body['os']
        version = {'debian11dvd': '11.0.0', 'debian12netinst': '12.0.0',
                   'debian13netinst': '13.7.0'}[os_id]
        source = ROOT / 'ISO' / OTHER_MEDIA[os_id]
        if not source.is_file():
            raise ValueError(f'Debian {version} ISO was not found')
        inspect_media(source, version)
        secret = body.get('os_password') or secrets.token_urlsafe(20)
        self.config['identity']['password_hash'] = password_hash(secret)
        self.config['identity']['generate_password'] = False
        run_id = secrets.token_hex(16)
        media_token = secrets.token_urlsafe(24)
        report_url = (f"http://{self.config['media']['host']}:{self.config['media']['port']}"
                      f"/{media_token}/event")
        answers = preseed(plan, inv, self.config['network'], self.config['identity'], run_id)
        report = report_script(run_id, report_url, self.config['identity']['ssh_port'])
        selected = {d['path'] for d in plan['disks']}
        def task():
            self.reviewed = None
            self.prepared_credentials = self.completed_credentials = None
            self.state['stage'] = 'preparing'
            run = self.run_directory()
            customized = run / f'debian-{version}-automated.iso'
            build(source, customized, answers, report)
            credentials = run / 'debian-credentials.json'
            credentials.write_text(json.dumps({'username': 'root', 'password': secret,
                'server_serial': self.discovery['system'].get('SerialNumber')}, indent=2))
            credentials.chmod(0o600)
            (run / 'debian-plan.yaml').write_text(yaml.safe_dump(plan, sort_keys=False))
            (run / 'preseed.cfg').write_text(answers)
            self.prepared_credentials = {'username': 'root', 'password': secret,
                'address': self.config['network']['address'].split('/')[0],
                'port': self.config['identity']['ssh_port'], 'protocol': 'SSH'}
            self.reviewed = {'os': os_id, 'source_iso': str(source),
                'prepared_iso': str(customized), 'plan_file': str(run / 'debian-plan.yaml'),
                'run_id': run_id, 'media_token': media_token, 'plan': plan,
                'disks_to_erase': [d for d in inv['installable'] if d['path'] in selected],
                'untouched': [d for d in inv['installable'] if d['path'] not in selected],
                'network': self.config['network'].copy(), 'username': 'root',
                'credentials_file': str(credentials)}
            self.state.update(stage='ready', message=f'Debian {version} preseed ISO is ready. Review the one disk marked for erasure, selected MAC and SSH port, then type INSTALL.')
        self.launch(task)

    def install_debian(self, body):
        review = self.reviewed
        if not review or review.get('os') != body.get('os') or body.get('os') not in ('debian11dvd', 'debian12netinst', 'debian13netinst') or body.get('confirmation') != 'INSTALL':
            raise ValueError('Prepare Debian and type INSTALL after reviewing the disk plan')
        if yaml.safe_load(Path(review['plan_file']).read_text()) != review['plan']:
            raise ValueError('Debian disk plan changed since review; prepare again')
        prepared = Path(review['prepared_iso'])
        if not prepared.is_file():
            raise ValueError('Prepared Debian ISO is missing')
        def task():
            self.state['stage'] = 'installing'
            ilo = self.management_ilo()
            cd_path, _ = cd_device(self.discovery)
            if self.media_server:
                eject(ilo, cd_path)
                if self.windows_floppy_path:
                    eject(ilo, self.windows_floppy_path)
                    self.windows_floppy_path = None
                self.media_server.close()
                self.media_server = None
            run = self.run_directory()
            media = self.config['media']
            server = MediaServer(prepared, media['host'], media['port'], review['media_token'], run / 'media.log')
            server.start()
            try:
                mount(ilo, self.discovery, server.url)
                baseline = server.iso_bytes_served
                boot_once(ilo, cd_path, require_virtual_cd=True)
                self.media_server = server
            except Exception:
                server.close()
                raise
            self.reviewed = None
            self.state.update(stage='boot-requested', message='Debian virtual-CD boot requested. Installation is not verified; inspect the graphical console.')
            self.watch_debian_result(server, baseline, review['run_id'])
        self.launch(task)

    def prepare_alma(self, body):
        if not self.live or not self.discovery or not self.live.get('uefi'):
            raise ValueError('AlmaLinux installation requires live disk discovery and UEFI boot')
        self.settings(body)
        if not self.config['network'].get('mac'):
            raise ValueError('Select the AlmaLinux network adapter by MAC')
        from src.alma_media import inspect_media, kickstart
        from src.windows_media import answer_usb_image
        plan = yaml.safe_load(body['plan'])
        inv = inventory(self.discovery, self.live)
        os_id = body['os']
        version = {'alma98minimal': '9.8', 'alma102minimal': '10.2'}[os_id]
        source = ROOT / 'ISO' / OTHER_MEDIA[os_id]
        if not source.is_file():
            raise ValueError(f'AlmaLinux {version} minimal ISO was not found')
        inspect_media(source, version)
        secret = body.get('os_password') or secrets.token_urlsafe(20)
        self.config['identity']['password_hash'] = password_hash(secret)
        self.config['identity']['generate_password'] = False
        run_id = secrets.token_hex(16)
        media_token = secrets.token_urlsafe(24)
        report_url = (f"http://{self.config['media']['host']}:{self.config['media']['port']}"
                      f"/{media_token}/event")
        script = kickstart(plan, inv, self.config['network'], self.config['identity'], run_id, report_url)
        selected = {d['path'] for d in plan['disks']}
        def task():
            self.reviewed = None
            self.prepared_credentials = self.completed_credentials = None
            self.state['stage'] = 'preparing'
            run = self.run_directory()
            answer = answer_usb_image(run / 'alma-answer.img', {'ks.cfg': script}, label='OEMDRV')
            credentials = run / 'alma-credentials.json'
            credentials.write_text(json.dumps({'username': 'root', 'password': secret,
                'server_serial': self.discovery['system'].get('SerialNumber')}, indent=2))
            credentials.chmod(0o600)
            (run / 'alma-plan.yaml').write_text(yaml.safe_dump(plan, sort_keys=False))
            (run / 'ks.cfg').write_text(script)
            self.prepared_credentials = {'username': 'root', 'password': secret,
                'address': self.config['network']['address'].split('/')[0],
                'port': self.config['identity']['ssh_port'], 'protocol': 'SSH'}
            self.reviewed = {'os': os_id, 'source_iso': str(source),
                'answer_image': str(answer), 'plan_file': str(run / 'alma-plan.yaml'),
                'run_id': run_id, 'media_token': media_token, 'plan': plan,
                'disks_to_erase': [d for d in inv['installable'] if d['path'] in selected],
                'untouched': [d for d in inv['installable'] if d['path'] not in selected],
                'network': self.config['network'].copy(), 'username': 'root',
                'credentials_file': str(credentials)}
            self.state.update(stage='ready', message=f'AlmaLinux {version} minimal ISO and OEMDRV Kickstart are ready. Review every disk marked for erasure, the selected MAC and SSH port, then type INSTALL.')
        self.launch(task)

    def install_alma(self, body):
        review = self.reviewed
        if not review or review.get('os') != body.get('os') or body.get('os') not in ('alma98minimal', 'alma102minimal') or body.get('confirmation') != 'INSTALL':
            raise ValueError('Prepare AlmaLinux and type INSTALL after reviewing the disk plan')
        if yaml.safe_load(Path(review['plan_file']).read_text()) != review['plan']:
            raise ValueError('AlmaLinux disk plan changed since review; prepare again')
        source, answer = Path(review['source_iso']), Path(review['answer_image'])
        if not source.is_file() or not answer.is_file():
            raise ValueError('AlmaLinux minimal ISO or OEMDRV answer image is missing')
        def task():
            self.state['stage'] = 'installing'
            ilo = self.management_ilo()
            cd_path, _ = cd_device(self.discovery)
            floppy = floppy_device(self.discovery)
            if self.media_server:
                eject(ilo, cd_path)
                if self.windows_floppy_path:
                    eject(ilo, self.windows_floppy_path)
                    self.windows_floppy_path = None
                self.media_server.close()
                self.media_server = None
            run = self.run_directory()
            media = self.config['media']
            server = MediaServer(source, media['host'], media['port'], review['media_token'],
                run / 'media.log', extras={'answer.img': answer})
            server.start()
            try:
                mount_device(ilo, floppy, server.extra_urls['answer.img'])
                mount(ilo, self.discovery, server.url)
                baseline = server.iso_bytes_served
                boot_once(ilo, cd_path, require_virtual_cd=True)
                self.media_server = server
                self.windows_floppy_path = floppy[0]
            except Exception:
                try:
                    eject(ilo, floppy[0])
                except Exception:
                    pass
                server.close()
                raise
            self.reviewed = None
            self.state.update(stage='boot-requested', message='AlmaLinux virtual-CD boot requested. Installation is not yet verified; inspect the graphical console.')
            self.watch_alma_result(server, baseline, review['run_id'])
        self.launch(task)

    def prepare_windows(self, body):
        if not self.live or not self.discovery:
            raise ValueError('Complete live discovery before Windows installation')
        if not self.live.get('uefi'):
            raise ValueError('This Windows installer currently requires UEFI boot')
        self.settings(body)
        inv = inventory(self.discovery, self.live)
        target = next((d for d in inv['installable'] if d['path'] == body.get('target_disk')), None)
        if not target:
            raise ValueError('Select one live-verified Windows installation disk')
        data_disk_path = body.get('data_disk') or ''
        data_disk = next((d for d in inv['installable'] if d['path'] == data_disk_path), None)
        if data_disk_path and (not data_disk or data_disk['path'] == target['path']):
            raise ValueError('Select a separate live-verified Windows data disk')
        if not self.config['network'].get('mac'):
            raise ValueError('Select the Windows network NIC by MAC')
        source = ROOT / 'ISO/SERVER_EVAL_x64FRE_en-us.iso'
        if not source.is_file():
            raise ValueError('Windows Server 2022 Evaluation ISO was not found')
        from src.windows_unattend import render
        from src.windows_media import answer_usb_image
        from src.windows_boot_media import no_prompt_patch
        from src.esxi_media import generate_root_password
        secret = body.get('os_password') or generate_root_password()
        backup_secret = generate_root_password()
        media_token = secrets.token_urlsafe(24)
        report_url = (f"http://{self.config['media']['host']}:{self.config['media']['port']}"
                      f"/{media_token}/event")
        index = int(body.get('windows_index', 2))
        files = render(target, [d for d in inv['installable'] if d['path'] != target['path']],
            self.config['network']['mac'], self.config['network']['address'],
            self.config['network']['gateway'], self.config['network']['dns'],
            self.config['network']['hostname'], secret, index, backup_password=backup_secret,
            data_disk=data_disk, report_url=report_url,
            rdp_port=self.config['installation']['rdp_port'])
        def task():
            self.reviewed = None
            self.prepared_credentials = self.completed_credentials = None
            self.state['stage'] = 'preparing'
            run = self.run_directory()
            answer = answer_usb_image(run / 'windows-answer.img', files)
            self.windows_boot_patch = no_prompt_patch(source)
            self.windows_media_token = media_token
            credentials = run / 'windows-credentials.json'
            credentials.write_text(json.dumps({'username': 'Administrator', 'password': secret,
                'backup_username': 'iranserver', 'backup_password': backup_secret,
                'server_serial': self.discovery['system'].get('SerialNumber')}, indent=2))
            credentials.chmod(0o600)
            self.prepared_credentials = {'username': 'Administrator', 'password': secret,
                'address': self.config['network']['address'].split('/')[0],
                'port': self.config['installation']['rdp_port'], 'protocol': 'RDP',
                'backup_username': 'iranserver', 'backup_password': backup_secret}
            self.reviewed = {'os': 'windows2022', 'source_iso': str(source),
                'answer_image': str(answer), 'windows_index': index,
                'target_disk': target['path'], 'data_disk': data_disk_path,
                'disks_to_erase': [target] + ([data_disk] if data_disk else []),
                'untouched': [d for d in inv['installable'] if d['path'] not in
                              {target['path'], data_disk_path}],
                'network': self.config['network'].copy(), 'username': 'Administrator',
                'credentials_file': str(credentials)}
            self.state.update(stage='ready', message='Windows answer USB is prepared. Review every disk marked for erasure and the network, then type INSTALL. WinPE checks the OS disk before wiping; post-install checks the optional data disk separately.')
        self.launch(task)

    def install_windows(self, body):
        review = self.reviewed
        if not review or review.get('os') != 'windows2022' or body.get('confirmation') != 'INSTALL':
            raise ValueError('Prepare Windows and type INSTALL after reviewing its target disk')
        source, answer = Path(review['source_iso']), Path(review['answer_image'])
        if not source.is_file() or not answer.is_file() or not self.windows_boot_patch:
            raise ValueError('Windows source ISO or answer USB image is missing')
        def task():
            self.state['stage'] = 'installing'
            ilo = self.management_ilo()
            cd_path, _ = cd_device(self.discovery)
            floppy = floppy_device(self.discovery)
            if self.media_server:
                eject(ilo, cd_path)
                self.media_server.close()
                self.media_server = None
            run = self.run_directory()
            media = self.config['media']
            server = MediaServer(source, media['host'], media['port'], self.windows_media_token,
                run / 'media.log', extras={'answer.img': answer}, patches=[self.windows_boot_patch])
            server.start()
            try:
                mount_device(ilo, floppy, server.extra_urls['answer.img'])
                mount(ilo, self.discovery, server.url)
                boot_baseline = server.bytes_served
                boot_once(ilo, cd_path)
                self.media_server = server
                self.windows_floppy_path = floppy[0]
            except Exception:
                try:
                    eject(ilo, floppy[0])
                except Exception:
                    pass
                server.close()
                raise
            self.reviewed = None
            self.state.update(stage='boot-requested', message='Windows virtual-CD boot requested. Waiting for evidence that iLO reads the installer; installation is not verified.')
            self.watch_installer_media(server, boot_baseline, windows=True)
        self.launch(task)

    def prepare_esxi(self, body):
        if not self.live or not self.discovery:
            raise ValueError('Complete live discovery before ESXi installation')
        self.settings(body)
        inv = inventory(self.discovery, self.live)
        path = body.get('target_disk', '')
        target = next((d for d in inv['installable'] if d['path'] == path), None)
        if not target or not target.get('wwn'):
            raise ValueError('Select one live-verified RAID logical disk with a WWN')
        if not self.config['network'].get('mac'):
            raise ValueError('Select the ESXi management NIC by MAC')
        os_id = body.get('os')
        cpu = self.discovery['system'].get('ProcessorSummary', {}).get('Model', '')
        if os_id in ('esxi800', 'esxi801', 'esxi803') and 'E5-2680 v3' in cpu:
            raise ValueError('ESXi 8 rejected this server CPU (Xeon E5-2680 v3) during live installation. Select ESXi 7.0.3 for this host.')
        source = next(iter(sorted((ROOT / 'ISO').glob(OTHER_MEDIA[os_id]), reverse=True)), None)
        if not source:
            raise ValueError('ESXi 8.0.3 HPE ISO was not found')
        from src.esxi_media import kickstart, build, generate_root_password
        secret = body.get('os_password') or generate_root_password()
        script = kickstart(target, self.config['network']['mac'], self.config['network']['address'],
            self.config['network']['gateway'], self.config['network']['dns'],
            self.config['network']['hostname'], secret)
        def task():
            self.reviewed = None
            self.state['stage'] = 'preparing'
            destination = ROOT / f'generated/{os_id}-autoinstall.iso'
            build(source, destination, script)
            credentials = ROOT / 'generated/esxi-credentials.json'
            credentials.write_text(json.dumps({'username': 'root', 'password': secret,
                'server_serial': self.discovery['system'].get('SerialNumber')}, indent=2))
            self.reviewed = {'os': os_id, 'source_iso': str(source), 'prepared_iso': str(destination), 'target_disk': path,
                'disks_to_erase': [target],
                'untouched': [d for d in inv['installable'] if d['path'] != path],
                'network': self.config['network'].copy(), 'username': 'root',
                'credentials_file': str(credentials)}
            self.state.update(stage='ready', message='ESXi ISO is prepared. Review the single disk and management network, then type INSTALL. Keep this app open through installation.')
        self.launch(task)

    def install_esxi(self, body):
        review = self.reviewed
        if not review or review.get('os') != body.get('os') or review.get('os') not in ('esxi803', 'esxi801', 'esxi800', 'esxi703') or body.get('confirmation') != 'INSTALL':
            raise ValueError('Prepare ESXi and type INSTALL after reviewing its target disk')
        prepared_iso = Path(review['prepared_iso'])
        if not prepared_iso.is_file():
            raise ValueError('Prepared ESXi ISO is missing')
        def task():
            self.state['stage'] = 'installing'
            ilo = self.management_ilo()
            path, _ = cd_device(self.discovery)
            if self.media_server:
                eject(ilo, path)
                if self.windows_floppy_path:
                    eject(ilo, self.windows_floppy_path)
                    self.windows_floppy_path = None
                self.media_server.close()
                self.media_server = None
            run = self.run_directory()
            media = self.config['media']
            server = MediaServer(prepared_iso, media['host'], media['port'],
                secrets.token_urlsafe(24), run / 'media.log')
            server.start()
            try:
                mount(ilo, self.discovery, server.url)
                boot_baseline = server.bytes_served
                boot_once(ilo, path)
                self.media_server = server
            except Exception:
                server.close()
                raise
            self.reviewed = None
            self.state.update(stage='boot-requested', message='ESXi virtual-CD boot requested. Waiting for evidence that iLO reads the installer; installation is not verified.')
            self.watch_installer_media(server, boot_baseline)
        self.launch(task)

    def install(self, body):
        if body.get('os') in ('debian11dvd', 'debian12netinst', 'debian13netinst'):
            return self.install_debian(body)
        if body.get('os') in ('alma98minimal', 'alma102minimal'):
            return self.install_alma(body)
        if body.get('os') == 'windows2022':
            return self.install_windows(body)
        if body.get('os') in ('esxi803', 'esxi801', 'esxi800', 'esxi703'):
            return self.install_esxi(body)
        if self.config['installation'].get('os') in OTHER_MEDIA:
            raise ValueError('Windows/ESXi unattended installation is not implemented; no disk will be modified')
        if not self.reviewed or body.get('confirmation') != 'INSTALL':
            raise ValueError('Review the prepared plan and type INSTALL')
        if yaml.safe_load(Path('generated/partition-plan.yaml').read_text()) != self.reviewed['plan']:
            raise ValueError('Disk plan changed since review; prepare it again')
        def task():
            self.state['stage'] = 'installing'
            self.command('install', self.run_directory(), ['--accept-plan', 'INSTALL'], capture=True)
            self.discovery['system']['PowerState'] = 'On'
            message = 'بوت سیستم نصب‌شده و بررسی‌های نصب موفق بودند.'
            if self.config['installation'].get('offline'):
                message += ' دسترسی شبکه تأیید نشده است؛ نتیجهٔ نصب مستقل از دسترسی شبکه ثبت شد.'
            else:
                message += ' ورود SSH و تنظیمات شبکه نیز تأیید شدند.'
            self.completed_credentials = self.prepared_credentials
            self.state.update(stage='completed', message=message)
            self.reviewed = None
        self.launch(task)

    def management_ilo(self):
        if not self.discovery or not self.password:
            raise ValueError('Connect to iLO first')
        ilo = Ilo(self.config['ilo'], self.password, self.run_directory() / 'ilo.log')
        ilo.system_path = self.discovery['system_path']
        ilo.system = ilo.get(ilo.system_path)
        return ilo

    def graphical_console_url(self):
        """Create a browser iLO session; Redfish Basic auth is not an IRC key."""
        if not self.discovery or not self.password:
            raise ValueError('Connect to iLO first')
        address = self.config['ilo']['address']
        parsed = urlsplit(address if address.startswith('https://') else 'https://' + address)
        if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError('Invalid iLO HTTPS address')
        origin = 'https://' + parsed.netloc
        context = (ssl.create_default_context() if self.config['ilo'].get('verify_tls', True)
                   else ssl._create_unverified_context())
        payload = json.dumps({'method': 'login',
                              'user_login': self.config['ilo']['username'],
                              'password': self.password}).encode()
        request = urllib.request.Request(origin + '/json/login_session', data=payload,
            headers={'Content-Type': 'application/json'}, method='POST')
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}),
                                             urllib.request.HTTPSHandler(context=context))
        try:
            with opener.open(request, timeout=20) as response:
                result = json.load(response)
        except (OSError, ValueError, urllib.error.URLError) as exc:
            raise ValueError('iLO could not create a graphical-console session') from exc
        key = result.get('session_key') if isinstance(result, dict) else None
        if not isinstance(key, str) or not re.fullmatch(r'[0-9A-Fa-f]{32}', key):
            raise ValueError('iLO did not return a valid graphical-console session key')
        return origin + '/irc.html?' + urlencode({'token': key})

    def power(self, body):
        choices = {'on': 'On', 'restart': 'ForceRestart',
                   'shutdown': 'PushPowerButton', 'off': 'ForceOff'}
        kind = choices.get(body.get('action'))
        if not kind or body.get('confirmation') != body.get('action', '').upper():
            raise ValueError('Select a power action and type its uppercase name to confirm')
        def task():
            ilo = self.management_ilo()
            state = ilo.system.get('PowerState')
            if body['action'] in ('restart', 'shutdown') and state != 'On':
                raise ValueError('Server must be powered on for this action')
            if body['action'] == 'on' and state == 'On':
                self.state.update(message='Server is already on')
                return
            if body['action'] == 'off' and state == 'Off':
                self.state.update(message='Server is already off')
                return
            ilo.reset(kind)
            self.discovery['system'] = ilo.get(ilo.system_path)
            self.state.update(message='Power action sent: ' + kind)
        self.launch(task)

    def custom_media(self, body):
        operation = body.get('action')
        if operation not in ('mount', 'boot', 'eject'):
            raise ValueError('Choose mount, boot or eject')
        if operation == 'boot' and body.get('confirmation') != 'BOOT':
            raise ValueError('Type BOOT to confirm the server reboot')
        source = None
        if operation != 'eject':
            source = Path(body.get('iso_path', '')).expanduser().resolve()
            if not source.is_file() or source.suffix.lower() != '.iso' or source.stat().st_size < 1024 * 1024:
                raise ValueError('Choose an existing local ISO file of at least 1 MiB')
        def task():
            ilo = self.management_ilo()
            path, _ = cd_device(self.discovery)
            if operation == 'eject':
                eject(ilo, path)
                if self.windows_floppy_path:
                    eject(ilo, self.windows_floppy_path)
                    self.windows_floppy_path = None
                if self.media_server:
                    self.media_server.close()
                    self.media_server = None
                self.state.update(message='Virtual ISO ejected')
                return
            if self.media_server:
                eject(ilo, path)
                if self.windows_floppy_path:
                    eject(ilo, self.windows_floppy_path)
                    self.windows_floppy_path = None
                self.media_server.close()
                self.media_server = None
            run = self.run_directory()
            media = self.config['media']
            server = MediaServer(source, media['host'], media['port'], secrets.token_urlsafe(24), run / 'media.log')
            server.start()
            try:
                mount(ilo, self.discovery, server.url)
                if operation == 'boot':
                    boot_baseline = server.bytes_served
                    boot_once(ilo, path)
                self.media_server = server
            except Exception:
                server.close()
                raise
            if operation == 'boot':
                self.state.update(stage='boot-requested', message='Custom ISO boot requested. This ISO may contain an unattended installer; inspect its contents and the server console: ' + source.name)
                self.watch_installer_media(server, boot_baseline)
            else:
                self.state.update(message='Virtual ISO mounted: ' + source.name)
        self.launch(task)

    def console_start(self, body):
        if self.console:
            return
        if not self.discovery or not self.password:
            raise ValueError('Connect to iLO first')
        def task():
            run = self.run_directory()
            self.console_log = run / 'console.log'
            self.console = SerialCapture(self.config['ilo'], self.password, self.console_log).start()
            self.state.update(message='iLO text console connected')
        self.launch(task)

    def console_input(self, body):
        if not self.console or self.console.process.poll() is not None:
            raise ValueError('Open the text console first')
        value = body.get('text', '')
        if not isinstance(value, str) or len(value) > 512 or any(ord(c) < 32 and c not in '\t' for c in value):
            raise ValueError('Invalid console input')
        self.console.process.stdin.write(value.encode('utf-8') + b'\r')
        self.console.process.stdin.flush()

    def console_stop(self, body):
        if self.console:
            self.console.close()
            self.console = None
        self.state.update(message='iLO text console closed')

    def console_output(self):
        if not self.console_log or not self.console_log.exists():
            return {'connected': False, 'text': ''}
        raw = self.console_log.read_bytes()[-50000:]
        return {'connected': bool(self.console and self.console.process.poll() is None),
                'text': raw.decode('utf-8', errors='replace')}


def make_handler(app):
    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def send(self, status, data, mime='application/json'):
            raw = data if isinstance(data, bytes) else json.dumps(data).encode()
            self.send_response(status)
            self.send_header('Content-Type', mime)
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Content-Length', str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def authorized(self):
            return secrets.compare_digest(self.headers.get('X-App-Token', ''), app.token)

        def do_GET(self):
            url = urlsplit(self.path)
            if url.path == '/fonts/Vazirmatn.woff2':
                source = (ROOT / 'ui/fonts/Vazirmatn.woff2').read_bytes()
                return self.send(200, source, 'font/woff2')
            if url.path == '/partition-editor.js':
                source = (ROOT / 'ui/partition-editor.js').read_bytes()
                return self.send(200, source, 'text/javascript; charset=utf-8')
            if url.path == '/other-os-editor.js':
                source = (ROOT / 'ui/other-os-editor.js').read_bytes()
                return self.send(200, source, 'text/javascript; charset=utf-8')
            if url.path == '/' and secrets.compare_digest(parse_qs(url.query).get('token', [''])[0], app.token):
                html = (ROOT / 'ui/index.html').read_text(encoding='utf-8').replace('__TOKEN__', app.token)
                return self.send(200, html.encode(), 'text/html; charset=utf-8')
            if url.path == '/api/state' and self.authorized():
                return self.send(200, app.public_state())
            if url.path == '/api/credentials' and self.authorized():
                try:
                    return self.send(200, app.completed_access())
                except ValueError as exc:
                    return self.send(409, {'error': str(exc)})
            if url.path == '/api/console/output' and self.authorized():
                return self.send(200, app.console_output())
            self.send(403, {'error': 'Open the local URL printed when starting app.py'})

        def do_POST(self):
            if not self.authorized():
                return self.send(403, {'error': 'Invalid session'})
            try:
                size = int(self.headers.get('Content-Length', '0'))
                if not 0 < size <= 1_000_000:
                    raise ValueError('Invalid request size')
                body = json.loads(self.rfile.read(size))
                if self.path == '/api/console/graphical':
                    return self.send(200, {'url': app.graphical_console_url()})
                with app.lock:
                    if app.state['busy']:
                        raise ValueError('Wait for the active operation to finish')
                action = {'/api/connect': app.connect, '/api/probe': app.probe,
                          '/api/prepare': app.prepare, '/api/install': app.install,
                          '/api/power': app.power, '/api/media': app.custom_media,
                          '/api/console/start': app.console_start,
                          '/api/console/input': app.console_input,
                          '/api/console/stop': app.console_stop,
                          '/api/verify-windows': app.verify_windows_current}.get(self.path)
                if action is None:
                    return self.send(404, {'error': 'Unknown action'})
                action(body)
                self.send(202, {'accepted': True})
            except (ValueError, KeyError, yaml.YAMLError) as exc:
                self.send(400, {'error': str(exc)})
    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--open', action='store_true')
    args = parser.parse_args()
    os.chdir(ROOT)
    app = Application()
    app.token = saved_session_token(args.port) or app.token
    try:
        server = LocalServer(('127.0.0.1', args.port), make_handler(app))
    except OSError:
        existing = running_session_url(args.port)
        if existing:
            print('Provisioning panel is already running; opening its existing window.', flush=True)
            if args.open:
                webbrowser.open(existing)
            return 0
        print(f'Port {args.port} is already in use. Close the previous provisioning window/process or select another --port.', flush=True)
        return 1
    url = f'http://127.0.0.1:{server.server_port}/?token={app.token}'
    SESSION_FILE.parent.mkdir(exist_ok=True)
    SESSION_FILE.write_text(json.dumps({'port': server.server_port, 'pid': os.getpid(),
                                        'token': app.token}), encoding='utf-8')
    SESSION_FILE.chmod(0o600)
    print('Open local provisioning window:', url, flush=True)
    if args.open:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        # Keep the token in this user-only local file so a same-port restart
        # does not invalidate a browser tab that is already open.
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
