import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from app import Application
from src.lo100 import Lo100

PROFILE = {'controller': 'LO100', 'model': 'DL160 G6', 'firmware': '4.26', 'advanced_license': True, 'system_guid': '8156aeda-8dfe-d511-8553-f4ce46866fde'}


def sample_inventory():
    return {'inventory_source':'existing-os-ssh-readonly','system_uuid':PROFILE['system_guid'],
            'server_serial':'G6-test-serial','server_model':'ProLiant DL160 G6','uefi':False,
            'lsblk':{'blockdevices':[{'name':'sda','type':'disk','size':100*1024**3,
                                     'model':'RAID SSD','serial':'test-disk','wwn':'0x1234','tran':'scsi','rota':False}]},
            'by_id':{'wwn-0x1234':'/dev/sda'},
            'nics':[{'name':'eth0','mac':'aa:bb:cc:dd:ee:ff','carrier':True}],
            'selected_nic':None,'candidates':[]}


class Lo100PanelTests(unittest.TestCase):
    def app(self):
        app = Application()
        app.launch = lambda task: task()
        app.lo100_profile = copy.deepcopy(PROFILE)
        app.discovery = {'controller': 'lo100', 'system': {'Model': 'DL160 G6'}, 'resources': {}, 'media': []}
        app.config['ilo'] = {'address': '172.20.224.30', 'username': 'ILO', 'controller': 'lo100'}
        app.password = 'management-test-secret'
        app.select_lo100_cd = Mock()
        app.state['lo100_media_probe'] = {'target':'172.20.224.30', 'port':443, 'status':'inconclusive'}
        return app

    def test_connect_uses_lo100_instead_of_redfish_and_clears_other_target(self):
        app = Application(); app.launch = lambda task: task()
        app.reviewed = {'target': 'previous-target'}
        with patch('src.lo100.Lo100') as legacy, patch('app.Ilo') as modern:
            legacy.return_value.inspect.return_value = copy.deepcopy(PROFILE)
            app.connect({'controller': 'lo100', 'address': '172.20.224.30', 'username': 'ILO', 'password': 'test-secret'})
            modern.assert_not_called()
        self.assertIsNone(app.reviewed)
        self.assertIsNone(app.live)
        self.assertEqual(app.public_state()['controller'], 'lo100')
        self.assertNotIn('test-secret', json.dumps(app.public_state()))

    def test_failed_legacy_connection_cannot_leave_old_installation_ready(self):
        app = Application(); app.launch = lambda task: task(); app.reviewed = {'old': True}
        with patch('src.lo100.Lo100') as legacy:
            legacy.return_value.inspect.side_effect = ConnectionResetError()
            with self.assertRaises(ConnectionResetError):
                app.connect({'controller': 'lo100', 'address': '172.20.224.30', 'username': 'ILO', 'password': 'test-secret'})
        self.assertIsNone(app.reviewed); self.assertIsNone(app.discovery)

    def test_default_controller_still_routes_to_existing_ilo_path(self):
        app = Application(); app.launch = Mock(); app.connect_lo100 = Mock()
        app.connect({'address': '172.20.224.42', 'username': 'ILO', 'password': 'test-secret'})
        app.connect_lo100.assert_not_called(); app.launch.assert_called_once()

    def test_unsupported_legacy_actions_never_open_redfish(self):
        app = self.app()
        with patch('app.Ilo') as modern:
            with self.assertRaises(ValueError): app.management_ilo()
            with patch.object(app, 'discover_lo100') as discover:
                app.probe({});discover.assert_called_once_with({})
            with self.assertRaises(ValueError): app.graphical_console_url()
            modern.assert_not_called()

    def test_preparation_is_inside_panel_and_keeps_operator_password(self):
        app = self.app()
        app.live = sample_inventory()
        from src.storage import inventory, recommend
        import yaml
        plan = recommend(inventory(app.discovery, app.live), False)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app.run_directory = Mock(return_value=root)
            def build(source, destination, hostname, secret, **kwargs):
                destination.mkdir()
                output = destination / 'prepared.iso'; output.write_bytes(b'test')
                (destination / 'login-credentials.json').write_text('{}')
                self.assertEqual(secret, 'chosen-test-password')
                return output
            with patch('src.lo100_ubuntu.prepare_ubuntu', side_effect=build) as prepare, patch('app.source_for', return_value=root / 'source.iso'), patch('app.Ilo') as modern:
                app.prepare({'os': 'ubuntu24', 'hostname': 'ubuntu-g6', 'os_password': 'chosen-test-password', 'skip_network':True, 'plan':yaml.safe_dump(plan)})
                prepare.assert_called_once(); modern.assert_not_called()
            self.assertEqual(app.state['stage'], 'ready')
            self.assertEqual(app.reviewed['target'], '172.20.224.30')
            self.assertEqual(app.reviewed['storage_mode'], 'reviewed-automatic')
            self.assertEqual(app.prepared_credentials['password'], 'chosen-test-password')
            self.assertNotIn('chosen-test-password', json.dumps(app.public_state()))

    def test_other_os_is_not_silently_used_on_legacy_target(self):
        app = self.app()
        with self.assertRaises(ValueError): app.prepare({'os': 'alma98minimal'})

    def ready_review(self, app, path):
        path.write_bytes(b'test')
        app.reviewed = {'controller': 'lo100', 'os': 'ubuntu24', 'target': '172.20.224.30', 'prepared_iso': str(path)}

    def test_start_requires_confirmation_and_verified_automatic_or_manual_media(self):
        app = self.app()
        with tempfile.TemporaryDirectory() as directory, patch('src.lo100.Lo100') as legacy:
            self.ready_review(app, Path(directory) / 'prepared.iso')
            with self.assertRaises(ValueError): app.install({'os':'ubuntu24'})
            with patch.object(app,'mount_lo100_automatic',return_value=False) as mount:
                app.install({'os':'ubuntu24','confirmation':'INSTALL'})
                mount.assert_called_once()
            legacy.assert_not_called()
            self.assertIsNotNone(app.reviewed)

    def test_failed_one_time_cd_readback_prevents_install_reset(self):
        app=self.app();app.select_lo100_cd.side_effect=RuntimeError('CD not confirmed')
        with tempfile.TemporaryDirectory() as directory,patch('src.lo100.Lo100') as legacy:
            self.ready_review(app,Path(directory)/'install.iso')
            with self.assertRaises(RuntimeError):app.install({'os':'ubuntu24','confirmation':'INSTALL','lo100_media_ready':True})
            legacy.return_value.boot_install.assert_not_called()
            self.assertIsNotNone(app.reviewed)

    def test_install_requests_one_boot_and_does_not_claim_os_success(self):
        app = self.app()
        with tempfile.TemporaryDirectory() as directory, patch('src.lo100.Lo100') as legacy:
            self.ready_review(app, Path(directory) / 'prepared.iso')
            legacy.return_value.boot_install.return_value = {'requested_action': 'HardReset', 'installation_verified': False}
            body = {'os': 'ubuntu24', 'confirmation': 'INSTALL', 'lo100_media_ready': True}
            app.install(body)
            legacy.return_value.boot_install.assert_called_once_with(confirmation='INSTALL', media_confirmed=True)
            self.assertEqual(app.state['stage'], 'awaiting-verification')
            self.assertIsNone(app.completed_credentials)
            with self.assertRaises(ValueError): app.install(body)

    def test_review_for_another_target_cannot_boot_current_target(self):
        app = self.app(); app.reviewed = {'controller': 'lo100', 'target': 'different', 'os': 'ubuntu24'}
        with patch('src.lo100.Lo100') as legacy:
            with self.assertRaises(ValueError):
                app.install({'os': 'ubuntu24', 'confirmation': 'INSTALL', 'lo100_media_ready': True})
            legacy.assert_not_called()

    def test_success_requires_explicit_operator_login_confirmation(self):
        app = self.app(); app.prepared_credentials = {'username': 'provision', 'password': 'test-secret'}
        with self.assertRaises(ValueError): app.verify_lo100({'confirmation': 'VERIFIED'})
        app.lo100_installation = {'target': '172.20.224.30'}
        app.state['stage'] = 'awaiting-verification'
        with self.assertRaises(ValueError): app.verify_lo100({'confirmation': ''})
        app.verify_lo100({'confirmation': 'VERIFIED'})
        self.assertEqual(app.state['verification']['method'], 'operator-console-login')
        self.assertFalse(app.state['verification']['automatic'])
        self.assertEqual(app.completed_access()['username'], 'provision')

    def test_console_button_launches_java_and_does_not_duplicate_open_window(self):
        app = self.app()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); jar = root / 'generated/lo100-console/M2.JAR'
            jar.parent.mkdir(parents=True); jar.write_bytes(b'test')
            app.run_directory = Mock(return_value=root)
            with patch('app.ROOT', root), patch('src.lo100.console_command', return_value=['java', '-jar', str(jar)]), patch('app.subprocess.Popen') as launch, patch('src.lo100.Lo100') as controller:
                launch.return_value.poll.return_value = None
                app.launch_lo100_console({})
                launch.assert_called_once()
                controller.assert_not_called()
                app.launch_lo100_console({})
                launch.assert_called_once()

    def test_ambiguous_boot_remains_unverified_and_can_be_checked_in_console(self):
        from src.lo100 import BootRequestUncertain
        app = self.app()
        with tempfile.TemporaryDirectory() as directory, patch('src.lo100.Lo100') as controller:
            self.ready_review(app, Path(directory) / 'prepared.iso')
            controller.return_value.boot_install.side_effect = BootRequestUncertain('Ambiguous')
            app.install({'os': 'ubuntu24', 'confirmation': 'INSTALL', 'lo100_media_ready': True})
            self.assertEqual(app.state['stage'], 'awaiting-verification')
            self.assertEqual(app.lo100_installation['boot_request']['response'], 'ambiguous')
            self.assertIsNone(app.completed_credentials)
            controller.return_value.boot_install.assert_called_once()


