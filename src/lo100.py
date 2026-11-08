"""Isolated Lights-Out 100 discovery, console preparation and approved boot actions.

LO100 is not a Redfish/iLO4 controller. Only boot_install may change power, after explicit installation and media confirmation. No firmware or BIOS writes.
"""
import io
import json
import re
import subprocess
import time
import urllib.error
import urllib.request
import zipfile
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit, urlencode


class Page(HTMLParser):
    def __init__(self, source):
        super().__init__()
        self.text, self.params = [], {}
        self.feed(source)

    def handle_data(self, text):
        self.text.append(text)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag.lower() == 'param' and attrs.get('name'):
            self.params[attrs['name'].lower()] = attrs.get('value', '')

    @property
    def plain(self):
        return re.sub(r'\s+', ' ', ' '.join(self.text)).strip()


def parse_summary(source):
    if 'lights-out100' not in source.lower():
        raise ValueError('This endpoint has not been identified as Lights-Out 100')
    text = Page(source).plain
    firmware = re.search(r'Firmware Version:\s*(\S+)', text)
    model = re.search(r'Description:\s*(.*?)\s+System GUID:', text)
    return {'controller': 'LO100', 'firmware': firmware.group(1) if firmware else None,
            'model': model.group(1) if model else None,
            'system_guid': (re.search(r'System GUID:\s*([0-9A-Fa-f-]{36})',text).group(1) if re.search(r'System GUID:\s*([0-9A-Fa-f-]{36})',text) else None),
            'capabilities': {'redfish': False, 'automatic_media_mount': False,
                             'automatic_power': False, 'web_disk_inventory': False,
                             'vendor_java_console': True}}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, newurl):
        raise ValueError('Redirects are disabled for the legacy management controller')


class BootRequestUncertain(RuntimeError):
    """The approved boot may have applied; do not repeat it automatically."""


class Lo100:
    def __init__(self, address, username, password):
        parsed = urlsplit(address if '://' in address else 'http://' + address)
        if parsed.scheme != 'http' or not parsed.hostname or parsed.username or parsed.password or parsed.path not in ('', '/') or parsed.query or parsed.fragment:
            raise ValueError('LO100 requires a plain host address or explicit http://host; credentials belong in the prompt')
        self.host = parsed.hostname
        self.base = 'http://' + parsed.netloc
        manager = urllib.request.HTTPPasswordMgrWithDefaultRealm()
        manager.add_password(None, self.base + '/', username, password)
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect(),
                                                 urllib.request.HTTPDigestAuthHandler(manager))

    def read(self, path, limit=200000):
        if path not in ('/index.html', '/hwinv.html', '/kvms.html', '/lkey.html', '/M2.JAR', '/chassis.html'):
            raise ValueError('Only known read-only LO100 resources are allowed')
        for attempt in range(3):
            try:
                with self.opener.open(self.base + path, timeout=15) as response:
                    if urlsplit(response.url).netloc != urlsplit(self.base).netloc:
                        raise ValueError('LO100 redirected to a different origin')
                    value = response.read(limit + 1)
                    if len(value) > limit:
                        raise ValueError('Unexpectedly large LO100 resource')
                    return value
            except urllib.error.HTTPError:
                # Never retry rejected credentials or management HTTP errors.
                raise
            except (ConnectionError, TimeoutError, urllib.error.URLError):
                if attempt == 2:
                    raise
                time.sleep(0.5 * (attempt + 1))

    def inspect(self):
        result = parse_summary(self.read('/index.html').decode(errors='replace'))
        license_text = Page(self.read('/lkey.html').decode(errors='replace')).plain
        result['advanced_license'] = 'LO100 Advanced' in license_text
        result['kvm_url'] = self.base + '/kvms.html'
        result['disk_inventory'] = 'not exposed by the inspected LO100 hardware page'
        return result

    def boot_install(self, *, confirmation, media_confirmed):
        """One operator-approved action, using the form read from this LO100.

        The query changes power. It must never use the retrying read() helper.
        """
        if confirmation != 'INSTALL' or media_confirmed is not True:
            raise ValueError('Confirm INSTALL and local ISO attachment before requesting server boot')
        source = self.read('/chassis.html').decode(errors='replace')
        text = Page(source).plain
        status = re.search(r'Power Status:\s*(ON|OFF)\b', text, re.I)
        if not status or not re.search(r'name=[\"\']PwrCtrl[\"\']', source, re.I):
            raise ValueError('Cannot verify the LO100 power form; no power command was sent')
        action = 'HardReset' if status.group(1).upper() == 'ON' else 'PowerUp'
        if not re.search(r'value=[\"\']' + action + r'[\"\']', source, re.I):
            raise ValueError('Required power action is not advertised by this LO100')
        query = urlencode({'PwrCtrl': action, 'Button1': 'Apply'})
        try:
            with self.opener.open(self.base + '/chassis.html?' + query, timeout=15) as response:
                response.read(200000)
        except urllib.error.HTTPError as exc:
            raise RuntimeError('LO100 rejected the boot request: HTTP ' + str(exc.code) + '. No automatic retry.') from exc
        except OSError as exc:
            raise BootRequestUncertain('LO100 boot response was ambiguous. The action was NOT retried. '
                               'Check the Java console before requesting another boot.') from exc
        return {'requested_action': action, 'installation_verified': False}

    def prepare_console(self, destination):
        data = self.read('/M2.JAR', 12000000)
        destination = Path(destination).resolve()
        destination.mkdir(parents=True, exist_ok=True)
        jar = destination / 'M2.JAR'
        validate_console_jar(data)
        jar.write_bytes(data)
        return jar


def validate_console_jar(data):
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        if 'com/serverengines/mahogany/MahoganyViewer.class' not in archive.namelist():
            raise ValueError('Expected vendor console class is missing')
        manifest = archive.read('META-INF/MANIFEST.MF').decode(errors='replace')
        if 'Main-Class: com.serverengines.mahogany.MahoganyViewer' not in manifest:
            raise ValueError('Expected vendor standalone launcher is missing')


def java8(java='java'):
    result = subprocess.run([str(java), '-version'], capture_output=True, text=True, timeout=10)
    if result.returncode or 'version "1.8.' not in result.stdout + result.stderr:
        raise ValueError('An installed Java 8 runtime with AppletViewer is required')
    return str(java)


def console_command(jar, java='java'):
    jar = Path(jar).resolve()
    validate_console_jar(jar.read_bytes())
    return [java8(java), '-jar', str(jar)]
