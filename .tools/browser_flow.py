"""Local CDP driver. Never print expressions containing credentials."""
import base64, hashlib, json, os, re, socket, struct, sys, time, urllib.request
from pathlib import Path
from urllib.parse import urlsplit

class Browser:
    def __init__(self):
        pages=json.load(urllib.request.urlopen('http://127.0.0.1:9222/json'))
        page=next(p for p in pages if p['type']=='page' and '8765' in p['url'])
        u=urlsplit(page['webSocketDebuggerUrl'])
        self.sock=socket.create_connection((u.hostname,u.port),timeout=30)
        key=base64.b64encode(os.urandom(16)).decode()
        self.sock.sendall(f'GET {u.path} HTTP/1.1\r\nHost: {u.netloc}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n'.encode())
        header=b''
        while not header.endswith(b'\r\n\r\n'): header+=self.sock.recv(1)
        assert b'HTTP/1.1 101 ' in header, header
        self.counter=0
    def read(self,n):
        out=b''
        while len(out)<n:
            chunk=self.sock.recv(n-len(out))
            if not chunk: raise EOFError('Browser socket closed')
            out+=chunk
        return out
    def call(self,method,params={}):
        self.counter+=1
        raw=json.dumps({'id':self.counter,'method':method,'params':params}).encode()
        n=len(raw); mask=os.urandom(4)
        head=bytes([0x81,0x80|n]) if n<126 else bytes([0x81,0x80|126])+struct.pack('!H',n) if n<65536 else bytes([0x81,0x80|127])+struct.pack('!Q',n)
        self.sock.sendall(head+mask+bytes(v^mask[i%4] for i,v in enumerate(raw)))
        while True:
            op,length=self.read(2); length &=127
            if length==126: length=struct.unpack('!H',self.read(2))[0]
            elif length==127: length=struct.unpack('!Q',self.read(8))[0]
            result=json.loads(self.read(length))
            if result.get('id')==self.counter:
                if 'error' in result: raise RuntimeError(result['error'])
                return result['result']
    def evaluate(self,expression):
        r=self.call('Runtime.evaluate',{'expression':expression,'awaitPromise':True,'returnByValue':True})
        if 'exceptionDetails' in r: raise RuntimeError(r['exceptionDetails']['text'])
        return r['result'].get('value')

if __name__=='__main__':
    b=Browser(); mode=sys.argv[1]
    if mode=='connect':
        text=Path(r'C:\Users\Administrator\.codex\attachments\766e1f35-74bc-4124-b705-07cb94ce51e2\Pasted text.txt').read_text(encoding='utf-8')
        password=re.search(r'iLO password:\s*([^\r\n]+)',text).group(1)
        values={'ilo-address':'193.141.127.2','ilo-user':'ILO','ilo-password':password}
        b.evaluate('Object.entries('+json.dumps(values)+').forEach(([id,v])=>document.getElementById(id).value=v);document.querySelector("#connect-form button").click();')
    elif mode=='probe':
        b.evaluate("document.getElementById('reboot-ok').checked=true;document.getElementById('prepare').click();")
    elif mode=='prepare': b.evaluate("document.getElementById('prepare').click();")
    elif mode=='install': b.evaluate("document.getElementById('confirmation').value='INSTALL';document.getElementById('install').click();")
    elif mode=='screenshot':
        r=b.call('Page.captureScreenshot',{'format':'png','captureBeyondViewport':True})
        Path('.tools/ui-flow.png').write_bytes(base64.b64decode(r['data']))
    print(json.dumps(b.evaluate("({status:document.getElementById('status').textContent,primary:document.getElementById('prepare').textContent,disabled:document.getElementById('prepare').disabled,cursor:getComputedStyle(document.getElementById('prepare')).cursor,log:document.getElementById('log').textContent.slice(-2500)})"),ensure_ascii=True))
