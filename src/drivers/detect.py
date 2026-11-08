"""Identify the iLO generation without credentials, then build its driver.

Every iLO 2-5 answers GET /xmldata?item=all anonymously with its product name
(PN) and the server model (SPN). This is read-only and touches nothing.
"""
import re
import ssl
import urllib.request

GENERATIONS = {2: 'ribcl', 3: 'ribcl', 4: 'redfish', 5: 'redfish', 6: 'redfish'}


def parse_xmldata(text):
    def tag(name):
        match = re.search(rf'<{name}>([^<]*)</{name}>', text)
        return match.group(1).strip() if match else ''
    product = tag('PN')
    match = re.search(r'iLO\s*(\d)', product)
    return {'generation': int(match.group(1)) if match else None, 'product': product,
            'firmware': tag('FWRI'), 'model': tag('SPN'), 'serial': tag('SBSN')}


def detect(address, timeout=10):
    host = address.removeprefix('https://').rstrip('/')
    context = ssl._create_unverified_context()
    try:
        context.minimum_version = ssl.TLSVersion.TLSv1
        context.set_ciphers('DEFAULT:@SECLEVEL=0')
    except (ValueError, ssl.SSLError):
        pass
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}),
                                         urllib.request.HTTPSHandler(context=context))
    with opener.open(f'https://{host}/xmldata?item=all', timeout=timeout) as response:
        info = parse_xmldata(response.read().decode('utf-8', errors='replace'))
    info['protocol'] = GENERATIONS.get(info['generation'])
    return info


def connect(config, password, **kwargs):
    info = detect(config['address'])
    if info['protocol'] == 'redfish':
        from .redfish import RedfishDriver
        return RedfishDriver(config, password, **kwargs)
    if info['protocol'] == 'ribcl':
        from .ribcl import RibclDriver
        return RibclDriver(config, password, info['generation'])
    raise RuntimeError(f'Unsupported or unknown iLO: {info["product"] or "no product name"}')
