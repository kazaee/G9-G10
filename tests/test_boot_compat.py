import copy
import unittest
from unittest.mock import patch
from src.virtual_media import boot_once, prefer_hard_disk_on_next_boot


class Controller:
    system_path = '/system'

    def __init__(self, *, targets=None, devices=None, vendor=None, retain=True):
        self.system = {'PowerState': 'Off', 'Boot': {
            'BootSourceOverrideTarget@Redfish.AllowableValues': targets if targets is not None else ['Cd', 'Hdd', 'UefiTarget'],
            'UefiTargetBootSourceOverride@Redfish.AllowableValues': devices or []}}
        self.media = {'Inserted': True, 'Image': 'http://example.test/discovery.iso'}
        if vendor:
            self.media['Oem'] = {vendor: {'BootOnNextServerReset': False}}
        self.retain, self.patches, self.resets = retain, [], []

    def get(self, path):
        return copy.deepcopy(self.media if path == '/cd' else self.system)

    def request(self, method, path, body):
        self.patches.append((path, copy.deepcopy(body)))
        if not self.retain:
            return
        if path == '/cd':
            for vendor, values in body['Oem'].items():
                self.media['Oem'][vendor].update(values)
        else:
            self.system['Boot'].update(body['Boot'])

    def reset(self, kind):
        self.resets.append(kind)


