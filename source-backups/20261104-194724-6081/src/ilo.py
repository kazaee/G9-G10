"""Small Redfish adapter; follow advertised links, including iLO 4 OEM storage."""
import json
import re
import socket
import time
from urllib.parse import urlsplit
try:
    import redfish
except ImportError:
    redfish = None
import base64
import ssl
import urllib.request
import urllib.error


class StandardTransport:
    """Dependency-free fallback for hosts unable to download the Redfish SDK."""
    def __init__(self, base, username, password, verify):
        self.base = base
        self.auth = 'Basic ' + base64.b64encode(f'{username}:{password}'.encode()).decode()
        context = ssl.create_default_context() if verify else ssl._create_unverified_context()
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), urllib.request.HTTPSHandler(context=context))

    def login(self, **kwargs):
        pass

    def __getattr__(self, method):
        def request(path, body=None):
            req = urllib.request.Request(self.base + path, method=method.upper(),
                data=json.dumps(body).encode() if body is not None else None,
                headers={'Authorization': self.auth, 'Content-Type': 'application/json'})
            for attempt in range(3 if method == 'get' else 1):
                try:
                    response = self.opener.open(req, timeout=20)
                    break
                except urllib.error.HTTPError as exc:
                    response = exc
                    break
                except (OSError, urllib.error.URLError):
                    if method != 'get' or attempt == 2:
                        raise
                    time.sleep(attempt + 1)
            with response:
                text = response.read().decode()
                return type('Response', (), {'status': response.status, 'text': text,
                    'dict': json.loads(text) if text else {}})()
        return request


def link(value):
    return value.get('@odata.id', value.get('href')) if isinstance(value, dict) else None


def manager_profile(managers):
    """Read the controller generation from identity, never from firmware version."""
    for _, manager in managers:
        identity = ' '.join(str(manager.get(key, '')) for key in ('Model', 'Name', 'Description'))
        match = re.search(r'\biLO\s*([4-7])\b', identity, re.I)
        if match:
            return {'generation': int(match.group(1)), 'model': manager.get('Model', ''),
                    'firmware': manager.get('FirmwareVersion', '')}
    return {'generation': None, 'model': '', 'firmware': ''}


def links(value):
    if isinstance(value, dict):
        if link(value):
            yield link(value)
        for key, item in value.items():
            if key not in ('@odata.id', 'href', '@odata.context'):
                yield from links(item)
    elif isinstance(value, list):
        for item in value:
            yield from links(item)


class Ilo:
    def __init__(self, config, password, log):
        address = config['address']
        self.base = address if address.startswith('https://') else 'https://' + address
        host = urlsplit(self.base)
        try:
            with socket.create_connection((host.hostname, host.port or 443), 8):
                print('[OK] TCP 443 reachable')
        except OSError as exc:
            raise RuntimeError('Direct iLO TCP connection failed. Run on the jump host or configure an SSH local forward as the iLO address.') from exc
        self.log = log
        verify = config.get('verify_tls', True)
        if redfish:
            self.client = redfish.redfish_client(base_url=self.base, username=config['username'],
                password=password, timeout=20, max_retry=0, check_connectivity=False,
                cafile=(config.get('ca_file') or True) if verify else None)
        else:
            self.client = StandardTransport(self.base, config['username'], password, verify)
        print('[INFO] TLS verification:', verify)
        self.root_path = None
        for path in ('/redfish/v1/', '/rest/v1/'):
            response = self.client.get(path)
            if response.status == 200:
                self.root_path, self.root = path, response.dict
                break
        if not self.root_path:
            raise RuntimeError('Neither Redfish nor legacy REST service root is available')
        self.client.login(auth='basic')
        print('[OK] HTTPS endpoint:', self.root_path)

    def request(self, method, path, body=None):
        if not path or not path.startswith('/') or path.startswith('//'):
            raise ValueError('Only discovered same-origin API paths are accepted')
        response = getattr(self.client, method)(path, **({'body': body} if body is not None else {}))
        with self.log.open('a', encoding='utf-8') as handle:
            handle.write(f'{method.upper()} {path} -> {response.status}\n')
        if response.status not in (200, 201, 202, 204):
            raise RuntimeError(f'{method.upper()} {path}: HTTP {response.status}: {response.text}')
        return response.dict if response.text else {}

    def get(self, path):
        return self.request('get', path)

    def collection(self, value):
        path = link(value)
        data = self.get(path)
        result = []
        while True:
            members = data.get('Members', data.get('Items', data.get('links', {}).get('Member', [])))
            result.extend((link(m), self.get(link(m))) if link(m) else (None, m) for m in members)
            next_page = data.get('Members@odata.nextLink') or data.get('links', {}).get('NextPage', {}).get('href')
            if not next_page:
                return result
            data = self.get(next_page)

    def discover(self):
        systems = self.collection(self.root.get('Systems', self.root.get('links', {}).get('Systems')))
        managers = self.collection(self.root.get('Managers', self.root.get('links', {}).get('Managers')))
        if len(systems) != 1:
            raise RuntimeError('Select a single-system iLO target')
        self.system_path, self.system = systems[0]
        media = []
        for _, manager in managers:
            vm = manager.get('VirtualMedia', manager.get('links', {}).get('VirtualMedia'))
            if vm:
                media.extend(self.collection(vm))
        resources = {}
        categories = ('storage', 'ethernet', 'network', '/drives/', '/volumes/')
        pending = [p for p in links(self.system) if any(s in p.lower() for s in categories)]
        while pending:
            path = pending.pop(0)
            if path in resources or len(resources) >= 150:
                continue
            data = self.get(path)
            resources[path] = data
            pending.extend(p for p in links(data) if p not in resources and any(s in p.lower() for s in categories))
        print('[OK] Authentication successful')
        print('[OK] Server:', self.system.get('Model'), 'Power:', self.system.get('PowerState'))
        print('[OK] RAM GiB:', self.system.get('MemorySummary', {}).get('TotalSystemMemoryGiB'))
        for _, manager in managers:
            print('[OK] Manager:', manager.get('Model'), 'Firmware:', manager.get('FirmwareVersion'))
        print('[OK] Virtual media devices:', len(media))
        physical = [d for d in resources.values() if 'DiskDrive.' in d.get('@odata.type', '')]
        for disk in physical:
            print('[DISK]', disk.get('Model'), disk.get('SerialNumber'), disk.get('CapacityGB'), 'GB', disk.get('InterfaceType'), disk.get('MediaType'))
        return {'system_path': self.system_path, 'system': self.system, 'managers': managers,
                'media': media, 'resources': resources, 'root': self.root,
                'ilo_profile': manager_profile(managers)}

    def reset(self, kind='ForceRestart'):
        action = self.system.get('Actions', {}).get('#ComputerSystem.Reset')
        if action:
            allowed = action.get('ResetType@Redfish.AllowableValues', [])
            if allowed and kind not in allowed:
                raise ValueError(f'Unsupported reset {kind}: {allowed}')
            return self.request('post', action['target'], {'ResetType': kind})
        return self.request('post', self.system_path, {'Action': 'Reset', 'ResetType': kind})