class Lo100BootRequestTests(unittest.TestCase):
    def controller(self, status='ON'):
        controller = Lo100('172.20.224.30', 'ILO', 'test')
        controller.read = Mock(return_value=('<img src="lights-out100_rgb.jpg">Power Status: ' + status +
            '<select name="PwrCtrl"><option value="HardReset">Reset</option><option value="PowerUp">On</option></select>').encode())
        context = Mock(); context.__enter__ = Mock(return_value=Mock()); context.__exit__ = Mock(return_value=False)
        controller.opener = Mock(); controller.opener.open.return_value = context
        return controller

    def test_power_query_matches_observed_form(self):
        controller = self.controller()
        result = controller.boot_install(confirmation='INSTALL', media_confirmed=True)
        self.assertEqual(result['requested_action'], 'HardReset')
        self.assertFalse(result['installation_verified'])
        self.assertIn('PwrCtrl=HardReset&Button1=Apply', controller.opener.open.call_args.args[0])
        controller.opener.open.assert_called_once()

    def test_off_target_is_powered_on_instead_of_reset(self):
        controller = self.controller('OFF')
        self.assertEqual(controller.boot_install(confirmation='INSTALL', media_confirmed=True)['requested_action'], 'PowerUp')

    def test_ambiguous_response_never_repeats_power_action(self):
        controller = self.controller(); controller.opener.open.side_effect = ConnectionResetError()
        with self.assertRaisesRegex(RuntimeError, 'NOT retried'):
            controller.boot_install(confirmation='INSTALL', media_confirmed=True)
        controller.opener.open.assert_called_once()

    def test_unconfirmed_boot_never_even_reads_power_page(self):
        controller = self.controller()
        with self.assertRaises(ValueError): controller.boot_install(confirmation='', media_confirmed=True)
        controller.read.assert_not_called(); controller.opener.open.assert_not_called()

