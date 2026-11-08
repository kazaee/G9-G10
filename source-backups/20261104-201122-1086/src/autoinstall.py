import re
import yaml
from .network import netplan
from .storage import compile_plan


def generate(config, plan, inventory, run_id, callback):
    identity = config['identity']
    if identity['username'] != 'root':
        raise ValueError('The final Linux administrator must be root')
    key = identity['ssh_public_key'].strip()
    if not key.startswith(('ssh-ed25519 ', 'ssh-rsa ', 'ecdsa-sha2-')) or '\n' in key:
        raise ValueError('An SSH public key is required for unattended verification')
    storage = compile_plan(plan, inventory)
    password = identity.get('password_hash', '!')
    if password != '!' and not re.fullmatch(r'\$6\$(?:rounds=\d+\$)?[./A-Za-z0-9]{1,16}\$[./A-Za-z0-9]{86}', password):
        raise ValueError('Expected a SHA-512 crypt administrator password hash')
    password_login = identity.get('ssh_password_login', False)
    if password_login and password == '!':
        raise ValueError('SSH password login requires an administrator password')
    data = {'version': 1, 'refresh-installer': {'update': False},
        'locale': 'en_US.UTF-8', 'keyboard': {'layout': 'us'},
        # Subiquity requires a non-root identity; the completion hook promotes
        # the requested credentials to root and removes this temporary account.
        'identity': {'hostname': config['network']['hostname'], 'username': 'provision-installer', 'password': password},
        'network': netplan(config['network']),
        'ssh': {'install-server': True, 'allow-pw': password_login, 'authorized-keys': [key]},
        'storage': {'config': storage, 'swap': {'size': 0}},
        'apt': {'geoip': False, 'fallback': 'offline-install'},
        'reporting': {'provisioner': {'type': 'webhook', 'endpoint': callback}},
        'early-commands': ['python3 /cdrom/install_hook.py preflight'],
        'late-commands': ['python3 /cdrom/install_hook.py complete'],
        'error-commands': ['python3 /cdrom/install_hook.py failed'],
        'shutdown': 'reboot'}
    if config.get('installation', {}).get('offline'):
        data.pop('reporting')
        data['apt']['mirror-selection'] = {'primary': []}
    return '#cloud-config\n' + yaml.safe_dump({'autoinstall': data}, sort_keys=False)
