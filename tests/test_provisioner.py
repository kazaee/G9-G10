import json
import tempfile
import unittest
from unittest.mock import patch, MagicMock
import urllib.request
import urllib.error
from pathlib import Path
from src.storage import recommend, compile_plan
from src.network import netplan, ram_iso_arguments
from src.virtual_media import MediaServer, prefer_hard_disk_on_next_boot
from src.autoinstall import generate
from src.verifier import verify
from src.live_report import from_serial
from src.offline_verify import validate as validate_offline
from src.live_probe import select_nic
from src.install_hook import configure_root


class NicSelectionTests(unittest.TestCase):
    def test_prefers_gateway_verified_port_among_multiple_links(self):
        nics = [{'name': 'eno1', 'mac': 'a', 'carrier': True}, {'name': 'eno2', 'mac': 'b', 'carrier': True}]
        chosen = select_nic(nics, [{'name': 'eno2', 'mac': 'b'}])
        self.assertEqual(chosen['mac'], 'b')
        self.assertTrue(chosen['gateway_verified'])

    def test_one_connected_port_can_be_selected_without_gateway_success(self):
        chosen = select_nic([{'name': 'eno3', 'mac': 'c', 'carrier': True}], [])
        self.assertEqual(chosen['name'], 'eno3')
        self.assertFalse(chosen['gateway_verified'])

    def test_does_not_guess_when_no_link_or_ambiguous_links(self):
        self.assertIsNone(select_nic([{'name': 'eno1', 'mac': 'a', 'carrier': False}], []))
        self.assertIsNone(select_nic([{'name': 'eno1', 'mac': 'a', 'carrier': True},
                                     {'name': 'eno2', 'mac': 'b', 'carrier': True}], []))


