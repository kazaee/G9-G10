import base64
import copy
import json
import tempfile
import unittest
import zlib
from pathlib import Path
from unittest.mock import Mock, patch
from app import Application
from src.lo100_clp import Lo100CLP, properties
from src.lo100_diagnostic import live_script
from test_lo100_panel import PROFILE, sample_inventory


class WorkflowTests(unittest.TestCase):
    def app(self):
        app=Application();app.launch=lambda task:task()
        app.lo100_profile=copy.deepcopy(PROFILE)
        app.config['ilo']={'address':'192.0.2.30','username':'ILO'};app.password='test'
        app.discovery={'system':{'Model':'DL160 G6','SerialNumber':'G6-test-serial'},'resources':{},'media':[]}
        app.read_lo100_hardware=Mock()
        app.select_lo100_cd=Mock()
        return app

    def test_no_reboot_without_cache_never_opens_media_or_sends_power(self):
        app=self.app()
        with tempfile.TemporaryDirectory() as d,patch.object(app,'lo100_cache_path',return_value=Path(d)/'absent'),patch('src.lo100.Lo100') as controller:
            app.discover_lo100({})
            self.assertEqual(app.state['stage'],'connected')
            self.assertIsNone(app.live)
            controller.assert_not_called()

    def test_matching_cached_probe_populates_shared_partition_editor_without_boot(self):
        app=self.app();live=sample_inventory();live['inventory_source']='lo100-diagnostic'
        with tempfile.TemporaryDirectory() as d,patch('src.lo100.Lo100') as controller:
            path=Path(d)/'inventory.json';path.write_text(json.dumps(live))
            with patch.object(app,'lo100_cache_path',return_value=path):app.discover_lo100({})
            self.assertEqual(app.state['stage'],'discovered')
            self.assertIn('/dev/disk/by-id/wwn-0x1234',app.state['plan'])
            controller.assert_not_called()

    def test_wrong_serial_cache_is_not_reused(self):
        app=self.app();live=sample_inventory();live['server_serial']='different'
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'inventory.json';path.write_text(json.dumps(live))
            with patch.object(app,'lo100_cache_path',return_value=path):app.discover_lo100({})
            self.assertIsNone(app.live)

    def test_failed_automatic_diagnostic_mount_never_sends_power(self):
        app=self.app()
        with tempfile.TemporaryDirectory() as d,patch.object(app,'run_directory',return_value=Path(d)),patch.object(app,'mount_lo100_automatic',return_value=False),patch('src.lo100_diagnostic.build',side_effect=lambda source,dest,nonce:dest),patch('src.os_catalog.source_for'),patch('src.lo100_workflow.Lo100') as controller:
            app.discover_lo100({'confirm_reboot':True})
            controller.assert_not_called()
            self.assertIn('probe_id',app.lo100_diagnostic)

    def test_manual_discovery_requires_pending_image(self):
        app=self.app()
        with tempfile.TemporaryDirectory() as d,patch.object(app,'lo100_cache_path',return_value=Path(d)/'cache'),patch('src.lo100_workflow.Lo100') as controller:
            with self.assertRaises(ValueError):app.discover_lo100({'confirm_reboot':True,'lo100_diagnostic_media_ready':True})
            controller.assert_not_called()

    def test_diagnostic_script_is_syntax_valid_and_read_only(self):
        source=live_script('unique-probe')
        compile(source,'probe','exec')
        self.assertIn("result['probe_id']='unique-probe'",source)
        self.assertIn("'inventory_source':'lo100-diagnostic'",source)
        self.assertNotIn('mkfs',source);self.assertNotIn('wipefs',source)
        self.assertIn('while True:',source)
        self.assertIn('/dev/ttyS0',source);self.assertIn('/dev/ttyS1',source)

    def test_wrong_nonce_serial_report_is_ignored(self):
        from src.live_report import from_serial
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'serial.log'
            raw=json.dumps({'event':'live-inventory','probe_id':'previous'}).encode()
            path.write_text('INVENTORY_ZLIB_BASE64='+base64.b64encode(zlib.compress(raw)).decode())
            self.assertIsNone(from_serial(path,'current'))

    def test_clp_rejects_power_commands(self):
        cli=Lo100CLP('192.0.2.30','test','test')
        for path in ('/system1; reset','/system1/console1\nstop','/map1/accounts1'):
            with self.assertRaises(ValueError):cli.show(path)

    def test_clp_properties_never_include_license(self):
        self.assertEqual(properties('oemhp_license_key=secret\noemhp_serial_number=serial'),{'oemhp_serial_number':'serial'})

    def test_background_discovery_prevents_prepare_or_target_switch(self):
        app=self.app();app.state['stage']='discovering'
        with self.assertRaises(ValueError):app.prepare_lo100({})
        with self.assertRaises(ValueError):app.connect({})

    def test_diagnostic_boot_captures_before_single_reset_even_when_response_is_uncertain(self):
        from src.lo100 import BootRequestUncertain
        app=self.app()
        order=[]
        app.select_lo100_cd.side_effect=lambda confirmation:order.append('select-cd')
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);image=root/'probe.iso';image.write_bytes(b'test')
            app.lo100_diagnostic={'target':'192.0.2.30','iso':str(image),'probe_id':'nonce','run':d}
            with patch('src.lo100_clp.Lo100CLP') as capture,patch('src.lo100_workflow.Lo100') as controller,patch('src.lo100_workflow.threading.Thread') as watcher:
                capture.return_value.capture_serial.side_effect=lambda path:order.append('capture')
                def boot(**kwargs):
                    order.append('boot');raise BootRequestUncertain()
                controller.return_value.boot_install.side_effect=boot
                app.discover_lo100({'confirm_reboot':True,'lo100_diagnostic_media_ready':True})
                self.assertEqual(order,['capture','select-cd','boot'])
                controller.return_value.boot_install.assert_called_once()
                watcher.return_value.start.assert_called_once()
                self.assertEqual(app.state['stage'],'discovering')

    def test_console_credentials_cannot_inject_terminal_commands(self):
        with self.assertRaises(ValueError):Lo100CLP('192.0.2.30','ILO','pass\nreset')

    def test_boot_override_failure_prevents_diagnostic_reset(self):
        app=self.app();app.select_lo100_cd.side_effect=RuntimeError('readback failed')
        with tempfile.TemporaryDirectory() as d:
            image=Path(d)/'probe.iso';image.write_bytes(b'test')
            app.lo100_diagnostic={'target':'192.0.2.30','iso':str(image),'probe_id':'nonce','run':d}
            with patch('src.lo100_clp.Lo100CLP') as capture,patch('src.lo100_workflow.Lo100') as controller:
                with self.assertRaisesRegex(RuntimeError,'readback failed'):
                    app.discover_lo100({'confirm_reboot':True,'lo100_diagnostic_media_ready':True})
                controller.return_value.boot_install.assert_not_called()
                capture.return_value.close.assert_called_once()

    def test_legacy_uuid_byte_order_still_requires_exact_chassis_serial(self):
        import uuid
        app=self.app();live=sample_inventory()
        live['system_uuid']=str(uuid.UUID(bytes_le=uuid.UUID(PROFILE['system_guid']).bytes))
        app.accept_lo100_inventory(live)
        self.assertEqual(app.state['stage'],'discovered')
        live['server_serial']='another-chassis'
        with self.assertRaisesRegex(ValueError,'serial number'):app.accept_lo100_inventory(live)
