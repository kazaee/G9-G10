import unittest
from src.install_hook import bind_verified_disks


class DiskBindingTests(unittest.TestCase):
    def setUp(self):
        self.path = '/dev/disk/by-id/wwn-0x123'
        self.config = {'storage': {'config': [
            {'type': 'disk', 'id': 'disk-0', 'path': self.path},
            {'type': 'partition', 'id': 'efi', 'device': 'disk-0'}]}}

    def test_resolves_verified_id_to_current_boot_path_without_changing_plan(self):
        for config in (self.config, {'autoinstall': self.config}):
            result = bind_verified_disks(config, {self.path: '/dev/sdb'})
            actions = result.get('autoinstall', result)['storage']['config']
            self.assertEqual(actions[0]['path'], '/dev/sdb')
            self.assertEqual(actions[1]['device'], 'disk-0')
        self.assertEqual(self.config['storage']['config'][0]['path'], self.path)

    def test_rejects_unreviewed_missing_and_duplicate_disks(self):
        with self.assertRaises(ValueError):
            bind_verified_disks(self.config, {'/dev/disk/by-id/other': '/dev/sda'})
        with self.assertRaises(ValueError):
            bind_verified_disks(self.config, {self.path: '/dev/sda', '/dev/disk/by-id/other': '/dev/sdb'})
        self.config['storage']['config'].append(dict(self.config['storage']['config'][0]))
        with self.assertRaises(ValueError):
            bind_verified_disks(self.config, {self.path: '/dev/sda'})
