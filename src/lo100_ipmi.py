"""Narrow LO100 IPMI 1.5 adapter: read boot flags and approved one-time CD.

No power, account, firmware or persistent BIOS commands are exposed.
Packet format was cross-checked against ipmitool LAN and chassis implementations:
https://github.com/ipmitool/ipmitool/blob/master/src/plugins/lan/lan.c
https://github.com/ipmitool/ipmitool/blob/master/lib/ipmi_chassis.c
This implementation uses the Python standard library and requires no administrator.
"""
import hashlib,hmac,secrets,socket,struct
class Lo100IPMI:
    def __init__(self,host,user,password):
        self.user=user.encode().ljust(16,b'\0');self.key=password.encode().ljust(16,b'\0')
        if len(self.user)!=16 or len(self.key)!=16:raise ValueError('IPMI 1.5 credentials exceed 16 bytes')
        self.sock=socket.socket(socket.AF_INET,socket.SOCK_DGRAM);self.sock.connect((host,623));self.sock.settimeout(3)
        self.auth=0;self.sid=bytes(4);self.seq=0;self.rq=0;self.active=False
    def _command(self,netfn,command,data=b''):
        self.rq=(self.rq+1)%64
        head=bytes([0x20,netfn<<2]);head+=bytes([-sum(head)&255])
        tail=bytes([0x81,self.rq<<2,command])+data;msg=head+tail+bytes([-sum(tail)&255])
        seq=struct.pack('<I',self.seq)
        digest=hashlib.md5(self.key+self.sid+msg+seq+self.key).digest() if self.auth else b''
        packet=b'\x06\x00\xff\x07'+bytes([self.auth])+seq+self.sid+digest+bytes([len(msg)])+msg
        self.sock.send(packet)
        self.seq=(self.seq+1)&0xffffffff
        response=self.sock.recv(1024)
        if response[:4]!=b'\x06\x00\xff\x07' or len(response)<14:raise ValueError('Malformed RMCP reply')
        auth=response[4];offset=29 if auth else 13
        if self.auth==2 and auth!=2:raise ValueError('Authenticated IPMI response required')
        if auth not in (0,2) or len(response)<offset+1:raise ValueError('Unsupported reply authentication')
        payload=response[offset+1:offset+1+response[offset]]
        if len(payload)<8 or payload[0]!=0x81 or payload[3]!=0x20 or sum(payload[:3])%256 or sum(payload[3:])%256:raise ValueError('Invalid IPMI checksums')
        if payload[4]>>2!=self.rq or payload[5]!=command or payload[1]>>2!=netfn+1:raise ValueError('Reply differs from command')
        if auth:
            expected=hashlib.md5(self.key+response[9:13]+payload+response[5:9]+self.key).digest()
            if not hmac.compare_digest(expected,response[13:29]):raise ValueError('IPMI reply authentication failed')
        if self.active and response[9:13]!=self.sid:raise ValueError('Wrong IPMI session')
        if payload[6]:raise RuntimeError('IPMI completion 0x%02x for command 0x%02x'%(payload[6],command))
        return payload[7:-1]
    def login(self):
        cap=self._command(6,0x38,bytes([14,4]))
        if len(cap)<3 or not cap[1]&4:raise ValueError('MD5 sessions not advertised')
        challenge=self._command(6,0x39,b'\x02'+self.user)
        if len(challenge)!=20:raise ValueError('Malformed session challenge')
        self.sid=challenge[:4];self.seq=0;self.auth=2
        active=self._command(6,0x3a,bytes([2,4])+challenge[4:20]+secrets.token_bytes(4))
        if len(active)!=10 or active[0]&15!=2:raise ValueError('Malformed authenticated session activation')
        self.sid=active[1:5];self.seq=struct.unpack('<I',active[5:9])[0] or 1;self.active=True
        self._command(6,0x3b,b'\x04')
        return self
    def close(self):
        try:
            if self.active:self._command(6,0x3c,self.sid)
        except (OSError,ValueError,RuntimeError):pass
        self.sock.close()

    def boot_flags(self):
        result=self._command(0,9,bytes([5,0,0]))
        if len(result)!=7 or result[1]&0x7f!=5 or result[1]&0x80:
            raise ValueError('LO100 did not return valid boot flags')
        flags=result[2:]
        return {'raw':flags.hex(),'valid':bool(flags[0]&0x80),
                'persistent':bool(flags[0]&0x40),'uefi':bool(flags[0]&0x20),
                'device':(flags[1]>>2)&15}

    def cd_once(self,*,confirmation,media_confirmed):
        if confirmation not in ('INSTALL','DISCOVER') or media_confirmed is not True:
            raise ValueError('Explicit boot approval and confirmed media are required')
        before=self.boot_flags()
        # Valid, nonpersistent, BIOS, CD/DVD; no CMOS clear or unrelated flags.
        # Send once only. If a response is lost, read back instead of repeating a write.
        warning=None
        try:self._command(0,8,bytes([5,0x80,0x14,0,0,0]))
        except OSError:warning='Boot-setting response was ambiguous; write not repeated'
        after=self.boot_flags()
        if not (after['valid'] and not after['persistent'] and not after['uefi'] and after['device']==5):
            raise RuntimeError('One-time BIOS CD boot was not confirmed; no reset requested')
        return {'before':before,'after':after,'warning':warning,'reset_requested':False}

    def __enter__(self):
        try:return self.login()
        except Exception:self.close();raise

    def __exit__(self,*args):self.close()
