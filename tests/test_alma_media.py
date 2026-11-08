import unittest

from src.alma_media import kickstart
from src.storage import recommend


class AlmaMediaTests(unittest.TestCase):
    def setUp(self):
        self.disk = {'path': '/dev/disk/by-id/wwn-0x1234', 'size': 1000 * 1024**3,
                     'media': 'SSD', 'bus': 'RAID'}
        self.inventory = {'installable': [self.disk]}
        self.plan = recommend(self.inventory)
        self.network = {'address': '192.0.2.10/24', 'gateway': '192.0.2.1',
                        'dns': ['192.0.2.53'], 'hostname': 'alma-test',
                        'mac': 'aa:bb:cc:dd:ee:ff'}
        self.identity = {'password_hash': '$6$salt1234$' + 'x' * 86, 'ssh_port': 2222}

    def test_reviewed_disk_network_and_custom_ssh_are_in_kickstart(self):
        script = kickstart(self.plan, self.inventory, self.network, self.identity,
                           'a' * 32, 'http://192.0.2.20:8766/token/event')
        self.assertIn('ignoredisk --only-use=disk/by-id/wwn-0x1234', script)
        self.assertIn('clearpart --all --initlabel --drives=disk/by-id/wwn-0x1234', script)
        self.assertIn('part / --fstype=ext4 --size=1 --grow --ondisk=disk/by-id/wwn-0x1234', script)
        self.assertIn('network --device=aa:bb:cc:dd:ee:ff --bootproto=static', script)
        self.assertIn('semanage port -a -t ssh_port_t -p tcp 2222', script)
        self.assertIn('PROVISION_ALMA_BOOT=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa', script)
        self.assertIn('ALMA_FIRSTBOOT', script)
        self.assertIn('for console in /dev/ttyS0 /dev/ttyS1; do', script)
        self.assertIn('test -c "$console"', script)
        self.assertNotIn('zerombr', script)

    def test_rejects_lvm_before_any_media_is_mounted(self):
        self.plan['lvm'] = [{'name': 'root', 'vg': 'system', 'size': -1,
                             'filesystem': 'ext4', 'mount': '/'}]
        with self.assertRaisesRegex(ValueError, 'direct UEFI partitions only'):
            kickstart(self.plan, self.inventory, self.network, self.identity,
                      'a' * 32, 'http://192.0.2.20:8766/token/event')