class BootCompatibilityTests(unittest.TestCase):
    def mapped_controller(self, *, advertised=True, retain=True):
        class Gen10(Controller):
            def get(self, path):
                if path == '/bios':
                    return {'Oem': {'Hpe': {'Links': {'Boot': {'@odata.id': '/boot-sources'}}}}}
                if path == '/boot-sources':
                    return {'BootSources': [
                        {'StructuredBootString': 'HD.SD.1.2', 'UEFIDevicePath': 'sd-path'},
                        {'StructuredBootString': 'CD.Virtual.3.1', 'UEFIDevicePath': 'virtual-cd-path'}]}
                return super().get(path)
        ilo = Gen10(devices=['sd-path', 'virtual-cd-path'] if advertised else ['sd-path'],
                    vendor='Hpe', retain=retain)
        ilo.system['Bios'] = {'@odata.id': '/bios'}
        return ilo

    def test_gen10_maps_virtual_cd_3_1_to_advertised_device_path(self):
        ilo = self.mapped_controller()
        boot_once(ilo, '/cd')
        self.assertEqual(ilo.system['Boot']['UefiTargetBootSourceOverride'], 'virtual-cd-path')
        self.assertEqual(ilo.system['Boot']['BootSourceOverrideTarget'], 'UefiTarget')
        self.assertEqual(ilo.resets, ['On'])

    def test_device_path_not_advertised_is_never_patched(self):
        ilo = self.mapped_controller(advertised=False)
        boot_once(ilo, '/cd')
        self.assertEqual(ilo.system['Boot']['BootSourceOverrideTarget'], 'Cd')
        self.assertFalse(any('UefiTargetBootSourceOverride' in body.get('Boot', {}) for _, body in ilo.patches))

    def test_any_advertised_structured_virtual_cd_port_is_supported(self):
        ilo = Controller(devices=['CD.Virtual.3.1'])
        boot_once(ilo, '/cd', require_virtual_cd=True)
        self.assertEqual(ilo.system['Boot']['UefiTargetBootSourceOverride'], 'CD.Virtual.3.1')

    def test_changed_exact_device_after_final_patch_prevents_power_on(self):
        ilo = self.mapped_controller()
        original = ilo.request
        def request(method, path, body):
            original(method, path, body)
            if body.get('Boot', {}).get('BootSourceOverrideTarget') == 'UefiTarget':
                ilo.system['Boot']['UefiTargetBootSourceOverride'] = 'sd-path'
        ilo.request = request
        with self.assertRaisesRegex(RuntimeError, 'changed the selected'):
            boot_once(ilo, '/cd')
        self.assertEqual(ilo.resets, [])

    def test_diagnostic_post_lock_recovers_with_one_off_on_cycle(self):
        class PostController(Controller):
            def request(self, method, path, body):
                if path == '/cd' and self.system['PowerState'] == 'On':
                    raise RuntimeError('HTTP 400 iLO.2.25.UnableToModifyDuringSystemPOST')
                return super().request(method, path, body)
            def reset(self, kind):
                super().reset(kind)
                self.system['PowerState'] = 'Off' if kind == 'ForceOff' else 'On'
        ilo = PostController(vendor='Hpe')
        ilo.system['PowerState'] = 'On'
        boot_once(ilo, '/cd', allow_post_power_cycle=True)
        self.assertEqual(ilo.resets, ['ForceOff', 'On'])
        self.assertTrue(ilo.media['Oem']['Hpe']['BootOnNextServerReset'])

    def test_post_lock_without_diagnostic_authorization_never_powers_off(self):
        ilo = Controller(vendor='Hpe')
        with patch.object(ilo, 'request', side_effect=RuntimeError('iLO.2.25.UnableToModifyDuringSystemPOST')):
            with self.assertRaisesRegex(RuntimeError, 'No automatic power-off'):
                boot_once(ilo, '/cd')
        self.assertEqual(ilo.resets, [])

    def test_other_400_errors_do_not_trigger_power_recovery(self):
        ilo = Controller(vendor='Hpe')
        with patch.object(ilo, 'request', side_effect=RuntimeError('HTTP 400 PropertyValueNotInList')):
            with self.assertRaisesRegex(RuntimeError, 'PropertyValueNotInList'):
                boot_once(ilo, '/cd', allow_post_power_cycle=True)
        self.assertEqual(ilo.resets, [])

    def test_post_recovery_does_not_loop_when_retry_fails(self):
        ilo = Controller(vendor='Hpe')
        with patch.object(ilo, 'request', side_effect=RuntimeError('iLO.2.25.UnableToModifyDuringSystemPOST')) as request:
            with self.assertRaisesRegex(RuntimeError, 'UnableToModifyDuringSystemPOST'):
                boot_once(ilo, '/cd', allow_post_power_cycle=True)
        self.assertEqual(request.call_count, 2)
        self.assertEqual(ilo.resets, [])

    def test_post_recovery_must_confirm_off_before_retrying(self):
        ilo = Controller(vendor='Hpe')
        ilo.system['PowerState'] = 'On'
        with patch.object(ilo, 'request', side_effect=RuntimeError('iLO.2.25.UnableToModifyDuringSystemPOST')) as request, patch('src.virtual_media.time.monotonic', side_effect=[0, 61]):
            with self.assertRaisesRegex(RuntimeError, 'timed out waiting for power Off'):
                boot_once(ilo, '/cd', allow_post_power_cycle=True)
        self.assertEqual(request.call_count, 1)
        self.assertEqual(ilo.resets, ['ForceOff'])

    def test_ilo5_standard_cd_powers_on_once(self):
        ilo = Controller()
        boot_once(ilo, '/cd')
        self.assertEqual(ilo.system['Boot']['BootSourceOverrideTarget'], 'Cd')
        self.assertEqual(ilo.system['Boot']['BootSourceOverrideEnabled'], 'Once')
        self.assertEqual(ilo.resets, ['On'])

    def test_ilo5_advertised_exact_virtual_device(self):
        ilo = Controller(devices=['CD.Virtual.2.1'])
        boot_once(ilo, '/cd', require_virtual_cd=True)
        self.assertEqual(ilo.system['Boot']['BootSourceOverrideTarget'], 'UefiTarget')
        self.assertEqual(ilo.system['Boot']['UefiTargetBootSourceOverride'], 'CD.Virtual.2.1')
        self.assertEqual(ilo.resets, ['On'])

    def test_ilo5_hpe_media_flag_for_strict_virtual_cd(self):
        ilo = Controller(vendor='Hpe')
        boot_once(ilo, '/cd', require_virtual_cd=True)
        self.assertTrue(ilo.media['Oem']['Hpe']['BootOnNextServerReset'])
        self.assertEqual(ilo.system['Boot']['BootSourceOverrideTarget'], 'Cd')
        self.assertEqual(ilo.resets, ['On'])

    def test_ilo5_hpe_media_flag_is_also_set_for_diagnostic_boot(self):
        ilo = Controller(vendor='Hpe')
        boot_once(ilo, '/cd')
        self.assertEqual(ilo.patches[0], ('/cd', {'Oem': {'Hpe': {'BootOnNextServerReset': True}}}))
        self.assertTrue(ilo.media['Oem']['Hpe']['BootOnNextServerReset'])
        self.assertEqual(ilo.resets, ['On'])

    def test_diagnostic_media_flag_must_be_retained(self):
        ilo = Controller(vendor='Hpe', retain=False)
        with self.assertRaisesRegex(RuntimeError, 'boot-on-next-reset'):
            boot_once(ilo, '/cd')
        self.assertEqual(ilo.resets, [])

    def test_ilo5_hdd_override(self):
        ilo = Controller()
        prefer_hard_disk_on_next_boot(ilo)
        self.assertEqual(ilo.system['Boot']['BootSourceOverrideTarget'], 'Hdd')
        self.assertEqual(ilo.system['Boot']['BootSourceOverrideEnabled'], 'Once')
        self.assertEqual(ilo.resets, [])

    def test_standard_values_take_precedence_over_legacy(self):
        ilo = Controller(targets=['Hdd'])
        ilo.system['Boot']['BootSourceOverrideSupported'] = ['Cd']
        with self.assertRaisesRegex(RuntimeError, 'does not advertise'):
            boot_once(ilo, '/cd')
        self.assertEqual(ilo.patches, [])
        self.assertEqual(ilo.resets, [])

    def test_explicit_empty_values_do_not_use_legacy(self):
        ilo = Controller(targets=[])
        ilo.system['Boot']['BootSourceOverrideSupported'] = ['Cd']
        with self.assertRaises(RuntimeError):
            boot_once(ilo, '/cd')
        self.assertEqual(ilo.resets, [])

    def test_unretained_override_does_not_power_on(self):
        ilo = Controller(retain=False)
        with self.assertRaisesRegex(RuntimeError, 'did not retain'):
            boot_once(ilo, '/cd')
        self.assertEqual(ilo.resets, [])

    def test_unretained_hpe_media_flag_does_not_power_on(self):
        ilo = Controller(vendor='Hpe', retain=False)
        with self.assertRaisesRegex(RuntimeError, 'boot-on-next-reset'):
            boot_once(ilo, '/cd', require_virtual_cd=True)
        self.assertEqual(ilo.resets, [])

    def test_missing_media_does_not_change_boot_or_power(self):
        ilo = Controller()
        ilo.media['Inserted'] = False
        with self.assertRaisesRegex(RuntimeError, 'not inserted'):
            boot_once(ilo, '/cd')
        self.assertEqual(ilo.patches, [])
        self.assertEqual(ilo.resets, [])

    def test_console_media_can_boot_without_an_image_url(self):
        ilo = Controller(vendor='Hpe')
        ilo.media.update(Image=None, ConnectedVia='Applet')
        boot_once(ilo, '/cd', require_virtual_cd=True, local_console=True)
        self.assertEqual(ilo.resets, ['On'])
        self.assertEqual(ilo.system['Boot']['BootSourceOverrideTarget'], 'Cd')
        self.assertFalse(any(path == '/cd' for path, _ in ilo.patches))

    def test_console_mode_rejects_uri_media_before_any_power_change(self):
        ilo = Controller(vendor='Hpe')
        ilo.media['ConnectedVia'] = 'URI'
        with self.assertRaisesRegex(RuntimeError, 'HTML5 console'):
            boot_once(ilo, '/cd', local_console=True, allow_post_power_cycle=True)
        self.assertEqual(ilo.resets, [])
        self.assertEqual(ilo.patches, [])

    def test_url_mode_does_not_accept_missing_url_on_console_media(self):
        ilo = Controller()
        ilo.media.update(Image=None, ConnectedVia='Applet')
        with self.assertRaisesRegex(RuntimeError, 'not inserted'):
            boot_once(ilo, '/cd')
        self.assertEqual(ilo.resets, [])
