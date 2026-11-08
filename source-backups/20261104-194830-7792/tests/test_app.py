import http.server
import json
from pathlib import Path
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch
from unittest.mock import Mock
import yaml
from app import Application, LocalServer, make_handler, running_session_url, saved_session_token
from src.credentials import ensure_login
from src.serial_capture import SerialCapture
import io


class ApplicationTests(unittest.TestCase):
    def test_access_details_are_only_available_after_verified_completion(self):
        app = Application()
        app.prepared_credentials = {'username': 'root', 'password': 'test-secret',
                                    'address': '192.0.2.10', 'port': 2222, 'protocol': 'SSH'}
        with self.assertRaises(ValueError):
            app.completed_access()
        app.completed_credentials = app.prepared_credentials
        with self.assertRaises(ValueError):
            app.completed_access()
        app.state['stage'] = 'completed'
        self.assertEqual(app.completed_access()['port'], 2222)

    def test_occupied_ilo_vsp_reports_specific_preboot_error(self):
        class OccupiedSession:
            def __init__(self, args, stdin, stdout, **kwargs):
                self.stdin = io.BytesIO()
                self.terminated = False
                stdout.write(b'Virtual Serial Port is currently in use by another session.\n')
                stdout.flush()
            def poll(self):
                return 0 if self.terminated else None
            def terminate(self):
                self.terminated = True
            def wait(self, timeout=None):
                return 0
        with tempfile.TemporaryDirectory() as directory, patch('src.serial_capture.subprocess.Popen', OccupiedSession):
            capture = SerialCapture({'address': '192.0.2.1', 'username': 'ILO'}, 'secret', Path(directory) / 'serial.log')
            with self.assertRaisesRegex(RuntimeError, 'occupied by another session.*No ISO boot or disk wipe'):
                capture.start()
            self.assertTrue(capture.process.terminated)

    def test_second_app_cannot_share_the_first_apps_port(self):
        first = LocalServer(('127.0.0.1', 0), make_handler(Application()))
        try:
            with self.assertRaises(OSError):
                LocalServer(first.server_address, make_handler(Application()))
        finally:
            first.server_close()

    def test_launcher_reuses_only_authenticated_panel_session(self):
        app = Application()
        server = LocalServer(('127.0.0.1', 0), make_handler(app))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as directory:
                session = Path(directory) / 'panel-session.json'
                session.write_text(json.dumps({'port': server.server_port,
                                               'token': app.token}))
                self.assertEqual(running_session_url(server.server_port, session),
                                 f'http://127.0.0.1:{server.server_port}/?token={app.token}')
                self.assertIsNone(running_session_url(server.server_port + 1, session))
                session.write_text(json.dumps({'port': server.server_port,
                                               'token': 'incorrect'}))
                self.assertIsNone(running_session_url(server.server_port, session))
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_same_port_restart_reuses_local_browser_token(self):
        with tempfile.TemporaryDirectory() as directory:
            session = Path(directory) / 'panel-session.json'
            token = 'a' * 48
            session.write_text(json.dumps({'port': 8765, 'token': token}))
            self.assertEqual(saved_session_token(8765, session), token)
            self.assertIsNone(saved_session_token(8767, session))

    @patch('app.source_for', return_value=Path('unused-test.iso'))
    def test_clearing_custom_password_explicitly_returns_to_generated_password_mode(self, source_for):
        app = Application()
        app.config['network'].update(address='192.0.2.20/24', gateway='192.0.2.1',
                                     dns=['192.0.2.1'], hostname='test-server')
        app.discovery = {'system': {}}
        with patch('app.password_hash', return_value='$6$test'):
            app.settings({'os_password': 'test-password-value'})
        self.assertEqual(app.config['identity']['password_hash'], '$6$test')
        self.assertNotIn('test-password-value', json.dumps(app.config))
        app.settings({'os_password': ''})
        self.assertNotIn('password_hash', app.config['identity'])
        self.assertTrue(app.config['identity']['generate_password'])

    def test_cannot_prepare_or_install_without_current_target_discovery_and_review(self):
        app = Application()
        with self.assertRaises(ValueError):
            app.prepare({'plan': '{}'})
        with self.assertRaises(ValueError):
            app.install({'confirmation': 'INSTALL'})

    def test_local_api_requires_session_token_and_omits_ilo_password(self):
        app = Application()
        app.password = 'test-secret-must-not-be-returned'
        server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), make_handler(app))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        url = f'http://127.0.0.1:{server.server_port}'
        try:
            with self.assertRaises(urllib.error.HTTPError) as error:
                urllib.request.urlopen(url + '/api/state')
            self.assertEqual(error.exception.code, 403)
            req = urllib.request.Request(url + '/api/state', headers={'X-App-Token': app.token})
            data = urllib.request.urlopen(req).read().decode()
            self.assertNotIn(app.password, data)
            self.assertEqual(json.loads(data)['stage'], 'disconnected')
            page = urllib.request.urlopen(url + '/?token=' + app.token).read().decode()
            self.assertIn('INSTALL', page)
            self.assertNotIn('__TOKEN__', page)
            with self.assertRaises(urllib.error.HTTPError) as console_error:
                urllib.request.urlopen(urllib.request.Request(url + '/api/console/graphical',
                    data=b'{}', headers={'Content-Type': 'application/json'}, method='POST'))
            self.assertEqual(console_error.exception.code, 403)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_graphical_console_creates_separate_ilo_browser_session(self):
        app = Application()
        app.discovery = {'system': {}}
        app.password = 'local-test-password'
        app.config['ilo'] = {'address': '192.0.2.2', 'username': 'ILO',
                             'verify_tls': False}
        fake = Mock()
        fake.open.return_value = io.BytesIO(b'{"session_key":"0123456789abcdef0123456789abcdef"}')
        with patch('app.urllib.request.build_opener', return_value=fake):
            url = app.graphical_console_url()
        self.assertEqual(url, 'https://192.0.2.2/irc.html?token=0123456789abcdef0123456789abcdef')
        request = fake.open.call_args.args[0]
        self.assertEqual(request.full_url, 'https://192.0.2.2/json/login_session')
        self.assertIn(b'local-test-password', request.data)
        self.assertNotIn('local-test-password', url)

    def test_edited_plan_invalidates_install_confirmation(self):
        app = Application()
        app.reviewed = {'plan': {'disks': ['reviewed-disk']}}
        with patch.object(Path, 'read_text', return_value=yaml.safe_dump({'disks': ['different-disk']})):
            with self.assertRaisesRegex(ValueError, 'changed since review'):
                app.install({'confirmation': 'INSTALL'})

    def test_generated_credentials_persist_for_target_and_are_regenerated_for_another(self):
        config = {'ilo': {'address': '192.0.2.1'}, 'identity': {'username': 'provision', 'generate_password': True}}
        with tempfile.TemporaryDirectory() as directory, patch('src.credentials.password_hash', return_value='$6$test') as hash_password:
            root = Path(directory)
            ensure_login(config, root)
            first = json.loads((root / 'login-credentials.json').read_text())
            config['identity'].pop('password_hash')
            ensure_login(config, root)
            self.assertEqual(json.loads((root / 'login-credentials.json').read_text())['password'], first['password'])
            self.assertEqual(hash_password.call_count, 1)
            config['ilo']['address'] = '192.0.2.2'
            config['identity'].pop('password_hash')
            ensure_login(config, root)
            self.assertNotEqual(json.loads((root / 'login-credentials.json').read_text())['password'], first['password'])

    def test_no_carrier_proposes_reviewable_mac_instead_of_omitting_netplan(self):
        live = {'nics': [{'name': 'eno1', 'mac': '30:e1:71:6c:30:b0', 'carrier': False},
                         {'name': 'eno2', 'mac': '30:e1:71:6c:30:b1', 'carrier': False}]}
        app = Application()
        self.assertEqual(app.proposed_mac(live), '30:e1:71:6c:30:b0')
        live['nics'][1]['carrier'] = True
        self.assertEqual(app.proposed_mac(live), '30:e1:71:6c:30:b1')

    def test_power_requires_explicit_confirmation_and_custom_iso_must_exist(self):
        app = Application()
        with self.assertRaisesRegex(ValueError, 'confirm'):
            app.power({'action': 'restart', 'confirmation': ''})
        with self.assertRaisesRegex(ValueError, 'existing local ISO'):
            app.custom_media({'action': 'boot', 'confirmation': 'BOOT', 'iso_path': 'missing.iso'})

    def test_console_input_requires_live_session(self):
        app = Application()
        with self.assertRaisesRegex(ValueError, 'Open the text console'):
            app.console_input({'text': 'test'})

    def test_windows_report_changes_waiting_stage_only_for_matching_success(self):
        app = Application()
        app.config['network']['address'] = '192.0.2.102/24'
        app.state['stage'] = 'awaiting-verification'
        server = Mock(events=[json.dumps({'event': 'WINDOWS_POSTINSTALL',
            'success': True, 'address': '192.0.2.102'})])
        app.media_server = server
        with patch.object(app, 'finish_windows') as finish:
            app.watch_windows_result(server)
        finish.assert_called_once()
        server.events = [json.dumps({'event': 'WINDOWS_POSTINSTALL',
            'success': False, 'error': 'network configuration failed'})]
        with patch('app.time.sleep', side_effect=lambda _: setattr(app, 'media_server', None)):
            app.watch_windows_result(server)
        self.assertEqual(app.state['stage'], 'error')

    def test_windows_later_success_supersedes_early_rdp_failure(self):
        app = Application()
        app.config['network']['address'] = '192.0.2.102/24'
        app.state['stage'] = 'awaiting-verification'
        server = Mock(events=[json.dumps({'event': 'WINDOWS_POSTINSTALL',
            'success': False, 'error': 'listener=False'})])
        app.media_server = server
        success = json.dumps({'event': 'WINDOWS_POSTINSTALL', 'success': True,
                              'address': '192.0.2.102'})
        with patch('app.time.sleep', side_effect=lambda _: server.events.append(success)), \
             patch.object(app, 'finish_windows') as finish:
            app.watch_windows_result(server)
        self.assertEqual(app.state['stage'], 'error')
        finish.assert_called_once()

    def test_manual_windows_verification_requires_login_and_real_rdp_response(self):
        app = Application()
        app.discovery = {'system': {}}
        app.config['network']['address'] = '192.0.2.102/24'
        with self.assertRaisesRegex(ValueError, 'Log in'):
            app.verify_windows_current({'confirmation': ''})
        with patch.object(app, 'launch', side_effect=lambda task: task()), \
             patch.object(app, 'management_ilo', return_value=Mock(system={'PowerState': 'On'})), \
             patch('app.rdp_responds', return_value=True):
            app.verify_windows_current({'confirmation': 'VERIFIED'})
        self.assertEqual(app.state['stage'], 'completed')
        self.assertEqual(app.state['verification']['method'], 'operator-login-and-rdp')

    def test_windows_report_timeout_is_unverified_not_installer_failure(self):
        app = Application()
        app.state['stage'] = 'awaiting-verification'
        server = Mock(events=[])
        app.media_server = server
        with patch('app.time.monotonic', side_effect=[0, 6 * 3600 + 1]), \
             patch('app.time.sleep', side_effect=lambda _: setattr(app, 'media_server', None)):
            app.watch_windows_result(server)
        self.assertEqual(app.state['stage'], 'unverified')

    def test_windows_report_after_timeout_can_complete_install(self):
        app = Application()
        app.config['network']['address'] = '192.0.2.102/24'
        app.state['stage'] = 'awaiting-verification'
        server = Mock(events=[])
        app.media_server = server
        report = json.dumps({'event': 'WINDOWS_POSTINSTALL', 'success': True,
                             'address': '192.0.2.102'})
        with patch('app.time.monotonic', side_effect=[0, 6 * 3600 + 1]), \
             patch('app.time.sleep', side_effect=lambda _: server.events.append(report)), \
             patch.object(app, 'finish_windows') as finish:
            app.watch_windows_result(server)
        self.assertEqual(app.state['stage'], 'unverified')
        finish.assert_called_once()


if __name__ == '__main__':
    unittest.main()
