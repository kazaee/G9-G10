"""iLO 2 (G6) and iLO 3 (G7): RIBCL XML over a TLS socket on port 443.

The raw-socket form works on every iLO from 2 to 5, unlike HTTP POST /ribcl.
"""
import re
import socket
import ssl
import time
from xml.sax.saxutils import quoteattr

from .base import IloDriver


class RibclError(RuntimeError):
    pass


def parse_responses(text):
    """Raise on the first non-zero RESPONSE status; return the raw text."""
    for status, message in re.findall(r'<RESPONSE\s+STATUS="(0x[0-9A-Fa-f]+)"\s+MESSAGE=\'([^\']*)\'', text):
        if int(status, 16) != 0:
            raise RibclError(f'RIBCL {status}: {message}')
    return text


def attribute(text, tag, name):
    match = re.search(rf'<{tag}\b[^>]*\b{name}="([^"]*)"', text)
    return match.group(1) if match else None


class RibclDriver(IloDriver):
    protocol = 'ribcl'

    def __init__(self, config, password, generation, timeout=60):
        address = config['address'].removeprefix('https://').rstrip('/')
        self.host, _, port = address.partition(':')
        self.port = int(port or 443)
        self.username, self.password = config['username'], password
        self.verify = config.get('verify_tls', False)
        self.generation = generation
        self.timeout = timeout

    def call(self, body):
        """Send one RIBCL command block and return the controller's reply."""
        script = ('<?xml version="1.0"?>\r\n<RIBCL VERSION="2.0">'
                  f'<LOGIN USER_LOGIN={quoteattr(self.username)} PASSWORD={quoteattr(self.password)}>'
                  f'{body}</LOGIN></RIBCL>\r\n')
        context = ssl.create_default_context()
        if not self.verify:
            # iLO 2/3 ship self-signed certificates and old TLS only.
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
            context.minimum_version = ssl.TLSVersion.TLSv1
            context.set_ciphers('DEFAULT:@SECLEVEL=0')
        with socket.create_connection((self.host, self.port), self.timeout) as raw:
            with context.wrap_socket(raw, server_hostname=self.host) as tls:
                tls.sendall(script.encode())
                chunks = []
                while True:
                    try:
                        data = tls.recv(65536)
                    except (ssl.SSLError, ConnectionError):
                        break
                    if not data:
                        break
                    chunks.append(data)
        return parse_responses(b''.join(chunks).decode('utf-8', errors='replace'))

    def power_state(self):
        reply = self.call('<SERVER_INFO MODE="read"><GET_HOST_POWER_STATUS/></SERVER_INFO>')
        return 'On' if attribute(reply, 'GET_HOST_POWER', 'HOST_POWER') == 'ON' else 'Off'

    def power(self, action):
        commands = {'On': '<SET_HOST_POWER HOST_POWER="Yes"/>',
                    'ForceOff': '<HOLD_PWR_BTN/>',
                    'ForceRestart': '<RESET_SERVER/>'}
        if action not in commands:
            raise ValueError(f'Unsupported power action {action}')
        self.call(f'<SERVER_INFO MODE="write">{commands[action]}</SERVER_INFO>')

    def cd_status(self):
        reply = self.call('<RIB_INFO MODE="read"><GET_VM_STATUS DEVICE="CDROM"/></RIB_INFO>')
        return {'inserted': attribute(reply, 'IMAGE_INSERTED', 'VALUE') == 'YES',
                'url': attribute(reply, 'IMAGE_URL', 'VALUE') or '',
                'boot_option': attribute(reply, 'VM_BOOT_OPTION', 'VALUE') or ''}

    def insert_cd(self, url):
        if self.cd_status()['inserted']:
            self.eject_cd()
        self.call(f'<RIB_INFO MODE="write"><INSERT_VIRTUAL_MEDIA DEVICE="CDROM" '
                  f'IMAGE_URL={quoteattr(url)}/></RIB_INFO>')
        for _ in range(12):
            state = self.cd_status()
            if state['inserted']:
                print('[OK] Virtual media reports IMAGE_INSERTED=YES')
                return state
            time.sleep(2)
        raise RuntimeError('iLO did not report the ISO inserted. Check iLO-to-media-host routing.')

    def eject_cd(self):
        self.call('<RIB_INFO MODE="write"><EJECT_VIRTUAL_MEDIA DEVICE="CDROM"/></RIB_INFO>')

    def boot_cd_once(self):
        if not self.cd_status()['inserted']:
            raise RuntimeError('Virtual CD is not inserted; refusing to reboot')
        self.call('<RIB_INFO MODE="write"><SET_VM_STATUS DEVICE="CDROM">'
                  '<VM_BOOT_OPTION VALUE="BOOT_ONCE"/><VM_WRITE_PROTECT VALUE="YES"/>'
                  '</SET_VM_STATUS></RIB_INFO>')
        if self.cd_status()['boot_option'] != 'BOOT_ONCE':
            raise RuntimeError('iLO did not retain BOOT_ONCE for the virtual CD; refusing to reboot')
        self.restart_or_on()

    def boot_pxe_once(self):
        # iLO 3 and later; some iLO 2 firmware lacks SET_ONE_TIME_BOOT.
        self.call('<SERVER_INFO MODE="write"><SET_ONE_TIME_BOOT value="NETWORK"/></SERVER_INFO>')
        self.restart_or_on()
