"""LO100 native reverse-TCP diagnostic. No selected share, image, input or power.

Wire-format reference: pedrotei/ilo-console (MIT), protocol.py, blob
4f9f7be1c3e75175bea70cd6385042ea03d13c7b. See docs/third-party/lo100-protocol.txt.
A missing callback is INCONCLUSIVE, not proof of a blocked TCP port.
"""
import ipaddress
import socket
import struct
import time
from .lo100 import Page


def handshake(username, token):
    if len(username.encode('latin-1')) > 16 or not token or len(token) > 128:
        raise ValueError('Unexpected LO100 native authentication fields')
    raw = token.encode('ascii')
    return (bytes([210])+bytes(7)+bytes([221])+struct.pack('<iiiii',0x5a5a5a5a,16,20,4,0)
        +username.encode('latin-1').ljust(16,b'\0')+raw[:20].ljust(20,b'\0')
        +struct.pack('<i',31)+raw.ljust(128,b'\0'))


def callback_request(address, port):
    # Both share indexes are undefined and both paths empty: no bootable image.
    return (bytes([153])+bytes(12)+socket.inet_aton(address)
        +struct.pack('<bbHBBBBB',-1,-1,port,0,0,0,255,1)+bytes(7)+bytes(1024))


def probe(controller, username, media_host, port=443, timeout=8):
    ipaddress.IPv4Address(media_host)
    port = int(port)
    if not 1 <= port <= 65535:raise ValueError('Invalid media port')
    params = Page(controller.read('/kvms.html').decode(errors='replace')).params
    if params.get('ipaddress') != controller.host or not params.get('httpdata'):
        raise ValueError('Unexpected LO100 KVM parameters')
    result = {'target':controller.host,'media_host':media_host,'port':port,
        'status':'inconclusive','callback_observed':False,'boot_requested':False,
        'protocol':'LO100 native TCP; not HTTP/HTTPS','image_selected':False}
    with socket.socket() as listener:
        if hasattr(socket,'SO_EXCLUSIVEADDRUSE'):
            listener.setsockopt(socket.SOL_SOCKET,socket.SO_EXCLUSIVEADDRUSE,1)
        listener.bind((media_host,port));listener.listen(2)
        with socket.create_connection((controller.host,int(params.get('nonsecure_kvmport','80'))),6) as control:
            control.settimeout(6);control.sendall(handshake(username,params['httpdata']))
            reply=b''
            while len(reply)<33:
                part=control.recv(33-len(reply))
                if not part:break
                reply+=part
            if len(reply)!=33 or reply[0]!=200:
                result['reason']='native-handshake-not-confirmed';return result
            result['handshake_flags']=reply[1:].decode(errors='replace').strip().split(' ',1)[-1]
            control.sendall(callback_request(media_host,port))
            deadline=time.monotonic()+timeout
            while time.monotonic()<deadline:
                listener.settimeout(max(0.1,deadline-time.monotonic()))
                try:connection,peer=listener.accept()
                except TimeoutError:break
                with connection:
                    if peer[0]!=socket.gethostbyname(controller.host):continue
                    result.update(status='tcp-reachable',callback_observed=True)
                    break
            if result['callback_observed']:
                control.sendall(bytes([154,0]))
            else:
                result['reason']='no-callback; firewall, native-storage-permission and request-acceptance are not distinguished'
    return result
