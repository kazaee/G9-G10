import hashlib
import struct
import unittest
from unittest.mock import Mock, patch, call
from src.lo100_ipmi import Lo100IPMI


class IpmiTests(unittest.TestCase):
    def client(self):
        with patch('src.lo100_ipmi.socket.socket'):
            return Lo100IPMI('192.0.2.30','ILO','test-password')

    def test_read_boot_flags_has_no_write(self):
        client=self.client();client._command=Mock(return_value=bytes([1,5,0,0,0,0,0]))
        self.assertFalse(client.boot_flags()['valid'])
        client._command.assert_called_once_with(0,9,bytes([5,0,0]))

    def test_cd_override_requires_explicit_confirmation_and_media(self):
        for confirmation,media in (('',True),('INSTALL',False),('DISCOVER','true')):
            client=self.client();client._command=Mock()
            with self.assertRaises(ValueError):client.cd_once(confirmation=confirmation,media_confirmed=media)
            client._command.assert_not_called()

    def test_only_nonpersistent_bios_cd_is_written_and_verified(self):
        client=self.client()
        client._command=Mock(side_effect=[bytes([1,5,0,0,0,0,0]),b'',bytes([1,5,0x80,0x14,0,0,0])])
        result=client.cd_once(confirmation='INSTALL',media_confirmed=True)
        self.assertEqual(client._command.call_args_list,[call(0,9,bytes([5,0,0])),call(0,8,bytes([5,0x80,0x14,0,0,0])),call(0,9,bytes([5,0,0]))])
        self.assertEqual(result['after']['device'],5)
        self.assertFalse(result['after']['persistent'])
        self.assertFalse(result['reset_requested'])

    def test_ambiguous_write_is_read_back_without_retry(self):
        client=self.client()
        client._command=Mock(side_effect=[bytes([1,5,0,0,0,0,0]),TimeoutError(),bytes([1,5,0x80,0x14,0,0,0])])
        self.assertIsNotNone(client.cd_once(confirmation='DISCOVER',media_confirmed=True)['warning'])
        self.assertEqual(client._command.call_count,3)

    def test_mismatched_readback_aborts_before_reset(self):
        client=self.client()
        client._command=Mock(side_effect=[bytes([1,5,0,0,0,0,0]),b'',bytes([1,5,0xc0,0x14,0,0,0])])
        with self.assertRaisesRegex(RuntimeError,'not confirmed'):client.cd_once(confirmation='INSTALL',media_confirmed=True)

    def response(self,client,*,tampered=False):
        first=bytes([0x81,4]);first+=bytes([-sum(first)&255])
        rest=bytes([0x20,4,9,0,1,5,0,0,0,0,0]);payload=first+rest+bytes([-sum(rest)&255])
        seq=struct.pack('<I',92);sid=b'abcd'
        digest=hashlib.md5(client.key+sid+payload+seq+client.key).digest()
        if tampered:digest=bytes(16)
        return b'\x06\x00\xff\x07\x02'+seq+sid+digest+bytes([len(payload)])+payload

    def test_authenticated_packet_is_verified_and_sequence_advances(self):
        client=self.client();client.auth=2;client.sid=b'abcd';client.seq=41;client.active=True
        client.sock.recv.return_value=self.response(client)
        self.assertEqual(client._command(0,9,bytes([5,0,0])),bytes([1,5,0,0,0,0,0]))
        self.assertEqual(client.seq,42)

    def test_forged_reply_is_rejected(self):
        client=self.client();client.auth=2;client.sid=b'abcd';client.active=True
        client.sock.recv.return_value=self.response(client,tampered=True)
        with self.assertRaisesRegex(ValueError,'authentication failed'):client._command(0,9,bytes([5,0,0]))
