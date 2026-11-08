import unittest
import xml.etree.ElementTree as ET
import tempfile
from pathlib import Path
from urllib.request import Request, urlopen

from src.esxi_media import kickstart, generate_root_password, validate_root_password
from src.windows_unattend import render
from src.windows_boot_media import no_prompt_patch
from src.virtual_media import MediaServer, boot_once


DISK = {'path': '/dev/disk/by-id/wwn-0x600508b1001c6d005714f698ca5f0135',
        'wwn': '0x600508b1001c6d005714f698ca5f0135', 'size': 1000171331584}
OTHER = {'path': '/dev/disk/by-id/wwn-0x600508b1001cb60bf3476b74719bc055',
         'wwn': '0x600508b1001cb60bf3476b74719bc055', 'size': 4000752599040}


class MediaPlans(unittest.TestCase):
    def test_esxi_password_generation_meets_installer_character_policy(self):
        for _ in range(100):
            password = generate_root_password()
            validate_root_password(password)
            self.assertLess(len(password), 40)
        with self.assertRaisesRegex(ValueError, 'character types'):
            validate_root_password('deadbeef0123456789deadbeef01234567')

    def test_gen9_boot_targets_virtual_cd_instead_of_generic_cd(self):
        class FakeIlo:
            system_path = '/system'
            def __init__(self):
                self.media = {'Inserted': True, 'Image': 'http://example/installer.iso'}
                self.system = {'Boot': {'BootSourceOverrideSupported': ['Cd', 'UefiTarget'],
                                        'UefiTargetBootSourceOverrideSupported': ['CD.Virtual.2.1']},
                               'PowerState': 'On'}
                self.resets = []
                self.patches = []
            def get(self, path):
                return self.media if path == '/cd' else self.system
            def request(self, method, path, body):
                if body['Boot'].get('BootSourceOverrideTarget') == 'UefiTarget':
                    if self.system['Boot'].get('UefiTargetBootSourceOverride') != 'CD.Virtual.2.1':
                        raise RuntimeError('iLO requires target to be set first')
                self.patches.append(body['Boot'].copy())
                self.system['Boot'].update(body['Boot'])
            def reset(self, kind):
                self.resets.append(kind)
        ilo = FakeIlo()
        boot_once(ilo, '/cd')
        self.assertEqual(ilo.system['Boot']['BootSourceOverrideTarget'], 'UefiTarget')
        self.assertEqual(ilo.system['Boot']['UefiTargetBootSourceOverride'], 'CD.Virtual.2.1')
        self.assertEqual(ilo.patches, [
            {'UefiTargetBootSourceOverride': 'CD.Virtual.2.1'},
            {'BootSourceOverrideEnabled': 'Once', 'BootSourceOverrideTarget': 'UefiTarget'}])
        self.assertEqual(ilo.resets, ['ForceRestart'])

    def test_virtual_cd_target_not_advertised_uses_cd(self):
        class FakeIlo:
            system_path = '/system'
            def __init__(self):
                self.system = {'Boot': {'BootSourceOverrideSupported': ['Cd', 'UefiTarget'],
                                        'UefiTargetBootSourceOverrideSupported': ['Generic.USB.1.1']},
                               'PowerState': 'On'}
                self.patches = []
                self.resets = []
            def get(self, path):
                return {'Inserted': True, 'Image': 'http://example/iso'} if path == '/cd' else self.system
            def request(self, method, path, body):
                self.patches.append(body['Boot'].copy())
                self.system['Boot'].update(body['Boot'])
            def reset(self, kind):
                self.resets.append(kind)
        ilo = FakeIlo()
        boot_once(ilo, '/cd')
        self.assertEqual(ilo.patches, [{'BootSourceOverrideEnabled': 'Once', 'BootSourceOverrideTarget': 'Cd'}])
        self.assertEqual(ilo.resets, ['ForceRestart'])

    def test_alma_boot_uses_ilo_virtual_media_flag_with_answer_usb(self):
        class FakeIlo:
            system_path = '/system'
            log = None
            def __init__(self):
                self.media = {'Inserted': True, 'Image': 'http://example/alma.iso',
                              'Oem': {'Hp': {'BootOnNextServerReset': False}}}
                self.system = {'PowerState': 'On', 'Boot': {
                    'BootSourceOverrideSupported': ['Cd', 'UefiTarget'],
                    'UefiTargetBootSourceOverrideSupported': ['FD.Virtual.1.1']}}
                self.patches = []
                self.resets = []
            def get(self, path):
                return self.media if path == '/cd' else self.system
            def request(self, method, path, body):
                self.patches.append((method, path, body))
                if path == '/cd':
                    self.media['Oem']['Hp'].update(body['Oem']['Hp'])
                else:
                    self.system['Boot'].update(body['Boot'])
            def reset(self, kind):
                self.resets.append(kind)
        ilo = FakeIlo()
        boot_once(ilo, '/cd', require_virtual_cd=True)
        self.assertEqual(ilo.patches, [('patch', '/cd',
            {'Oem': {'Hp': {'BootOnNextServerReset': True}}}),
            ('patch', '/system', {'Boot': {'BootSourceOverrideEnabled': 'Once',
                                         'BootSourceOverrideTarget': 'Cd'}})])
        self.assertEqual(ilo.resets, ['ForceRestart'])

    def test_alma_boot_refuses_ambiguous_generic_cd(self):
        class FakeIlo:
            system_path = '/system'
            def get(self, path):
                if path == '/cd':
                    return {'Inserted': True, 'Image': 'http://example/alma.iso'}
                return {'PowerState': 'On', 'Boot': {
                    'BootSourceOverrideSupported': ['Cd'],
                    'UefiTargetBootSourceOverrideSupported': ['FD.Virtual.1.1']}}
            def request(self, *args):
                raise AssertionError('No boot override should be attempted')
            def reset(self, *args):
                raise AssertionError('Server must not be rebooted')
        with self.assertRaisesRegex(RuntimeError, 'ambiguous generic Cd'):
            boot_once(FakeIlo(), '/cd', require_virtual_cd=True)

    def test_windows_boot_patch_is_served_without_copying_dvd(self):
        source = Path(__file__).resolve().parents[1] / 'ISO/SERVER_EVAL_x64FRE_en-us.iso'
        if not source.exists():
            self.skipTest('Windows evaluation ISO absent')
        offset, data = no_prompt_patch(source)
        with tempfile.TemporaryDirectory() as temp:
            server = MediaServer(source, '127.0.0.1', 0, 'test', Path(temp) / 'media.log',
                                 patches=[(offset, data)])
            server.start()
            try:
                port = server.server.server_address[1]
                request = Request(f'http://127.0.0.1:{port}/test/ubuntu.iso',
                                  headers={'Range': f'bytes={offset + 20}-{offset + 39}'})
                with urlopen(request, timeout=5) as response:
                    self.assertEqual(response.status, 206)
                    self.assertEqual(response.read(), data[20:40])
            finally:
                server.close()

    def test_media_port_cannot_be_shared_by_two_panels(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / 'tiny.iso'
            source.write_bytes(b'boot')
            first = MediaServer(source, '127.0.0.1', 0, 'first', Path(temp) / 'first.log')
            try:
                port = first.server.server_address[1]
                with self.assertRaises(OSError):
                    MediaServer(source, '127.0.0.1', port, 'second', Path(temp) / 'second.log')
            finally:
                first.server.server_close()

    def test_media_accounting_separates_installer_from_answer_usb(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / 'tiny.iso'
            answer = Path(temp) / 'answer.img'
            source.write_bytes(b'installer')
            answer.write_bytes(b'answer')
            server = MediaServer(source, '127.0.0.1', 0, 'test', Path(temp) / 'media.log',
                                 extras={'answer.img': answer})
            server.start()
            try:
                port = server.server.server_address[1]
                with urlopen(f'http://127.0.0.1:{port}/test/answer.img', timeout=5) as response:
                    self.assertEqual(response.read(), b'answer')
                self.assertEqual(server.iso_bytes_served, 0)
                with urlopen(f'http://127.0.0.1:{port}/test/ubuntu.iso', timeout=5) as response:
                    self.assertEqual(response.read(), b'installer')
                self.assertEqual(server.iso_bytes_served, len(b'installer'))
            finally:
                server.close()

    def test_esxi_kickstart_targets_exact_logical_disk_and_mac(self):
        script = kickstart(DISK, '02:00:00:00:00:08', '192.0.2.102/24',
                           '192.0.2.254', ['4.2.2.4'], 'esxi-test', 'SafePassword123')
        self.assertIn('install --disk=naa.600508b1001c6d005714f698ca5f0135', script)
        self.assertIn('localcli network nic list', script)
        self.assertIn('\nreboot\n', script)
        self.assertIn('tolower($5) == "up"', script)
        self.assertIn('if [ "$COUNT" -eq 1 ]; then NIC="$LINKED"; fi', script)
        self.assertIn('/var/log/provisioning-network.log', script)
        self.assertIn('policy failover set --active-uplinks="$NIC"', script)
        self.assertIn('route ipv4 add --gateway=192.0.2.254 --network=default', script)
        self.assertIn('uplink remove --uplink-name="$OLD"', script)
        self.assertLess(script.index('route ipv4 add --gateway='), script.index('uplink remove --uplink-name="$OLD"'))
        self.assertIn('02:00:00:00:00:08', script)
        self.assertNotIn('network --bootproto=static --device=', script)
        self.assertNotIn(OTHER['wwn'].removeprefix('0x'), script)

    def test_windows_preflight_matches_one_disk_before_clean(self):
        files = render(DISK, [OTHER], '02:00:00:00:00:08',
                       '192.0.2.102/24', '192.0.2.254', ['4.2.2.4'],
                       'win-test', 'SafePassword123', 2, backup_password='OtherSafePassword123!')
        for name in ('Autounattend.xml', 'FIRSTBOOT.XML'):
            ET.fromstring(files[name])
        script = files['DISKPREP.CMD']
        self.assertLess(script.index('if not "!COUNT!"=="1" goto fail'), script.index('echo clean'))
        self.assertIn('dism /Apply-Image', script)
        self.assertIn('wmic.exe /? >nul 2>&1', script)
        self.assertIn('set /a TARGET=%%J >nul', script)
        self.assertIn('if /I "%%I"=="Index"', script)

        self.assertNotIn('findstr', script.lower())
        self.assertNotIn('timeout', script.lower())
        self.assertIn('for /l %%N in (1,1,6)', script)
        self.assertIn('for /l %%N in (1,1,12)', script)
        self.assertLess(script.index('if not defined WIM goto fail'), script.index('echo clean'))
        self.assertIn('provisioning-diskpart.log', script)
        self.assertIn('if exist W:\\ goto occupiedletters', script)
        self.assertIn('WINDOWS PROVISIONING FAILED at !STEP!', script)
        self.assertIn('cmd.exe /k', script)
        self.assertNotIn('wpeutil shutdown', script)
        self.assertIn('/Index:2', script)
        self.assertIn('02-00-00-00-00-08', files['FIRSTBOOT.XML'])
        self.assertIn('192.0.2.102/24', files['FIRSTBOOT.XML'])
        self.assertIn('Provision-PostInstall.ps1', script)
        self.assertIn('SetupComplete.cmd', script)
        self.assertIn('<FirstLogonCommands>', files['FIRSTBOOT.XML'])
        self.assertIn('SetupComplete.cmd</CommandLine>', files['FIRSTBOOT.XML'])
        self.assertIn('name="Microsoft-Windows-Deployment"', files['FIRSTBOOT.XML'])
        self.assertIn('SetupComplete.cmd</Path>', files['FIRSTBOOT.XML'])
        self.assertIn('New-NetIPAddress', files['POSTINST.PS1'])
        self.assertIn('Removing duplicate address from inactive interface', files['POSTINST.PS1'])
        self.assertLess(files['POSTINST.PS1'].index('Remove-NetIPAddress -InputObject $old'),
                        files['POSTINST.PS1'].index('New-NetIPAddress -InterfaceIndex $nic.ifIndex'))
        self.assertIn("if ($prior.success -eq $true)", files['POSTINST.PS1'])
        self.assertNotIn('del /f /q', files['SETUPCOMP.CMD'])
        self.assertIn('fDenyTSConnections', files['POSTINST.PS1'])
        self.assertIn('IranServer-Provision-PostInstall', files['POSTINST.PS1'])
        self.assertIn('Register-ScheduledTask -TaskName $retryTask', files['POSTINST.PS1'])
        self.assertIn("-User 'NT AUTHORITY\\SYSTEM'", files['POSTINST.PS1'])
        self.assertIn('Unregister-ScheduledTask -TaskName $retryTask', files['POSTINST.PS1'])
        self.assertIn("'iranserver'", files['POSTINST.PS1'])
        self.assertNotIn('slmgr /skms', files['POSTINST.PS1'])

    def test_windows_custom_rdp_port_reaches_postinstall_script(self):
        files = render(DISK, [OTHER], '02:00:00:00:00:08',
            '192.0.2.102/24', '192.0.2.254', ['4.2.2.4'],
            'win-test', 'SafePassword123', 2,
            backup_password='OtherSafePassword123!', rdp_port=44900)
        self.assertIn('"rdp_port": 44900', files['POSTINST.PS1'])

    def test_windows_postinstall_sends_tokenized_completion_report(self):
        files = render(DISK, [OTHER], '02:00:00:00:00:08',
                       '192.0.2.102/24', '192.0.2.254', ['4.2.2.4'],
                       'win-test', 'SafePassword123', backup_password='OtherSafePassword123!',
                       report_url='http://192.0.2.200:8080/job-secret/event')
        script = files['POSTINST.PS1']
        self.assertIn('WINDOWS_POSTINSTALL', script)
        self.assertIn('http://192.0.2.200:8080/job-secret/event', script)
        self.assertIn('Invoke-WebRequest -Uri $cfg.report_url -Method POST', script)

    def test_windows_refuses_indistinguishable_disks(self):
        twin = dict(OTHER, size=DISK['size'])
        with self.assertRaisesRegex(ValueError, 'equal-sized'):
            render(DISK, [twin], '02:00:00:00:00:08',
                   '192.0.2.102/24', '192.0.2.254', ['4.2.2.4'],
                   'win-test', 'SafePassword123', backup_password='OtherSafePassword123!')

    def test_windows_data_disk_is_explicit_and_size_checked_before_erase(self):
        files = render(DISK, [OTHER], '02:00:00:00:00:08',
                       '192.0.2.102/24', '192.0.2.254', ['4.2.2.4'],
                       'win-test', 'SafePassword123',
                       backup_password='OtherSafePassword123!', data_disk=OTHER)
        script = files['POSTINST.PS1']
        self.assertIn(str(OTHER['size']), script)
        self.assertLess(script.index('$dataCandidates.Count -ne 1'), script.index('Clear-Disk'))
        self.assertLess(script.index('$dataDisk.IsBoot'), script.index('Clear-Disk'))
        self.assertIn('Format-Volume -DriveLetter $dataDrive -FileSystem NTFS', script)
        self.assertLess(script.index('$availableLetters.Count -eq 0'), script.index('Clear-Disk'))
        self.assertNotIn(str(OTHER['size']), render(DISK, [OTHER], '02:00:00:00:00:08',
            '192.0.2.102/24', '192.0.2.254', ['4.2.2.4'], 'win-test',
            'SafePassword123', backup_password='OtherSafePassword123!')['POSTINST.PS1'])

    def test_windows_rejects_two_class_generated_password(self):
        with self.assertRaisesRegex(ValueError, 'three character types'):
            render(DISK, [OTHER], '02:00:00:00:00:08',
                   '192.0.2.102/24', '192.0.2.254', ['4.2.2.4'],
                   'win-test', 'deadbeef0123456789deadbeef01234567',
                   backup_password='OtherSafePassword123!')
