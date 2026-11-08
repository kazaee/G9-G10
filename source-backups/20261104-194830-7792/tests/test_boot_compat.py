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
