import copy
import json
import unittest
from unittest.mock import patch, mock_open, Mock
import tempfile
from pathlib import Path
from src.alma_direct import direct_inventory, storage_preflight
from src.alma_media import kickstart
from src.storage import recommend
from app import Application


class DirectAlmaTests(unittest.TestCase):
    def snapshot(self):
        return {'system': {'SerialNumber': 'server-a', 'Boot': {'BootSourceOverrideMode': 'UEFI'}},
                'resources': {'/nvme': {'@odata.type': '#Drive.v1_0_0.Drive',
                'Protocol': 'NVMe', 'MediaType': 'SSD', 'SerialNumber': 'serial-a',
                'CapacityBytes': 2000000000000, 'Model': 'NVMe SSD'},
                '/sata': {'@odata.type': '#Drive.v1_0_0.Drive', 'Protocol': 'SATA',
                'SerialNumber': 'member-a', 'CapacityBytes': 500000000000, 'Model': 'SATA'}}}

    def test_only_standalone_nvme_is_added_not_raid_physical_member(self):
        inv = direct_inventory(self.snapshot())
        self.assertEqual(len(inv['installable']), 1)
        self.assertEqual(inv['installable'][0]['identity']['serial'], 'serial-a')
        self.assertFalse(inv['linux_verified'])

    def run_preflight(self, rows, serial='server-a'):
        inv = direct_inventory(self.snapshot())
        plan = recommend(inv)
        commands = ['ignoredisk --only-use=' + plan['boot_disk'].removeprefix('/dev/')]
        lines = storage_preflight(plan, inv, commands)
        source = '\n'.join(lines[1:lines.index('%end')])
        files = mock_open(read_data=serial)
        with patch('builtins.open', files), patch('subprocess.check_output', return_value=json.dumps({'blockdevices': rows}).encode()):
            exec(compile(source, '<generated-preflight>', 'exec'), {})
        return files

    def row(self, **changes):
        return dict(name='nvme2n1', type='disk', serial='serial-a', wwn='', size=2000000000000, **changes)

    def test_resolves_nvme_using_serial_instead_of_device_order(self):
        files = self.run_preflight([self.row()])
        files().write.assert_called_once_with('ignoredisk --only-use=nvme2n1\n')

    def test_missing_disk_aborts_before_storage_file_is_written(self):
        with self.assertRaisesRegex(AssertionError, 'missing or ambiguous'):
            self.run_preflight([])

    def test_duplicate_serial_aborts(self):
        second = self.row(); second['name'] = 'nvme3n1'
        with self.assertRaisesRegex(AssertionError, 'missing or ambiguous'):
            self.run_preflight([self.row(), second])

    def test_wrong_capacity_aborts(self):
        row = self.row(); row['size'] = 1000000000000
        with self.assertRaisesRegex(AssertionError, 'missing or ambiguous'):
            self.run_preflight([row])

    def test_wrong_server_aborts(self):
        with self.assertRaisesRegex(AssertionError, 'server identity'):
            self.run_preflight([self.row()], serial='server-b')

    def test_offline_kickstart_needs_no_ip_mac_dns_or_gateway(self):
        inv = direct_inventory(self.snapshot())
        script = kickstart(recommend(inv), inv, {'deferred': True},
             {'password_hash': '$6$salt1234$' + 'x' * 86, 'ssh_port': 22},
             'a' * 32, 'http://192.0.2.2:8080/token/event')
        self.assertFalse(any(line.startswith('network ') for line in script.splitlines()))
        self.assertNotIn('route get', script)
        self.assertIn('%include /tmp/provision-storage.ks', script)
        self.assertIn('After=network.target', script)
        self.assertNotIn('Wants=network-online.target', script)

    def test_settings_skip_network_without_diagnostic_ubuntu_or_live_inventory(self):
        app = Application(); app.discovery = self.snapshot(); app.live = None
        with patch('app.source_for', side_effect=AssertionError('Diagnostic ISO must not be required')):
            app.settings({'os': 'alma98minimal', 'skip_network': True,
                          'address': '', 'gateway': '', 'dns': '', 'hostname': ''})
        self.assertTrue(app.config['network']['deferred'])

    def test_alma_serial_firstboot_is_verified_without_http_callback(self):
        app = Application()
        server = Mock(events=[])
        app.media_server = server
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / 'serial.log'
            log.write_text('PROVISION_ALMA_BOOT=' + 'a' * 32)
            app.config['installation']['alma_serial_log'] = str(log)
            with patch('app.threading.Thread') as thread, patch.object(app, 'finish_alma') as finish:
                app.watch_alma_result(server, 0, 'a' * 32)
                thread.call_args.kwargs['target']()
                finish.assert_called_once_with(server, 'a' * 32)

    def test_raid_wwn_is_checked_before_using_linux_disk_name(self):
        snapshot = self.snapshot()
        snapshot['resources']['/logical'] = {'@odata.type': '#HpeSmartStorageLogicalDrive.v2_0_0.LogicalDrive',
            'Id': '1', 'VolumeUniqueIdentifier': '60000D10001CDBB4', 'CapacityMiB': 476908, 'Raid': 'Raid1'}
        inv = direct_inventory(snapshot)
        plan = recommend(inv)
        self.assertIn('wwn-', plan['boot_disk'])
        selected = next(d for d in inv['installable'] if d['path'] == plan['boot_disk'])
        lines = storage_preflight(plan, inv, ['ignoredisk --only-use=' + plan['boot_disk'].removeprefix('/dev/')])
        source = '\n'.join(lines[1:lines.index('%end')])
        files = mock_open(read_data='server-a')
        rows = [{'name': 'sdc', 'type': 'disk', 'wwn': '0x60000d10001cdbb4', 'serial': '', 'size': selected['size']}]
        with patch('builtins.open', files), patch('subprocess.check_output', return_value=json.dumps({'blockdevices': rows}).encode()):
            exec(compile(source, '<raid-preflight>', 'exec'), {})
        files().write.assert_called_once_with('ignoredisk --only-use=sdc\n')

    def test_portable_hash_matches_reference_vectors(self):
        from src.sha512_crypt import hash_password
        vectors = [
            ('Hello world!', 'saltstring', 5000, 'svn8UoSVapNtMuq1ukKS4tPQd8iKwSMHWjl/O817G3uBnIFNjnQJuesI68u4OTLiBFdcbYEdFCoEOfaS35inz1'),
            ('Hello world!', 'saltstringsaltst', 10000, 'OW1/O6BYHV6BcXZu8QVeXbDWra3Oeqh0sbHbbMCVNSnCM/UrjmM0Dp8vOuZeHBy/YTBmSK6H9qs/y3RnOaw5v.'),
            ('This is just a test', 'toolongsaltstrin', 5000, 'lQ8jolhgVRVhY4b5pZKaysCLi0QBxGoNeKQzQ3glMhwllF7oGDZxUhx1yxdYcz/e1JSbq3y6JMxxl8audkUEm0')]
        for password, salt, rounds, expected in vectors:
            self.assertEqual(hash_password(password, salt, rounds).rsplit('$', 1)[1], expected)

    def test_password_hash_without_openssl(self):
        from src.credentials import password_hash
        with patch('src.credentials.shutil.which', return_value=None):
            value = password_hash('test-password-12345')
        self.assertTrue(value.startswith('$6$rounds=100000$'))
        self.assertEqual(len(value.rsplit('$', 1)[1]), 86)

    def test_fat_answer_image_has_label_valid_fat_chain_and_exact_file(self):
        from src.fat_media import answer_usb_image
        import struct
        payload = b'kickstart-data-' * 400
        with tempfile.TemporaryDirectory() as directory:
            path = answer_usb_image(Path(directory) / 'answer.img', {'ks.cfg': payload})
            with path.open('rb') as stream:
                boot = stream.read(512)
                self.assertEqual(boot[43:54], b'OEMDRV     ')
                self.assertEqual(boot[510:512], bytes([0x55, 0xaa]))
                stream.seek(512); fat = stream.read(128 * 512)
                self.assertEqual(fat, stream.read(128 * 512))
                root = stream.read(32 * 512)
                self.assertEqual(root[32:43], b'KS      CFG')
                cluster = struct.unpack_from('<H', root, 58)[0]
                size = struct.unpack_from('<I', root, 60)[0]
                data = b''
                while cluster < 0xfff8:
                    stream.seek((289 + (cluster - 2) * 4) * 512)
                    data += stream.read(2048)
                    cluster = struct.unpack_from('<H', fat, cluster * 2)[0]
                self.assertEqual(data[:size], payload)
            with self.assertRaises(FileExistsError):
                answer_usb_image(path, {'ks.cfg': 'replacement'})

    def test_confirmed_alma_install_enables_bounded_post_recovery(self):
        import yaml
        app = Application()
        app.discovery = self.snapshot()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'alma.iso'; source.write_bytes(b'test')
            answer = root / 'answer.img'; answer.write_bytes(b'test')
            plan = recommend(direct_inventory(app.discovery))
            plan_file = root / 'plan.yaml'; plan_file.write_text(yaml.safe_dump(plan))
            app.reviewed = {'os': 'alma98minimal', 'plan': plan, 'plan_file': str(plan_file),
                            'source_iso': str(source), 'answer_image': str(answer),
                            'media_token': 'test-token', 'run_id': 'a' * 32, 'network': {}}
            app.launch = lambda task: task()
            controller = Mock()
            app.management_ilo = Mock(return_value=controller)
            app.run_directory = Mock(return_value=root)
            app.watch_alma_result = Mock()
            server = Mock(url='http://host/iso', extra_urls={'answer.img': 'http://host/answer'})
            with patch('app.MediaServer', return_value=server), patch('app.cd_device', return_value=('/cd', {})), patch('app.floppy_device', return_value=('/usb', {})), patch('app.mount_device'), patch('app.mount'), patch('app.boot_once') as boot:
                with self.assertRaises(ValueError):
                    app.install_alma({'os': 'alma98minimal', 'confirmation': ''})
                boot.assert_not_called()
                app.install_alma({'os': 'alma98minimal', 'confirmation': 'INSTALL'})
                boot.assert_called_once_with(controller, '/cd', require_virtual_cd=True, allow_post_power_cycle=True)
                self.assertEqual(app.state['stage'], 'boot-requested')

    def test_local_console_install_uses_no_http_server_or_url_mount(self):
        import yaml
        app = Application(); app.discovery = self.snapshot()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'alma.iso'; source.write_bytes(b'test')
            answer = root / 'answer.img'; answer.write_bytes(b'test')
            plan = recommend(direct_inventory(app.discovery))
            plan_file = root / 'plan.yaml'; plan_file.write_text(yaml.safe_dump(plan))
            app.reviewed = {'os': 'alma98minimal', 'plan': plan, 'plan_file': str(plan_file),
                            'source_iso': str(source), 'answer_image': str(answer),
                            'media_transport': 'console', 'run_id': 'a' * 32,
                            'network': {'deferred': True}}
            app.launch = lambda task: task()
            controller = Mock(); controller.get.return_value = {'Inserted': True, 'ConnectedVia': 'Applet'}
            app.management_ilo = Mock(return_value=controller)
            app.run_directory = Mock(return_value=root); app.watch_alma_result = Mock()
            with patch('app.MediaServer') as http, patch('app.SerialCapture'), patch('app.cd_device', return_value=('/cd', {})), patch('app.floppy_device', return_value=('/usb', {})), patch('app.mount') as mount, patch('app.mount_device') as mount_device, patch('app.boot_once') as boot:
                app.install_alma({'os': 'alma98minimal', 'confirmation': 'INSTALL'})
                http.assert_not_called(); mount.assert_not_called(); mount_device.assert_not_called()
                boot.assert_called_once_with(controller, '/cd', require_virtual_cd=True, allow_post_power_cycle=True, local_console=True)
                self.assertTrue(app.media_server.local_console)
                self.assertEqual(app.state['stage'], 'awaiting-verification')
                controller.reset.assert_not_called()

    def test_configurable_media_port_is_validated(self):
        app = Application(); app.discovery = self.snapshot()
        app.settings({'os': 'alma98minimal', 'skip_network': True, 'media_port': 443})
        self.assertEqual(app.config['media']['port'], 443)
        with self.assertRaises(ValueError):
            app.settings({'os': 'alma98minimal', 'skip_network': True, 'media_port': 65536})
