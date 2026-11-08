import ipaddress
import re


def validate(settings, require_mac=True):
    interface = ipaddress.IPv4Interface(settings['address'])
    gateway = ipaddress.IPv4Address(settings['gateway'])
    if gateway not in interface.network or gateway == interface.ip:
        raise ValueError('Gateway must be a different address in the selected subnet')
    if interface.ip in (interface.network.network_address, interface.network.broadcast_address):
        raise ValueError('Host address cannot be the subnet or broadcast address')
    if not re.fullmatch(r'[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?', settings['hostname']):
        raise ValueError('Invalid hostname')
    if not settings['dns']:
        raise ValueError('At least one DNS address is required')
    for dns in settings['dns']:
        ipaddress.ip_address(dns)
    domain = settings.get('domain', '')
    if domain and (len(domain) > 253 or any(not re.fullmatch(r'[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?', part) for part in domain.split('.'))):
        raise ValueError('Invalid domain')
    if require_mac and not re.fullmatch(r'(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}', settings.get('mac', '')):
        raise ValueError('Select a MAC address from live-probe inventory first')


def netplan(settings):
    if settings.get('deferred') and not settings.get('mac'):
        validate(settings, require_mac=False)
        return {'version': 2, 'ethernets': {}}
    validate(settings)
    nameservers = {'addresses': settings['dns']}
    if settings.get('domain'):
        nameservers['search'] = [settings['domain']]
    return {'version': 2, 'ethernets': {'management': {
        'match': {'macaddress': settings['mac'].lower()},
        'optional': True,
        'dhcp4': False, 'dhcp6': False, 'addresses': [settings['address']],
        'routes': [{'to': 'default', 'via': settings['gateway']}], 'nameservers': nameservers}}}


def ram_iso_arguments(settings, live, iso_url):
    """Casper's native ISO download, using a NIC observed with this same kernel."""
    validate(settings)
    matches = [n for n in live['nics'] if n['mac'].lower() == settings['mac'].lower()]
    if len(matches) != 1 or not re.fullmatch(r'[a-zA-Z0-9_.-]+', matches[0]['name']):
        raise ValueError('RAM ISO boot requires one live-verified NIC matching the selected MAC')
    if not re.fullmatch(r'http://[a-zA-Z0-9.-]+:[0-9]+/[a-zA-Z0-9_-]+/ubuntu\.iso', iso_url):
        raise ValueError('Invalid ISO download URL')
    iface = ipaddress.IPv4Interface(settings['address'])
    ip_arg = f"ip={iface.ip}::{settings['gateway']}:{iface.netmask}:{settings['hostname']}:{matches[0]['name']}:none"
    return f"{ip_arg} BOOTIF=01-{settings['mac'].lower().replace(':', '-')} iso-url={iso_url}"