class OfflineVerificationTests(unittest.TestCase):
    def setUp(self):
        import yaml
        self.config = {'network': {'address': '192.0.2.20/24', 'gateway': '192.0.2.1',
            'dns': ['1.1.1.1'], 'hostname': 'test', 'mac': '00:11:22:33:44:55'}}
        self.plan = {'disks': [{'path': '/dev/disk/by-id/wwn-0x123', 'partitions': [
            {'mount': '/', 'filesystem': 'ext4'}]}]}
        self.completion = {'installer_boot_id': 'installer'}
        self.evidence = {'event': 'installed-boot', 'run_id': 'current',
            'marker': {'run_id': 'current', 'installer_boot_id': 'installer'},
            'boot_id': 'new-boot', 'hostname': 'test', 'os': 'ID=ubuntu\nVERSION_ID="24.04"\n',
            'ip': [], 'route': [],
            'mounts': {'filesystems': [{'target': '/', 'fstype': 'ext4'}]},
            'disks': {'blockdevices': [{'wwn': '0x123', 'mountpoints': ['/']}]},
            'netplan_files': {'config.yaml': yaml.safe_dump({'network': netplan(self.config['network'])})}}

    def test_installed_disk_boot_succeeds_without_network_reachability(self):
        self.assertTrue(all(validate_offline(self.evidence, self.completion, self.config, self.plan, 'current')['checks'].values()))

    def test_deferred_network_requires_saved_intent_and_enabled_retry(self):
        import copy
        self.config['network'].update(mac='', deferred=True, allowed_macs=['00:11:22:33:44:55'])
        self.evidence['netplan_files'] = {}
        self.evidence['network_intent'] = copy.deepcopy(self.config['network'])
        self.evidence['network_retry_enabled'] = True
        result = validate_offline(self.evidence, self.completion, self.config, self.plan, 'current')
        self.assertNotIn('network_configured', result['checks'])
        self.assertTrue(all(result['checks'].values()))
        self.evidence['network_intent']['gateway'] = '192.0.2.99'
        with self.assertRaises(RuntimeError):
            validate_offline(self.evidence, self.completion, self.config, self.plan, 'current')

    def test_deferred_selection_never_guesses_between_connected_ports(self):
        from src.deferred_network import choose
        first, second = {'ifname': 'eno1'}, {'ifname': 'eno2'}
        self.assertIsNone(choose([], []))
        self.assertIsNone(choose([first, second], []))
        self.assertEqual(choose([first], []), first)
        self.assertEqual(choose([first, second], [second]), second)
        self.assertIsNone(choose([first, second], [first, second]))

    def test_locked_administrator_cannot_pass_installation_verification(self):
        self.config['identity'] = {'username': 'provision', 'ssh_password_login': True}
        self.evidence['administrator'] = {'username': 'provision', 'password_set': False, 'sudo_member': True}
        with self.assertRaises(RuntimeError):
            validate_offline(self.evidence, self.completion, self.config, self.plan, 'current')
        self.evidence['administrator']['password_set'] = True
        self.assertTrue(validate_offline(self.evidence, self.completion, self.config, self.plan, 'current')['checks']['administrator_ready'])

    def test_root_requires_password_and_selected_ssh_port_in_boot_evidence(self):
        self.config['identity'] = {'username': 'root', 'ssh_password_login': True, 'ssh_port': 2222}
        self.evidence['administrator'] = {'username': 'root', 'password_set': True, 'sudo_member': False}
        self.evidence['ssh_settings'] = 'Port 2222\nPermitRootLogin yes\nPasswordAuthentication yes\n'
        self.evidence['installer_account_absent'] = True
        checks = validate_offline(self.evidence, self.completion, self.config, self.plan, 'current')['checks']
        self.assertTrue(checks['administrator_ready'] and checks['root_ssh_configured'])
        self.evidence['ssh_settings'] = 'Port 22\nPermitRootLogin yes\nPasswordAuthentication yes\n'
        with self.assertRaises(RuntimeError):
            validate_offline(self.evidence, self.completion, self.config, self.plan, 'current')

    def test_rejects_installer_boot_or_live_root_or_wrong_disk(self):
        import copy
        for change in ('boot', 'root', 'disk', 'network', 'run'):
            evidence = copy.deepcopy(self.evidence)
            if change == 'boot':
                evidence['boot_id'] = 'installer'
            elif change == 'root':
                evidence['mounts']['filesystems'][0]['fstype'] = 'overlay'
            elif change == 'disk':
                evidence['disks']['blockdevices'][0]['wwn'] = '0x999'
            elif change == 'network':
                evidence['netplan_files'] = {}
            else:
                evidence['run_id'] = 'stale'
            with self.subTest(change=change), self.assertRaises(RuntimeError):
                validate_offline(evidence, self.completion, self.config, self.plan, 'current')

    def test_offline_completion_does_not_attempt_http_callback(self):
        from src.install_hook import post
        from unittest.mock import mock_open
        with patch('builtins.open', mock_open()) as serial, patch('src.install_hook.urllib.request.urlopen') as http:
            post({'run_id': 'current', 'offline': True}, 'install-complete', installer_boot_id='installer')
            http.assert_not_called()
            encoded_line = serial().write.call_args[0][0]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'serial.log'
            path.write_text(encoded_line)
            self.assertEqual(from_serial(path, 'current', 'PROVISION_ZLIB_BASE64', 'run_id', 'install-complete')['installer_boot_id'], 'installer')


