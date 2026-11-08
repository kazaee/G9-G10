import tempfile
import unittest
from pathlib import Path

import pycdlib

from src.debian_media import build, preseed, report_script


class DebianMediaTests(unittest.TestCase):
    def setUp(self):
        self.path = '/dev/disk/by-id/wwn-0x123456'
        self.plan = {'boot_mode': 'uefi', 'boot_disk': self.path, 'lvm': [],
                     'disks': [{'path': self.path, 'ptable': 'gpt', 'partitions': [
                         {'name': 'efi', 'size': '1G', 'filesystem': 'fat32',
                          'mount': '/boot/efi', 'flag': 'boot'},
                         {'name': 'boot', 'size': '2G', 'filesystem': 'ext4', 'mount': '/boot'},
                         {'name': 'swap', 'size': '8G', 'filesystem': 'swap'},
                         {'name': 'root', 'size': -1, 'filesystem': 'ext4', 'mount': '/'}]}]}
        self.inventory = {'installable': [{'path': self.path, 'size': 100 * 1024**3}]}
        self.network = {'address': '192.0.2.10/24', 'gateway': '192.0.2.1',
                        'dns': ['192.0.2.53'], 'hostname': 'debian-test',
                        'domain': 'example.test', 'mac': '8c:dc:d4:b7:e3:09'}
        self.identity = {'password_hash': '$6$salt$' + 'x' * 86, 'ssh_port': 2222}

    def test_preseed_targets_one_stable_disk_and_configures_root_ssh(self):
        result = preseed(self.plan, self.inventory, self.network, self.identity, 'a' * 32)
        self.assertIn('readlink -f ' + self.path, result)
        self.assertIn('partman/early_command string disk=$(readlink -f ' + self.path, result)
        self.assertIn('netcfg/choose_interface select 8c:dc:d4:b7:e3:09', result)
        self.assertNotIn('preseed/early_command', result)
        self.assertNotIn('${', result)
        self.assertIn('netcfg/get_ipaddress string 192.0.2.10', result)
        self.assertIn('partman-auto/expert_recipe string provision ::', result)
        self.assertIn('mountpoint{ / }', result)
        self.assertIn('Port 2222', result)
        self.assertIn('passwd/make-user boolean false', result)
        self.assertIn('pkgsel/include string openssh-server python3', result)

    def test_preseed_refuses_multiple_disks(self):
        self.plan['disks'].append({'path': '/dev/disk/by-id/wwn-other', 'ptable': 'gpt',
                                   'partitions': []})
        with self.assertRaisesRegex(ValueError, 'two-disk plan is not RAID or LVM'):
            preseed(self.plan, self.inventory, self.network, self.identity, 'a' * 32)

    def test_first_boot_report_is_tied_to_run_id(self):
        report = report_script('b' * 32, 'http://192.0.2.5:8080/token/event', 2222)
        self.assertIn('DEBIAN_FIRSTBOOT', report)
        self.assertIn('b' * 32, report)
        self.assertIn(':2222 ', report)
        self.assertIn('urllib.request.Request', report)

    def test_remaster_retains_uefi_entry_and_embeds_preseed(self):
        source = Path(__file__).resolve().parents[1] / 'ISO/debian-13.7.0-amd64-netinst.iso'
        if not source.exists():
            self.skipTest('Debian test ISO is absent')
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'debian.iso'
            build(source, output, 'd-i debian-installer/locale string en_US.UTF-8\n', '#!/bin/sh\nexit 0\n')
            iso = pycdlib.PyCdlib()
            iso.open(str(output))
            try:
                self.assertTrue(any(s.platform_id == 0xef for s in iso.eltorito_boot_catalog.sections))
                iso.get_record(rr_path='/report.sh')
                iso.get_record(rr_path='/report.srv')
                import io
                grub = io.BytesIO()
                iso.get_file_from_iso_fp(grub, rr_path='/boot/grub/grub.cfg')
                self.assertIn(b'locale=en_US.UTF-8 language=en country=US keymap=us', grub.getvalue())
                self.assertIn(b'preseed/file=/cdrom/preseed.cfg', grub.getvalue())
            finally:
                iso.close()


if __name__ == '__main__':
    unittest.main()