class Lo100TLSRoutingTests(unittest.TestCase):
    app = Lo100PanelTests.app

    def test_tls_handshake_failure_routes_verified_lo100_in_same_task(self):
        import ssl,urllib.error
        app=self.app()
        error=urllib.error.URLError(ssl.SSLError('SSLV3_ALERT_HANDSHAKE_FAILURE'))
        with tempfile.TemporaryDirectory() as d,patch.object(app,'run_directory',return_value=Path(d)),patch('app.Ilo',side_effect=error),patch('src.lo100.Lo100') as legacy:
            legacy.return_value.inspect.return_value=copy.deepcopy(PROFILE)
            app.connect({'address':'172.20.224.30','username':'ILO','password':'test','controller':'redfish'})
            self.assertEqual(app.public_state()['controller'],'lo100')
            self.assertEqual(app.state['stage'],'connected')
            self.assertEqual(app.config['ilo']['address'],'172.20.224.30')
            legacy.return_value.inspect.assert_called_once()

    def test_tls_fallback_requires_positive_lo100_identity(self):
        import ssl,urllib.error
        app=self.app();error=urllib.error.URLError(ssl.SSLError('SSLV3_ALERT_HANDSHAKE_FAILURE'))
        with tempfile.TemporaryDirectory() as d,patch.object(app,'run_directory',return_value=Path(d)),patch('app.Ilo',side_effect=error),patch('src.lo100.Lo100') as legacy:
            legacy.return_value.inspect.side_effect=ValueError('Not LO100')
            with self.assertRaises(urllib.error.URLError):app.connect({'address':'172.20.224.42','username':'ILO','password':'test'})
            self.assertIsNone(app.discovery)

    def test_strict_tls_and_certificate_errors_never_use_legacy_http(self):
        import ssl,urllib.error
        for verify,error_text in ((True,'SSLV3_ALERT_HANDSHAKE_FAILURE'),(False,'CERTIFICATE_VERIFY_FAILED')):
            app=self.app();error=urllib.error.URLError(ssl.SSLError(error_text))
            with tempfile.TemporaryDirectory() as d,patch.object(app,'run_directory',return_value=Path(d)),patch('app.Ilo',side_effect=error),patch('src.lo100.Lo100') as legacy:
                with self.assertRaises(urllib.error.URLError):app.connect({'address':'172.20.224.42','username':'ILO','password':'test','verify_tls':verify})
                legacy.assert_not_called()
