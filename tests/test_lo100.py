import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import Mock, patch
from src.lo100 import Lo100, Page, parse_summary, console_command

SUMMARY = '<html><img src="lights-out100_rgb.jpg">IPMI Version: 2.0 Firmware Version: 4.26 Hardware Version: 1.0 Description: DL160 G6 System GUID: abc</html>'


class Lo100Tests(unittest.TestCase):
    def test_identifies_actual_legacy_controller_without_claiming_redfish(self):
        profile = parse_summary(SUMMARY)
        self.assertEqual(profile['firmware'], '4.26')
        self.assertEqual(profile['model'], 'DL160 G6')
        self.assertFalse(profile['capabilities']['automatic_power'])
        self.assertFalse(profile['capabilities']['redfish'])

    def test_modern_ilo_is_never_misclassified_as_lo100(self):
        with self.assertRaises(ValueError):
            parse_summary('<title>HPE iLO 5</title>')

    def test_only_read_only_resource_paths_are_allowed(self):
        controller = Lo100('172.20.224.30', 'ILO', 'test')
        with self.assertRaises(ValueError):
            controller.read('/chassis.html?power=off')

    def test_credentials_cannot_be_embedded_in_address(self):
        with self.assertRaises(ValueError):
            Lo100('http://user:password@host/', 'ILO', 'test')

    def test_standalone_vendor_jar_needs_no_applet_or_security_policy(self):
        content = io.BytesIO()
        with zipfile.ZipFile(content, 'w') as archive:
            archive.writestr('com/serverengines/mahogany/MahoganyViewer.class', b'test')
            archive.writestr('META-INF/MANIFEST.MF', 'Main-Class: com.serverengines.mahogany.MahoganyViewer')
        controller = Lo100('172.20.224.30', 'ILO', 'test')
        controller.read = Mock(return_value=content.getvalue())
        with tempfile.TemporaryDirectory() as directory:
            jar = controller.prepare_console(Path(directory))
            with patch('src.lo100.java8', return_value='java8'):
                args = console_command(jar)
            self.assertEqual(args, ['java8', '-jar', str(jar)])
            self.assertEqual([p.resolve() for p in Path(directory).iterdir()], [jar])
        controller.read.assert_called_once_with('/M2.JAR', 12000000)
        self.assertFalse(hasattr(controller, 'reset'))

    def test_arbitrary_jar_is_not_accepted_as_vendor_console(self):
        from src.lo100 import validate_console_jar
        content = io.BytesIO()
        with zipfile.ZipFile(content, 'w') as archive:
            archive.writestr('other.class', b'test')
        with self.assertRaises(ValueError):
            validate_console_jar(content.getvalue())

    def test_ubuntu_preparation_never_selects_a_disk_without_linux_inventory(self):
        import lo100
        import yaml
        catalog = Mock(); catalog.validation_entry.platform_id = 0; catalog.initial_entry.boot_indicator = 0x88
        source = Mock(); source.eltorito_boot_catalog = catalog
        source.pvd.volume_identifier = b'Ubuntu-Server 24.04.3 LTS amd64'
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / 'prepared'
            with patch('pycdlib.PyCdlib', return_value=source), patch('src.iso.build') as build, patch('src.credentials.password_hash', return_value='$6$hash'):
                lo100.prepare_ubuntu(Path('source.iso'), destination, 'ubuntu-g6', 'test-password-123')
                seed = yaml.safe_load((destination / 'user-data').read_text())['autoinstall']
                self.assertEqual(seed['interactive-sections'], ['storage', 'network'])
                self.assertNotIn('storage', seed)
                self.assertEqual(seed['identity']['username'], 'provision')
                self.assertTrue(build.call_args.kwargs['graphical_console_only'])

    def test_legacy_bios_is_mandatory_for_this_path(self):
        import lo100
        source = Mock(); source.eltorito_boot_catalog = None
        with tempfile.TemporaryDirectory() as directory, patch('pycdlib.PyCdlib', return_value=source), patch('src.iso.build') as build:
            with self.assertRaisesRegex(ValueError, 'Legacy BIOS'):
                lo100.prepare_ubuntu('source.iso', Path(directory) / 'prepared', 'ubuntu-g6', 'test-password-123')
            build.assert_not_called()

    def test_graphics_option_does_not_change_default_ilo_grub(self):
        from src.iso import build
        for graphics_only in [False, True]:
            iso, verify = Mock(), Mock()
            iso.get_file_from_iso_fp.side_effect = lambda fp, **kwargs: fp.write(b'old md5 entry')
            verify.get_file_from_iso_fp.side_effect = lambda fp, **kwargs: fp.write(b'#cloud-config')
            section = Mock(); section.platform_id = 0xef
            verify.eltorito_boot_catalog.sections = [section]
            with patch('src.iso.pycdlib.PyCdlib', side_effect=[iso, verify]), patch('builtins.print'):
                build('source.iso', 'target.iso', '#cloud-config', graphical_console_only=graphics_only)
            grub = next(call.args[0].getvalue().decode() for call in iso.add_fp.call_args_list
                        if call.kwargs['iso_path'] == '/BOOT/GRUB/GRUB.CFG;1')
            self.assertIn('console=tty0', grub)
            self.assertEqual('console=ttyS1,115200n8' in grub, not graphics_only)

    def test_legacy_get_retries_only_connection_failure(self):
        controller = Lo100('172.20.224.30', 'ILO', 'test')
        response = Mock(); response.url = 'http://172.20.224.30/index.html'; response.read.return_value = b'result'
        context = Mock(); context.__enter__ = Mock(return_value=response); context.__exit__ = Mock(return_value=False)
        controller.opener = Mock(); controller.opener.open.side_effect = [ConnectionResetError(), context]
        with patch('src.lo100.time.sleep'):
            self.assertEqual(controller.read('/index.html'), b'result')
        self.assertEqual(controller.opener.open.call_count, 2)

    def test_full_plan_uses_panel_storage_without_interactive_sections(self):
        from src.lo100_ubuntu import prepare_ubuntu
        from src.storage import recommend
        import yaml
        disk={'path':'/dev/disk/by-id/wwn-test','size':100*1024**3,'model':'SSD','bus':'scsi','media':'SSD'}
        inv={'installable':[disk],'uefi':False}
        plan=recommend(inv,False)
        catalog=Mock();catalog.validation_entry.platform_id=0;catalog.initial_entry.boot_indicator=0x88
        source=Mock();source.eltorito_boot_catalog=catalog;source.pvd.volume_identifier=b'Ubuntu-Server 24.04.3 LTS amd64'
        with tempfile.TemporaryDirectory() as directory,patch('pycdlib.PyCdlib',return_value=source),patch('src.iso.build') as build,patch('src.credentials.password_hash',return_value='$6$hash'):
            output=Path(directory)/'prepared'
            prepare_ubuntu('source.iso',output,'ubuntu-g6','test-password-123',storage_plan=plan,
                           live_inventory=inv,network_settings={'deferred':True},system_guid='8156aeda-8dfe-d511-8553-f4ce46866fde')
            config=yaml.safe_load((output/'user-data').read_text())['autoinstall']
            self.assertNotIn('interactive-sections',config)
            self.assertTrue(config['storage']['config'])
            self.assertEqual(config['network'],{'version':2,'ethernets':{}})
            self.assertEqual(config['shutdown'],'poweroff')
            guard=build.call_args.kwargs['extra']['lo100-verify.py']
            compile(guard,'<runtime-guard>','exec')
            self.assertIn('product_uuid',guard)
            self.assertIn('wwn-test',guard)
