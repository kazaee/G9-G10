"""LO100 read-only native media adapter; no power commands or OS input.

The controller dials a fixed listener port, unlike Redfish URL-based media.
Wire codecs are isolated under _lo100_wire (MIT, attribution retained).
A listener or Inserted-style acknowledgement alone is never boot success.
"""
import socket
import threading
import time
from pathlib import Path
from .lo100 import Page
from ._lo100_wire import protocol as p
from ._lo100_wire.buffer import SocketReader
from ._lo100_wire.vmedia100 import FileMedia, MediaChannel, VMediaError


class NativeMediaUnavailable(RuntimeError):
    pass


class NativeMedia:
    def __init__(self,controller,username,image,media_host,port=443):
        self.controller,self.username=controller,username
        self.image=Path(image).resolve()
        if not self.image.is_file():raise ValueError('ISO file does not exist')
        self.media_host,self.port=media_host,int(port)
        if not 1<=self.port<=65535:raise ValueError('Invalid native media port')
        self.listener=self.control=None
        self.stop=threading.Event()
        self.tcp_connected=self.native_ready=False
        self.iso_bytes_served=0
        self.reads=0
        self.storage_allowed=None
        self.full_control=False
        self.control_state_seen=False
        self.storage_status=None
        self.error=None
        self.threads=[]
        self.connections=[]
        self.registered=False
        self.send_lock=threading.Lock()

    def send(self,data):
        with self.send_lock:
            if self.control:self.control.sendall(data)

    def _receive(self):
        reader=SocketReader(self.control)
        requested=False
        try:
            while not self.stop.is_set():
                msg=reader.read_u8()
                if msg==p.MSG_MULTI_USER_STATE:
                    state=p.decode_multi_user_state(reader)
                    self.control_state_seen=True
                    self.storage_allowed=state.allowed_to_do_storage
                    self.full_control=state.has_full_control
                    if state.allowed_to_take_full_control and not state.has_full_control and not requested:
                        self.send(p.encode_request_primary_control());requested=True
                elif msg==p.MSG_STORAGE_STATUS:
                    status=p.decode_storage_status(reader)
                    self.storage_status=status.share_status0
                elif msg==p.MSG_SERVER_DISCONNECT:
                    p.decode_server_disconnect(reader);self.error='Controller closed native session';break
                else:
                    decoders={p.MSG_INFORM_VESA_MODE:p.decode_inform_vesa_mode,
                        p.MSG_BIT_BLT:p.decode_bit_blt,p.MSG_ENHANCE_BIT_BLT:p.decode_enhance_bit_blt,
                        p.MSG_SET_PALETTE:p.decode_set_palette,p.MSG_SET_TEXT_CURSOR:p.decode_set_text_cursor,
                        p.MSG_SPECIAL_GRAPHICS_BIT:p.decode_special_graphics_bit,
                        p.MSG_MATROX_GRAPHICS_CURSOR:p.decode_matrox_graphics_cursor,
                        p.MSG_STANDBY_POWER:p.decode_standby_power,
                        p.MSG_INFORM_KEY_INDICATORS:p.decode_inform_key_indicators,
                        p.MSG_FIRMWARE_VERSION:p.decode_firmware_version,
                        p.MSG_GRAPHICS_REGISTER_VALUE:p.decode_graphics_register_value}
                    decoder=decoders.get(msg)
                    if not decoder:raise NativeMediaUnavailable('Unrecognized native media control frame')
                    decoder(reader)
        except (OSError,EOFError,ValueError,NativeMediaUnavailable) as exc:
            if not self.stop.is_set():self.error=type(exc).__name__

    def _accept(self):
        while not self.stop.is_set():
            try:connection,peer=self.listener.accept()
            except TimeoutError:continue
            except OSError:break
            if peer[0]!=socket.gethostbyname(self.controller.host):connection.close();continue
            self.tcp_connected=True
            self.connections.append(connection)
            connection.settimeout(1800)
            owner=self
            class ReadOnlyImage(FileMedia):
                def read(self,offset,length):
                    if offset<0 or length<0 or length>8*1024**2 or offset+length>self.size():raise VMediaError('Invalid bounded ISO read')
                    value=super().read(offset,length)
                    owner.reads+=1;owner.iso_bytes_served+=len(value)
                    return value
            class Channel(MediaChannel):
                def _read_hello(self):
                    super()._read_hello();owner.native_ready=True
            def serve(conn):
                media=ReadOnlyImage(str(owner.image),writable=False)
                try:Channel(conn,media).run(should_stop=owner.stop.is_set)
                finally:media.close()
            thread=threading.Thread(target=serve,args=(connection,),daemon=True)
            self.threads.append(thread);thread.start()

    def start(self,timeout=10):
        try:
            self.listener=socket.socket()
            if hasattr(socket,'SO_EXCLUSIVEADDRUSE'):
                self.listener.setsockopt(socket.SOL_SOCKET,socket.SO_EXCLUSIVEADDRUSE,1)
            self.listener.bind((self.media_host,self.port));self.listener.listen(2);self.listener.settimeout(0.5)
            thread=threading.Thread(target=self._accept,daemon=True);self.threads.append(thread);thread.start()
            params=Page(self.controller.read('/kvms.html').decode(errors='replace')).params
            if params.get('ipaddress')!=self.controller.host or not params.get('httpdata'):
                raise NativeMediaUnavailable('Native media authentication parameters were not confirmed')
            self.control=socket.create_connection((self.controller.host,int(params.get('nonsecure_kvmport','80'))),6)
            self.control.settimeout(30)
            self.send(p.encode_client_nop()+p.encode_client_handshake(self.username,params['httpdata']))
            reader=SocketReader(self.control)
            if reader.read_u8()!=p.MSG_SERVER_HANDSHAKE:
                raise NativeMediaUnavailable('Native media authentication was not accepted')
            p.decode_server_handshake(reader)
            self.control.settimeout(None)
            thread=threading.Thread(target=self._receive,daemon=True);self.threads.append(thread);thread.start()
            # Wait for effective permissions, including any offered primary-control request.
            # Never force-disconnect another console or register a share while view-only.
            permission_deadline=time.monotonic()+min(timeout,5)
            while time.monotonic()<permission_deadline:
                if self.control_state_seen and self.storage_allowed and self.full_control:break
                if self.error:break
                time.sleep(0.1)
            if not (self.control_state_seen and self.storage_allowed and self.full_control):
                raise NativeMediaUnavailable('LO100 did not grant native virtual-media control to this session; no ISO share or boot requested. Close an idle Java console normally and retry, or use its manual-media function.')
            self.send(p.encode_storage_client_connect(socket.inet_aton(self.media_host),self.port,0,
                                                      p.MEDIA_TYPE_CD_ISO_IMAGE,str(self.image)))
            self.registered=True
            deadline=time.monotonic()+timeout
            while time.monotonic()<deadline:
                if self.native_ready and (self.storage_status==p.STORAGE_STATUS_CONNECTED or self.iso_bytes_served>0):
                    return self
                if self.error:break
                time.sleep(0.1)
            raise NativeMediaUnavailable('Automatic native ISO delivery was not verified; no boot requested. '
                'Check the selected port/firewall and LO100 storage permissions, or use the manual-media fallback.')
        except Exception:
            self.close();raise

    def evidence(self):
        return {'target':self.controller.host,'media_host':self.media_host,'port':self.port,'tcp_connected':self.tcp_connected,'native_handshake':self.native_ready,
                'iso_reads':self.reads,'iso_bytes':self.iso_bytes_served,'storage_allowed':self.storage_allowed,
                'full_control':self.full_control,'control_state_seen':self.control_state_seen,'storage_status':self.storage_status,'error':self.error}

    def close(self):
        self.stop.set()
        if self.control and self.registered:
            try:self.send(p.encode_storage_client_disconnect())
            except OSError:pass
        if self.control and self.full_control:
            try:self.send(p.encode_relinquish_full_control())
            except OSError:pass
        for sock in [self.control,self.listener,*self.connections]:
            if sock:
                try:sock.shutdown(socket.SHUT_RDWR)
                except OSError:pass
                try:sock.close()
                except OSError:pass
        for thread in self.threads:
            if thread is not threading.current_thread():thread.join(timeout=0.5)
