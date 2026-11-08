import copy
import unittest
import json
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch
from src.lo100_inventory import validate_target, REMOTE_SCRIPT
from src.lo100_media_probe import callback_request, handshake

GUID='8156aeda-8dfe-d511-8553-f4ce46866fde'


class ExistingOSInventoryTests(unittest.TestCase):
    def data(self):
        return {'system_uuid':GUID,'server_model':'ProLiant DL160 G6','server_serial':'g6-serial',
                'by_id':{'wwn-test':'/dev/sda'},'lsblk':{'blockdevices':[{'name':'sda'}]}}

    def test_verified_same_physical_server_is_required(self):
        data=self.data()
        self.assertIs(validate_target(data,GUID,'DL160 G6'),data)

    def test_another_os_host_is_rejected(self):
        data=self.data();data['system_uuid']='11111111-1111-1111-1111-111111111111'
        with self.assertRaises(ValueError):validate_target(data,GUID,'DL160 G6')

    def test_missing_dmi_permission_is_not_treated_as_verified(self):
        data=self.data();data['system_uuid']=''
        with self.assertRaises(ValueError):validate_target(data,GUID,'DL160 G6')

    def test_no_controller_guid_cannot_verify_target(self):
        with self.assertRaises(ValueError):validate_target(self.data(),None,'DL160 G6')

    def test_collector_contains_no_disk_or_network_mutation_commands(self):
        compile(REMOTE_SCRIPT,'<readonly>','exec')
        for token in ['mkfs','wipefs','reboot','shutdown','ip addr add','ip link set']:
            self.assertNotIn(token,REMOTE_SCRIPT)

    def test_ssh_collects_only_readonly_report_without_password_in_arguments(self):
        from src.lo100_inventory import collect
        data=self.data()
        process=Mock(returncode=0,stdout=('LO100_INVENTORY_JSON='+json.dumps(data)).encode(),stderr=b'')
        with tempfile.TemporaryDirectory() as directory, patch('src.lo100_inventory.subprocess.run',return_value=process) as run:
            result=collect('192.0.2.30:22','root','private-test-password',GUID,'DL160 G6',Path(directory)/'known-hosts')
            self.assertEqual(result['server_serial'],'g6-serial')
            self.assertNotIn('private-test-password',str(run.call_args.args))
            self.assertEqual(run.call_args.kwargs['env']['LO100_OS_SSH_PASSWORD'],'private-test-password')
            self.assertIn('python3 -B',run.call_args.args[0][-1])

    def test_ssh_option_injection_is_rejected(self):
        from src.lo100_inventory import collect
        with patch('src.lo100_inventory.subprocess.run') as run:
            with self.assertRaises(ValueError):
                collect('-oProxyCommand=bad','root','password',GUID,'DL160 G6','unused')
            run.assert_not_called()


class Lo100NativeDiagnosticTests(unittest.TestCase):
    def test_probe_selects_no_shares_or_images(self):
        frame=callback_request('172.20.224.253',443)
        self.assertEqual(len(frame),1057)
        self.assertEqual(frame[0],153)
        self.assertEqual(frame[17:19],bytes([255,255]))
        self.assertEqual(frame[33:],bytes(1024))

    def test_handshake_is_only_nop_and_authentication(self):
        frame=handshake('ILO','a'*96)
        self.assertEqual(frame[:8],bytes([210])+bytes(7))
        self.assertEqual(frame[8],221)
        self.assertEqual(len(frame),197)
