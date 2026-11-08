import tempfile
import unittest
from pathlib import Path
from src.os_catalog import source_for, identity, available, detected_media


class OperatingSystemCatalogTests(unittest.TestCase):
    def test_ubuntu_22_media_and_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'ubuntu-22.04.5-live-server-amd64.iso'
            source.touch()
            self.assertEqual(source_for('ubuntu22', Path(directory)), source)
        self.assertEqual(identity({'installation': {'os': 'ubuntu22'}}), ('ubuntu', '22.04'))

    def test_other_distributions_do_not_enter_ubuntu_installer(self):
        with self.assertRaisesRegex(ValueError, 'not yet implemented'):
            source_for('debian12')
        with self.assertRaisesRegex(ValueError, 'not yet implemented'):
            source_for('alma9')
        with self.assertRaisesRegex(ValueError, 'not yet implemented'):
            source_for('windows2022')
        with self.assertRaisesRegex(ValueError, 'not yet implemented'):
            source_for('esxi803')

    def test_other_media_is_detected_without_enabling_ubuntu_installer(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'SERVER_EVAL_x64FRE_en-us.iso').touch()
            (root / 'VMware-ESXi-8.0.3-24022510-HPE-test.iso').touch()
            (root / 'AlmaLinux-9.8-x86_64-minimal.iso').touch()
            (root / 'AlmaLinux-10.2-x86_64-minimal.iso').touch()
            (root / 'debian-11.0.0-amd64-DVD-1.iso').touch()
            (root / 'debian-12.0.0-amd64-netinst.iso').touch()
            (root / 'debian-13.7.0-amd64-netinst.iso').touch()
            self.assertEqual(detected_media(root)['windows2022'], 'SERVER_EVAL_x64FRE_en-us.iso')
            self.assertEqual(detected_media(root)['alma98minimal'], 'AlmaLinux-9.8-x86_64-minimal.iso')
            self.assertEqual(detected_media(root)['alma102minimal'], 'AlmaLinux-10.2-x86_64-minimal.iso')
            self.assertEqual(detected_media(root)['debian11dvd'], 'debian-11.0.0-amd64-DVD-1.iso')
            self.assertEqual(detected_media(root)['debian12netinst'], 'debian-12.0.0-amd64-netinst.iso')
            self.assertEqual(detected_media(root)['debian13netinst'], 'debian-13.7.0-amd64-netinst.iso')
            self.assertEqual(available(root), [])

    def test_all_three_ubuntu_live_media_are_listed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for version in ('22.04.5', '24.04.3', '26.04'):
                (root / f'ubuntu-{version}-live-server-amd64.iso').touch()
            self.assertEqual(set(available(root)), {'ubuntu22', 'ubuntu24', 'ubuntu26'})
            self.assertEqual(identity({'installation': {'os': 'ubuntu26'}}), ('ubuntu', '26.04'))
