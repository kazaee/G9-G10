"""Read-only evidence for iLO virtual media and the jump host's network route."""
import ipaddress
import json
import socket
import re
from urllib.parse import urlsplit
from .ilo import link


def route_source(address):
    parsed = urlsplit(address if '://' in address else 'https://' + address)
    if not parsed.hostname:
        raise ValueError('Missing iLO host')
    # UDP connect selects an OS route without sending a packet to the controller.
    family, kind, protocol, _, target = socket.getaddrinfo(
        parsed.hostname, parsed.port or 443, type=socket.SOCK_DGRAM)[0]
    with socket.socket(family, kind, protocol) as probe:
        probe.connect(target)
        return probe.getsockname()[0]


def media_state(data):
    """Keep operational status, excluding image URLs, tokens and credentials."""
    result = {key: data[key] for key in ('Id', 'Name', 'MediaTypes', 'Inserted', 'ConnectedVia',
                                        'WriteProtected', 'Status', 'TransferMethod') if key in data}
    result['image_configured'] = bool(data.get('Image'))
    result['boot_on_next_reset'] = {vendor: data.get('Oem', {}).get(vendor, {}).get('BootOnNextServerReset')
                                    for vendor in ('Hp', 'Hpe')
                                    if 'BootOnNextServerReset' in data.get('Oem', {}).get(vendor, {})}
    return result


def collect(ilo, discovery, config):
    result = {'media_host': config['media'].get('host'),
              'media_port': config['media'].get('port', 8080),
              'ilo_to_media_connectivity': 'unverified', 'errors': []}
    try:
        source = route_source(config['ilo']['address'])
        result['jump_host_route_source'] = source
        advertised = config['media'].get('host', '')
        print(f'[MEDIA_NETWORK] advertised={advertised}:{result["media_port"]}; '
              f'jump_host_route_source_to_ilo={source}', flush=True)
        if source != advertised:
            print('[MEDIA_NETWORK] Addresses differ. If the route source is reachable FROM iLO, '
                  'use it as the ISO host in the panel. A successful jump-host-to-iLO connection '
                  'does not verify the reverse HTTP route.', flush=True)
        result['route_source_is_loopback'] = ipaddress.ip_address(source).is_loopback
    except (OSError, ValueError) as exc:
        result['errors'].append('Route lookup: ' + str(exc))

    def read(path, label):
        if not path:
            return {}
        try:
            return ilo.get(path)
        except (OSError, RuntimeError, ValueError) as exc:
            result['errors'].append(label + ': ' + str(exc))
            return {}

    system = read(ilo.system_path, 'System')
    result['power'] = system.get('PowerState')
    result['boot'] = system.get('Boot', {})
    result['media'] = []
    for path, _ in discovery.get('media', []):
        state = media_state(read(path, 'Virtual media'))
        state['path'] = path
        result['media'].append(state)
        print('[MEDIA_STATE]', json.dumps(state, ensure_ascii=True), flush=True)

    bios = read(link(system.get('Bios')), 'BIOS')
    attrs = bios.get('Attributes', bios)
    result['bios'] = {key: value for key, value in attrs.items()
                      if key in ('BootMode', 'UsbControl', 'UsbBoot', 'VirtualInstallDisk',
                                 'SerialConsolePort', 'SerialConsoleBaudRate', 'UefiOptimizedBoot')}
    print('[BOOT_BIOS]', json.dumps(result['bios']), flush=True)
    for vendor in ('Hpe', 'Hp'):
        boot_path = link(bios.get('Oem', {}).get(vendor, {}).get('Links', {}).get('Boot'))
        if boot_path:
            settings = read(boot_path, 'Boot sources')
            result['boot_sources'] = settings.get('BootSources', [])
            print('[BOOT_SOURCES]', json.dumps(result['boot_sources']), flush=True)
            break
    return result


def connection_failures(ilo, discovery, media_urls):
    """Match only this run's URLs in advertised iLO logs; never expose URL tokens."""
    urls = [url for url in media_urls if isinstance(url, str) and url]
    if not urls:
        return []
    failures = []
    for _, manager in discovery.get('managers', []):
        service_path = link(manager.get('LogServices') or manager.get('links', {}).get('LogServices'))
        if not service_path:
            continue
        services = ilo.get(service_path).get('Members', [])
        for service in services:
            service = ilo.get(link(service)) if link(service) and not service.get('Entries') else service
            path = link(service.get('Entries'))
            visited = set()
            while path and path not in visited and len(visited) < 4:
                visited.add(path)
                entries = ilo.get(path)
                for entry in entries.get('Members', [])[-64:]:
                    if 'Message' not in entry and link(entry):
                        entry = ilo.get(link(entry))
                    message = entry.get('Message', '')
                    if 'could not establish a connection to' not in message.lower():
                        continue
                    for url in urls:
                        if not re.search(re.escape(url) + r"(?=$|[\s'\"<>.,;)])", message):
                            continue
                        host = urlsplit(url).netloc.rsplit('@', 1)[-1]
                        failures.append({'id': entry.get('Id'), 'created': entry.get('Created'),
                                         'media_host': host,
                                         'message': 'iLO could not establish a connection to the media host'})
                path = entries.get('Members@odata.nextLink')
    return failures
