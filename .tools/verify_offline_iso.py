import io
import json
from pathlib import Path
import pycdlib
import yaml
iso = pycdlib.PyCdlib()
iso.open('generated/ubuntu-autoinstall.iso')
for name in ('install_hook.py','postboot_report.py','deferred_network.py'):
    stream = io.BytesIO()
    iso.get_file_from_iso_fp(stream, rr_path='/' + name)
    assert stream.getvalue().decode() == Path('src',name).read_text()
seed = io.BytesIO()
iso.get_file_from_iso_fp(seed, rr_path='/nocloud/user-data')
data = yaml.safe_load(seed.getvalue())['autoinstall']
assert 'reporting' not in data
assert data['apt']['mirror-selection']['primary'] == []
from src.network import netplan
config = yaml.safe_load(Path('generated/effective-config.yaml').read_text())
assert data['network'] == netplan(config['network'])
credentials = json.loads(Path('generated/login-credentials.json').read_text())
assert data['identity']['password'] == credentials['password_hash']
assert data['identity']['username'] == credentials['username'] == 'provision'
assert data['ssh']['allow-pw'] is True
packages=[]
for directory, dirs, files in iso.walk(rr_path='/pool'):
    packages.extend(f for f in files if f.startswith('openssh-server_'))
assert packages, 'SSH server package not available on local ISO'
iso.close()
print('Verified embedded offline hooks, seed, and local openssh-server package:', packages)
print('ISO bytes:',Path('generated/ubuntu-autoinstall.iso').stat().st_size)
