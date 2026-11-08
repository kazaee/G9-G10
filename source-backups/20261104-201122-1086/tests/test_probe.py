import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, Mock
from src.live_probe import existing_systems


class ExistingSystemTests(unittest.TestCase):
    def test_inspects_old_os_readonly_without_disclosing_password_hashes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'usr/lib').mkdir(parents=True)
            (root / 'etc/netplan').mkdir(parents=True)
            (root / 'usr/lib/os-release').write_text('ID=ubuntu\nVERSION_ID="24.04"\n')
            (root / 'etc/hostname').write_text('existing-server\n')
            (root / 'etc/passwd').write_text('root:x:0:0:root:/root:/bin/bash\nadmin:x:1000:1000:Admin:/home/admin:/bin/bash\n')
            (root / 'etc/shadow').write_text('root:!:0:0:0:0:0:0:\nadmin:$6$secret-test-hash:0:0:0:0:0:0:\n')
            (root / 'etc/netplan/network.yaml').write_text('network:\n  ethernets:\n    eno3:\n      dhcp4: true\n')
            disks = [{'name': 'sda2', 'fstype': 'ext4', 'mountpoints': [None]},
                     {'name': 'sdb1', 'fstype': 'VMFS_volume_member', 'mountpoints': [None]}]
            with patch('src.live_probe.run', return_value=Mock(returncode=0)) as run:
                result = existing_systems(disks, root)
            self.assertEqual(run.call_args_list[0].args, ('mount', '-t', 'ext4', '-o', 'ro,noload', '/dev/sda2', str(root)))
            self.assertEqual(run.call_args_list[-1].args, ('umount', str(root)))
            self.assertEqual(len(result), 1)
            self.assertEqual(result[0]['hostname'], 'existing-server')
            self.assertIsNone(result[0]['provision_marker'])
            self.assertEqual(result[0]['accounts'][1]['password_state'], 'password-set')
            self.assertNotIn('secret-test-hash', json.dumps(result))

    def test_mount_failure_is_reported_without_reading_target(self):
        with tempfile.TemporaryDirectory() as directory, patch('src.live_probe.run', return_value=Mock(returncode=1, stderr='mount failed')):
            result = existing_systems([{'name': 'sda2', 'fstype': 'ext4', 'mountpoints': [None]}], Path(directory))
            self.assertEqual(result, [{'device': '/dev/sda2', 'read_error': 'mount failed'}])


if __name__ == '__main__':
    unittest.main()
