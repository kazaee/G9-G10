"""LO100 SMASH-CLP over its legacy terminal service.

Only show commands and the advertised console1 attachment are implemented.
No firmware, BIOS, power or OS keystrokes are sent by this module.
"""
import re
import socket
import struct
import threading
import time
from pathlib import Path


class Lo100CLP:
    def __init__(self, host, username, password, timeout=8):
        if any(not value or any(ord(c)<32 or ord(c)>126 for c in value) for value in (username,password)):
            raise ValueError('LO100 terminal credentials must be printable ASCII without control characters')
        self.host, self.username, self.password, self.timeout = host, username, password, timeout
        self.socket = None
        self.pending = bytearray()
        self.serial = False
        self.stop = threading.Event()
        self.thread = None

    def _feed(self, data):
        self.pending.extend(data)
        out = bytearray()
        while self.pending:
            if self.pending[0] != 255:
                out.append(self.pending.pop(0)); continue
            if len(self.pending) < 2:break
            command = self.pending[1]
            if command == 255:
                out.append(255); del self.pending[:2]; continue
            if command in (251,252,253,254):
                if len(self.pending)<3:break
                option=self.pending[2];del self.pending[:3]
                if command==251:self.socket.sendall(bytes([255,253 if option in (1,3) else 254,option]))
                elif command==253:
                    accept=option in (3,24,31)
                    self.socket.sendall(bytes([255,251 if accept else 252,option]))
                    if option==31:
                        self.socket.sendall(bytes([255,250,31])+struct.pack('>HH',160,200)+bytes([255,240]))
                continue
            if command==250:
                end=self.pending.find(bytes([255,240]),2)
                if end<0:break
                body=bytes(self.pending[2:end]);del self.pending[:end+2]
                if body[:2]==bytes([24,1]):self.socket.sendall(bytes([255,250,24,0])+b'VT100'+bytes([255,240]))
                continue
            del self.pending[:2]
        return bytes(out).replace(b'\x00',b'')

    def _wait(self, pattern, timeout=None):
        output=b'';deadline=time.monotonic()+(timeout or self.timeout)
        while time.monotonic()<deadline and not self.stop.is_set():
            try:
                chunk=self.socket.recv(8192)
                if not chunk:raise RuntimeError('LO100 terminal session closed')
                output+=self._feed(chunk)
                if re.search(pattern,output,re.I):return output
                # Pagination is handled only while still at management CLI.
                if not self.serial and re.search(rb'--\s*more\s*--\s*$',output,re.I):self.socket.sendall(b' ')
            except TimeoutError:continue
        raise RuntimeError('LO100 terminal did not reach the expected prompt')

    def login(self):
        self.socket=socket.create_connection((self.host,23),self.timeout)
        self.socket.settimeout(0.5)
        banner=self._wait(rb'login:\s*$')
        if b'IPCOM' not in banner:raise RuntimeError('Unrecognized LO100 management terminal')
        self.socket.sendall(self.username.encode('ascii')+b'\n')
        self._wait(rb'password:\s*$')
        # This firmware consumes LF correctly; CRLF can submit a second empty line.
        self.socket.sendall(self.password.encode('ascii')+b'\n')
        banner=self._wait(rb'/[^\r\n]{0,200}->\s*$')
        if b'Lights-Out 100 Management' not in banner:raise RuntimeError('LO100 management login not confirmed')
        return self

    def show(self,path):
        if self.serial or not re.fullmatch(r'/(?:map1|system1(?:/console1|/oemhp_frus(?:/oemhp_fru_devid[0-9]+lun[0-9]+)?)?)',path):
            raise ValueError('Only advertised read-only management resources are accepted')
        self.socket.sendall(('show '+path+'\n').encode('ascii'))
        return self._wait(rb'/[^\r\n]{0,200}->\s*$').decode('utf-8',errors='replace')

    def capture_serial(self,path):
        if self.serial:raise ValueError('Serial capture is already active')
        self.show('/system1/console1')
        path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
        self.socket.sendall(b'start /system1/console1\n')
        self.serial=True
        def pump():
            with path.open('wb') as output:
                while not self.stop.is_set():
                    try:
                        data=self.socket.recv(8192)
                        if not data:break
                        clean=self._feed(data)
                        if clean:output.write(clean);output.flush()
                    except TimeoutError:continue
                    except OSError:break
        self.thread=threading.Thread(target=pump,daemon=True);self.thread.start()
        return self

    def close(self):
        self.stop.set()
        if self.socket:
            try:self.socket.shutdown(socket.SHUT_RDWR)
            except OSError:pass
            self.socket.close()
        if self.thread and self.thread is not threading.current_thread():self.thread.join(timeout=1)

    def __enter__(self):
        try:return self.login()
        except Exception:self.close();raise

    def __exit__(self,*args):self.close()


def properties(text):
    # Whitelist output fields: /map1 also contains the installed license key.
    safe={}
    for name,value in re.findall(r'^\s*([A-Za-z0-9_]+)=(.*?)\s*$',text,re.M):
        if 'license' not in name.lower() and name.lower() not in ('password','key'):
            safe[name]=value
    return safe


def hardware(controller,username,password):
    with Lo100CLP(controller.host,username,password) as cli:
        result={'system':properties(cli.show('/system1')),'manager':properties(cli.show('/map1')),'frus':[]}
        listing=cli.show('/system1/oemhp_frus')
        targets=re.findall(r'^\s*(oemhp_fru_devid[0-9]+lun[0-9]+)\s*$',listing,re.M)
        for target in targets[:8]:
            result['frus'].append(properties(cli.show('/system1/oemhp_frus/'+target)))
    result['server_serial']=result['manager'].get('oemhp_serial_number')
    result['disk_identifiers_verified']=False
    return result
