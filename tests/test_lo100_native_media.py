import socket
import struct
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from src.lo100_native_media import NativeMedia, NativeMediaUnavailable
from src._lo100_wire import protocol as p


def read_exact(sock,count):
    result=b''
    while len(result)<count:
        data=sock.recv(count-len(result))
        if not data:raise EOFError()
        result+=data
    return result


class NativeMediaTests(unittest.TestCase):
    def test_real_local_socket_handshake_and_iso_read_are_required(self):
        # A simulated controller requests a sector over the actual listener.
        # This verifies framing locally; it is not a hardware compatibility test.
        with tempfile.TemporaryDirectory() as d:
            image=Path(d)/'test.iso';image.write_bytes(b'I'*4096)
            with socket.socket() as spare:
                spare.bind(('127.0.0.1',0));port=spare.getsockname()[1]
            controller=Mock(host='127.0.0.1')
            controller.read.return_value=b'<param name="ipaddress" value="127.0.0.1"><param name="httpdata" value="token">'
            client,server=socket.socketpair();server.settimeout(5)
            real_connect=socket.create_connection
            errors=[];finished=threading.Event()
            def fake_controller():
                try:
                    read_exact(server,len(p.encode_client_nop()+p.encode_client_handshake('ILO','token')))
                    server.sendall(bytes([p.MSG_SERVER_HANDSHAKE])+b'MAHOGANY KVM'.ljust(32,b'\x00')+bytes([p.MSG_MULTI_USER_STATE])+struct.pack('<I',0x101)+bytes(4))
                    read_exact(server,len(p.encode_storage_client_connect(socket.inet_aton('127.0.0.1'),port,0,11,str(image.resolve()))))
                    with real_connect(('127.0.0.1',port),2) as channel:
                        channel.sendall(bytes(8)+b'VENDORID'+bytes(520)+bytes(8))
                        self.assertEqual(len(read_exact(channel,16)),16)
                        request=bytearray(76);request[64]=0x28;request[71:73]=struct.pack('>H',1)
                        channel.sendall(request)
                        reply=read_exact(channel,64+2048)
                        self.assertEqual(reply[64:],b'I'*2048)
                        finished.wait(3)
                except Exception as exc:errors.append(exc)
                finally:server.close()
            thread=threading.Thread(target=fake_controller,daemon=True);thread.start()
            media=NativeMedia(controller,'ILO',image,'127.0.0.1',port)
            try:
                with patch('src.lo100_native_media.socket.create_connection',return_value=client):media.start(timeout=3)
                self.assertTrue(media.native_ready)
                self.assertEqual(media.iso_bytes_served,2048)
                self.assertEqual(media.reads,1)
            finally:finished.set();media.close();thread.join(4)
            self.assertEqual(errors,[])

    def test_listener_without_controller_media_handshake_is_not_success(self):
        with tempfile.TemporaryDirectory() as d:
            image=Path(d)/'test.iso';image.write_bytes(b'I'*2048)
            controller=Mock(host='127.0.0.1')
            controller.read.return_value=b'<param name="ipaddress" value="127.0.0.1"><param name="httpdata" value="token">'
            client,server=socket.socketpair()
            server.sendall(bytes([p.MSG_SERVER_HANDSHAKE])+b'MAHOGANY KVM'.ljust(32,b'\x00')+bytes([p.MSG_MULTI_USER_STATE])+struct.pack('<I',0x101)+bytes(4))
            with socket.socket() as spare:
                spare.bind(('127.0.0.1',0));port=spare.getsockname()[1]
            media=NativeMedia(controller,'ILO',image,'127.0.0.1',port)
            try:
                with patch('src.lo100_native_media.socket.create_connection',return_value=client):
                    with self.assertRaises(NativeMediaUnavailable):media.start(timeout=0.1)
                self.assertFalse(media.native_ready)
                self.assertEqual(media.iso_bytes_served,0)
            finally:server.close();media.close()

    def test_permission_denial_does_not_register_iso(self):
        with tempfile.TemporaryDirectory() as d:
            image=Path(d)/'test.iso';image.write_bytes(b'I'*2048)
            controller=Mock(host='127.0.0.1')
            controller.read.return_value=b'<param name="ipaddress" value="127.0.0.1"><param name="httpdata" value="token">'
            client,server=socket.socketpair();server.settimeout(2)
            server.sendall(bytes([p.MSG_SERVER_HANDSHAKE])+b'MAHOGANY KVM'.ljust(32,b'\x00')+bytes([p.MSG_MULTI_USER_STATE])+struct.pack('<I',8)+bytes(4))
            with socket.socket() as spare:
                spare.bind(('127.0.0.1',0));port=spare.getsockname()[1]
            media=NativeMedia(controller,'ILO',image,'127.0.0.1',port)
            try:
                with patch('src.lo100_native_media.socket.create_connection',return_value=client):
                    with self.assertRaisesRegex(NativeMediaUnavailable,'did not grant'):media.start(timeout=0.2)
                data=server.recv(4096)
                self.assertEqual(data,p.encode_client_nop()+p.encode_client_handshake('ILO','token'))
                self.assertFalse(media.registered)
                self.assertEqual(media.iso_bytes_served,0)
            finally:server.close();media.close()