class SerialReportTests(unittest.TestCase):
    def test_recovers_current_report_and_ignores_stale_or_partial_records(self):
        import base64
        import zlib
        def record(probe):
            data = json.dumps({'event': 'live-inventory', 'probe_id': probe}).encode()
            return 'INVENTORY_ZLIB_BASE64=' + base64.b64encode(zlib.compress(data)).decode() + '\n'
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'serial.log'
            path.write_text(record('old'))
            self.assertIsNone(from_serial(path, 'current'))
            path.write_text(record('old') + record('current') + 'INVENTORY_ZLIB_BASE64=eJw')
            self.assertEqual(from_serial(path, 'current')['probe_id'], 'current')


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.inv = {'installable': [
            {'path': '/dev/disk/by-id/wwn-ssd', 'size': 1000 * 1024**3, 'bus': 'RAID', 'media': 'SSD'},
            {'path': '/dev/disk/by-id/wwn-hdd', 'size': 4000 * 1024**3, 'bus': 'RAID', 'media': 'HDD'}]}
        self.plan = recommend(self.inv)

    def lvm_plan(self):
        plan = recommend(self.inv)
        plan['disks'][0]['partitions'] = plan['disks'][0]['partitions'][:2] + [
            {'name': 'pv', 'size': -1, 'vg': 'vg0'}]
        plan['lvm'] = [
            {'name': 'swap', 'vg': 'vg0', 'size': '8G', 'filesystem': 'swap'},
            {'name': 'root', 'vg': 'vg0', 'size': -1, 'filesystem': 'ext4', 'mount': '/'}]
        return plan

    def test_only_selected_disk_erased(self):
        actions = compile_plan(self.plan, self.inv)
        self.assertEqual([x['path'] for x in actions if x['type'] == 'disk'], ['/dev/disk/by-id/wwn-ssd'])
        self.assertEqual([x['path'] for x in actions if x['type'] == 'mount'], ['', '/', '/boot', '/boot/efi'])
        root = next(x for x in actions if x.get('id') == 'disk-0-root')
        self.assertEqual(root['size'], -1)
        self.assertEqual(self.plan['disks'][0]['partitions'][-1]['size'], -1)
        self.assertEqual(self.plan['lvm'], [])

    def test_reject_unstable_or_unknown_path(self):
        self.plan['disks'][0]['path'] = '/dev/sda'
        self.plan['boot_disk'] = '/dev/sda'
        with self.assertRaises(ValueError):
            compile_plan(self.plan, self.inv)

    def test_reject_oversized_lv(self):
        self.plan = self.lvm_plan()
        self.plan['lvm'][0]['size'] = '2000G'
        with self.assertRaises(ValueError):
            compile_plan(self.plan, self.inv)

    def test_fill_lv_must_be_last_for_its_vg(self):
        self.plan = self.lvm_plan()
        self.plan['lvm'].reverse()
        with self.assertRaisesRegex(ValueError, 'final LV'):
            compile_plan(self.plan, self.inv)

    def test_reject_missing_efi(self):
        self.plan['disks'][0]['partitions'][0]['mount'] = '/wrong'
        with self.assertRaises(ValueError):
            compile_plan(self.plan, self.inv)

    def test_separate_boot_and_os_disk(self):
        root = self.plan['disks'][0]['partitions'].pop()
        self.plan['disks'].append({'path': '/dev/disk/by-id/wwn-hdd', 'ptable': 'gpt',
            'partitions': [root]})
        self.plan['untouched'] = []
        self.assertEqual(len([x for x in compile_plan(self.plan, self.inv) if x['type'] == 'disk']), 2)

    def test_direct_ssd_backup_and_sas_root_without_lvm(self):
        plan = {'boot_mode': 'uefi', 'boot_disk': self.inv['installable'][0]['path'],
            'disks': [
                {'path': self.inv['installable'][0]['path'], 'ptable': 'gpt', 'partitions': [
                    {'name': 'efi', 'size': '1G', 'filesystem': 'fat32', 'mount': '/boot/efi', 'flag': 'boot'},
                    {'name': 'boot', 'size': '2G', 'filesystem': 'ext4', 'mount': '/boot'},
                    {'name': 'swap', 'size': '16G', 'filesystem': 'swap'},
                    {'name': 'backup', 'size': -1, 'filesystem': 'ext4', 'mount': '/backup'}]},
                {'path': self.inv['installable'][1]['path'], 'ptable': 'gpt', 'partitions': [
                    {'name': 'root', 'size': -1, 'filesystem': 'ext4', 'mount': '/'}]}],
            'lvm': [], 'untouched': []}
        actions = compile_plan(plan, self.inv)
        mounts = {a['path']: a['device'] for a in actions if a['type'] == 'mount'}
        self.assertEqual(mounts['/'], 'disk-1-root-format')
        self.assertEqual(mounts['/backup'], 'disk-0-backup-format')
        self.assertEqual(mounts['/boot'], 'disk-0-boot-format')
        self.assertEqual(mounts['/boot/efi'], 'disk-0-efi-format')
        self.assertFalse(any(a['type'].startswith('lvm_') for a in actions))

    def test_bios_plan(self):
        actions = compile_plan(recommend(self.inv, False), self.inv)
        self.assertTrue(any(x.get('flag') == 'bios_grub' for x in actions))

    def test_custom_mount_is_a_real_filesystem_action(self):
        self.plan = self.lvm_plan()
        self.plan['lvm'].insert(-1, {'name': 'backup', 'vg': 'vg0', 'size': '100G',
                                    'filesystem': 'ext4', 'mount': '/backup'})
        actions = compile_plan(self.plan, self.inv)
        self.assertTrue(any(x['type'] == 'mount' and x['path'] == '/backup' for x in actions))

    def test_two_nvme_raid1_has_mirrored_boot_and_root_devices(self):
        paths = ['/dev/disk/by-id/nvme-eui.a', '/dev/disk/by-id/nvme-eui.b']
        nvme = {'installable': [{'path': path, 'bus': 'nvme', 'size': 500 * 1024**3,
                                'media': 'SSD'} for path in paths]}
        plan = {'boot_mode': 'uefi', 'boot_disk': paths[0], 'software_raid': {'level': 1, 'members': paths},
                'disks': [{'path': path, 'ptable': 'gpt', 'partitions': []} for path in paths],
                'lvm': [{'name': 'swap', 'vg': 'vg0', 'size': '8G', 'filesystem': 'swap'},
                        {'name': 'root', 'vg': 'vg0', 'size': -1, 'filesystem': 'ext4', 'mount': '/'}],
                'untouched': []}
        actions = compile_plan(plan, nvme)
        self.assertEqual([a['id'] for a in actions if a['type'] == 'raid'], ['md-boot', 'md-root'])
        self.assertEqual(len([a for a in actions if a.get('grub_device') is True]), 2)
        self.assertEqual([a['path'] for a in actions if a['type'] == 'disk'], paths)
        self.assertNotIn('size', next(a for a in actions if a.get('name') == 'root'))
        plan['software_raid']['members'] = [paths[0], paths[0]]
        with self.assertRaisesRegex(ValueError, 'distinct'):
            compile_plan(plan, nvme)

    def test_two_nvme_raid0_keeps_mirrored_boot_and_stripes_root(self):
        paths = ['/dev/disk/by-id/nvme-eui.a', '/dev/disk/by-id/nvme-eui.b']
        nvme = {'installable': [{'path': path, 'bus': 'nvme', 'size': 500 * 1024**3} for path in paths]}
        plan = {'boot_mode': 'uefi', 'boot_disk': paths[0], 'software_raid': {'level': 0, 'members': paths},
            'disks': [{'path': path, 'ptable': 'gpt', 'partitions': []} for path in paths],
            'lvm': [{'name': 'root', 'vg': 'vg0', 'size': '700G', 'filesystem': 'ext4', 'mount': '/'}],
            'untouched': []}
        actions = compile_plan(plan, nvme)
        self.assertEqual([(a['name'], a['raidlevel']) for a in actions if a['type'] == 'raid'],
            [('mdboot', 1), ('mdroot', 0)])

    def test_seed_network_and_callbacks(self):
        import yaml
        config = {'network': {'address': '192.0.2.20/24', 'gateway': '192.0.2.1',
            'dns': ['1.1.1.1'], 'hostname': 'test', 'mac': '00:11:22:33:44:55'},
            'identity': {'username': 'root', 'ssh_public_key': 'ssh-ed25519 test-fixture'}}
        seed = yaml.safe_load(generate(config, self.plan, self.inv, 'test-run', 'http://192.0.2.2/token/event'))['autoinstall']
        nic = seed['network']['ethernets']['management']
        self.assertEqual(nic['addresses'], ['192.0.2.20/24'])
        self.assertEqual(nic['match'], {'macaddress': '00:11:22:33:44:55'})
        self.assertFalse(seed['ssh']['allow-pw'])
        self.assertEqual(seed['identity']['username'], 'provision-installer')
        self.assertEqual(seed['early-commands'], ['python3 /cdrom/install_hook.py preflight'])

    def test_root_access_is_written_to_target_before_installer_completion(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            (target / 'etc').mkdir()
            (target / 'etc/shadow').write_text('root:!:20000:0:99999:7:::\n')
            configure_root(target, {'username': 'root', 'root_password_hash': '$6$test',
                'ssh_public_key': 'ssh-ed25519 example', 'ssh_port': 2222})
            self.assertIn('root:$6$test:', (target / 'etc/shadow').read_text())
            self.assertEqual((target / 'root/.ssh/authorized_keys').read_text(), 'ssh-ed25519 example\n')
            self.assertEqual((target / 'etc/ssh/sshd_config.d/00-provision-root.conf').read_text(),
                'Port 2222\nPermitRootLogin yes\nPasswordAuthentication yes\n')

    def test_reject_invalid_gateway(self):
        with self.assertRaises(ValueError):
            netplan({'address': '192.0.2.20/24', 'gateway': '198.51.100.1', 'dns': ['1.1.1.1'],
                'hostname': 'test', 'mac': '00:11:22:33:44:55'})

    def test_ram_boot_uses_observed_nic_and_static_network(self):
        network = {'address': '192.0.2.20/24', 'gateway': '192.0.2.1', 'dns': ['1.1.1.1'],
                   'hostname': 'test', 'mac': '00:11:22:33:44:55'}
        live = {'nics': [{'name': 'eno3', 'mac': network['mac']}]}
        args = ram_iso_arguments(network, live, 'http://192.0.2.2:8080/token/ubuntu.iso')
        self.assertIn('ip=192.0.2.20::192.0.2.1:255.255.255.0:test:eno3:none', args)
        self.assertIn('BOOTIF=01-00-11-22-33-44-55', args)
        with self.assertRaises(ValueError):
            ram_iso_arguments(network, {'nics': []}, 'http://192.0.2.2:8080/token/ubuntu.iso')


class MediaTests(unittest.TestCase):
    def test_next_windows_boot_uses_disk_without_ejecting_active_iso(self):
        class Ilo:
            system_path = '/redfish/v1/Systems/1/'
            boot = {'BootSourceOverrideSupported': ['Hdd', 'Cd'],
                    'BootSourceOverrideEnabled': 'Disabled', 'BootSourceOverrideTarget': 'None'}
            def __init__(self):
                self.patches = []
            def get(self, path):
                return {'Boot': self.boot.copy()}
            def request(self, method, path, body):
                self.patches.append(body)
                self.boot.update(body['Boot'])
        ilo = Ilo()
        prefer_hard_disk_on_next_boot(ilo)
        self.assertEqual(ilo.boot['BootSourceOverrideTarget'], 'Hdd')
        self.assertEqual(ilo.boot['BootSourceOverrideEnabled'], 'Once')
        prefer_hard_disk_on_next_boot(ilo)
        self.assertEqual(len(ilo.patches), 1)

    def test_range_and_path_isolation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            iso = root / 'test.iso'
            iso.write_bytes(bytes(range(256)) * 10)
            server = MediaServer(iso, '127.0.0.1', 0, 'test-token', root / 'http.log')
            server.start()
            url = f'http://127.0.0.1:{server.server.server_port}/test-token/ubuntu.iso'
            try:
                response = urllib.request.urlopen(urllib.request.Request(url, headers={'Range': 'bytes=2048-2051'}))
                self.assertEqual(response.status, 206)
                self.assertEqual(response.read(), bytes(range(4)))
                self.assertEqual(response.headers['Content-Range'], 'bytes 2048-2051/2560')
                # HTTP reader can finish before the server thread updates its counter.
                import time
                deadline = time.monotonic() + 1
                while server.bytes_served < 4 and time.monotonic() < deadline:
                    time.sleep(0.01)
                self.assertEqual(server.bytes_served, 4)
                with self.assertRaises(urllib.error.HTTPError) as context:
                    urllib.request.urlopen(url.replace('ubuntu.iso', '../config/test.yaml'))
                self.assertEqual(context.exception.code, 404)
            finally:
                server.close()


class VerificationTests(unittest.TestCase):
    def setUp(self):
        self.config = {'network': {'address': '192.0.2.20/24', 'gateway': '192.0.2.1', 'hostname': 'test'},
                       'identity': {'username': 'provision'}}
        self.evidence = {'os': 'ID=ubuntu\nVERSION_ID="24.04"\n', 'hostname': 'test',
            'ip': [{'addr_info': [{'local': '192.0.2.20'}]}],
            'route': [{'dst': 'default', 'gateway': '192.0.2.1'}],
            'mounts': {'filesystems': []}, 'disks': {'blockdevices': []},
            'marker': {'run_id': 'expected-run'}, 'boot_id': 'installed-boot'}
        self.completion = {'host_key': 'ssh-ed25519 test-fixture', 'installer_boot_id': 'live-boot'}

    def execute(self):
        with tempfile.TemporaryDirectory() as folder, patch('src.verifier.socket.create_connection'), \
                patch('src.verifier.subprocess.run') as ssh:
            ssh.return_value = MagicMock(returncode=0, stdout=json.dumps(self.evidence))
            return verify(self.config, Path('unused-test-key'), Path(folder) / 'known_hosts',
                          self.completion, 'expected-run', timeout=1)

    def test_success_requires_matching_installation(self):
        self.assertTrue(all(self.execute()['checks'].values()))

    def test_reachable_ssh_wrong_run_is_not_success(self):
        self.evidence['marker']['run_id'] = 'older-installation'
        with self.assertRaises(RuntimeError):
            self.execute()

    def test_same_boot_is_not_success(self):
        self.evidence['boot_id'] = 'live-boot'
        with self.assertRaises(RuntimeError):
            self.execute()


if __name__ == '__main__':
    unittest.main()
