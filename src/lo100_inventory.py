"""Read inventory from the existing Linux OS over SSH, without reboot or writes."""
import json
import os
import re
import subprocess
import tempfile
import base64
import uuid
from pathlib import Path
from urllib.parse import urlsplit

# Compatible with older Python installations. All remote commands are read-only.
REMOTE_SCRIPT = r"""
import os,glob,json,subprocess

def read(path):
    try:
        with open(path) as f:return f.read().strip()
    except IOError:return ''

def run(args):
    try:
        p=subprocess.Popen(args,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        out,err=p.communicate()
        return out.decode('utf-8','replace') if p.returncode==0 else ''
    except OSError:return ''

rows=[]
for path in sorted(glob.glob('/sys/class/block/*')):
    name=os.path.basename(path)
    if os.path.exists(path+'/partition') or name.startswith(('loop','ram','sr','fd','dm-','md')):continue
    if not os.path.exists('/dev/'+name):continue
    properties={}
    for line in run(['udevadm','info','--query=property','--name=/dev/'+name]).splitlines():
        if '=' in line:
            key,value=line.split('=',1);properties[key]=value
    rows.append({'name':name,'type':'disk','size':int(read(path+'/size') or '0')*512,
        'model':read(path+'/device/model') or properties.get('ID_MODEL','unknown'),
        'serial':properties.get('ID_SERIAL_SHORT') or read(path+'/device/serial'),
        'wwn':properties.get('ID_WWN_WITH_EXTENSION') or properties.get('ID_WWN',''),
        'tran':properties.get('ID_BUS','unknown'),'rota':read(path+'/queue/rotational')=='1'})
nics=[]
for path in sorted(glob.glob('/sys/class/net/*')):
    name=os.path.basename(path)
    if name=='lo':continue
    nics.append({'name':name,'mac':read(path+'/address'),'carrier':read(path+'/carrier')=='1'})
result={'event':'existing-os-inventory','inventory_source':'existing-os-ssh-readonly',
    'system_uuid':read('/sys/class/dmi/id/product_uuid'),
    'server_serial':read('/sys/class/dmi/id/product_serial'),
    'server_model':read('/sys/class/dmi/id/product_name'),
    'uefi':os.path.isdir('/sys/firmware/efi'),'lsblk':{'blockdevices':rows},
    'by_id':dict((os.path.basename(p),os.path.realpath(p)) for p in glob.glob('/dev/disk/by-id/*')),
    'nics':nics,'candidates':[],'selected_nic':None}
print('LO100_INVENTORY_JSON='+json.dumps(result))
"""


def validate_target(data, expected_guid, expected_model):
    if not expected_guid:
        raise ValueError('LO100 did not supply a system GUID; target identity cannot be verified')
    try:
        expected = str(uuid.UUID(expected_guid))
        actual = str(uuid.UUID(data.get('system_uuid', '')))
    except ValueError as exc:
        raise ValueError('Cannot read the OS system UUID; use an OS account able to read DMI (usually root)') from exc
    # Legacy BMCs and Linux SMBIOS may render the first three GUID fields
    # in opposite byte order. Accept only that precise representation change.
    # The panel separately requires the physical chassis serial to match CLP.
    equivalent = str(uuid.UUID(bytes_le=uuid.UUID(expected).bytes))
    if actual not in (expected, equivalent):
        raise ValueError('OS system UUID differs from LO100; refusing inventory from a different server')
    normalize = lambda s: re.sub('[^a-z0-9]', '', s.lower())
    if expected_model and normalize(expected_model) not in normalize(data.get('server_model', '')):
        raise ValueError('OS model differs from the connected LO100 target')
    if not data.get('server_serial'):
        raise ValueError('The OS did not supply its serial number')
    if not isinstance(data.get('by_id'), dict) or not data.get('lsblk', {}).get('blockdevices'):
        raise ValueError('No disk inventory was received from the existing OS')
    return data


def collect(address, username, password, expected_guid, expected_model, known_hosts):
    target = urlsplit(address if '://' in address else 'ssh://' + address)
    if (target.scheme != 'ssh' or not target.hostname or target.hostname.startswith('-')
            or target.username or target.password or target.path not in ('','/')
            or target.query or target.fragment):
        raise ValueError('Enter the existing Linux OS host/IP, optionally with its SSH port')
    if not username or username.startswith('-') or not password:
        raise ValueError('Existing OS SSH username and password are required')
    encoded = base64.b64encode(REMOTE_SCRIPT.encode()).decode()
    # The shell receives only a fixed command and base64 of our own constant script.
    payload = "import base64;exec(compile(base64.b64decode('" + encoded + "'),'<inventory>','exec'))"
    command = 'if command -v python3 >/dev/null 2>&1; then python3 -B -c "' + payload + '"; else python -B -c "' + payload + '"; fi'
    known_hosts = Path(known_hosts).resolve(); known_hosts.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='lo100-os-read-') as directory:
        askpass = Path(directory) / ('askpass.cmd' if os.name == 'nt' else 'askpass.sh')
        askpass.write_text('@echo off\npowershell.exe -NoProfile -Command "[Console]::Write($env:LO100_OS_SSH_PASSWORD)"\n'
            if os.name=='nt' else '#!/bin/sh\nprintf "%s" "$LO100_OS_SSH_PASSWORD"\n')
        askpass.chmod(0o700)
        env = dict(os.environ, LO100_OS_SSH_PASSWORD=password, SSH_ASKPASS=str(askpass),
                   SSH_ASKPASS_REQUIRE='force', DISPLAY=os.environ.get('DISPLAY') or ':0')
        args = ['ssh','-T','-o','StrictHostKeyChecking=accept-new','-o','UserKnownHostsFile='+str(known_hosts),
                '-o','PubkeyAuthentication=no','-o','NumberOfPasswordPrompts=1','-o','ConnectTimeout=8',
                '-p',str(target.port or 22),'-l',username,target.hostname,command]
        process = subprocess.run(args, input=b'', capture_output=True, env=env, timeout=40)
    if process.returncode:
        raise RuntimeError('Read-only OS SSH inventory failed; verify OS address, credentials, SSH and Python availability')
    marker = 'LO100_INVENTORY_JSON='
    lines = process.stdout.decode(errors='replace').splitlines()
    record = next((line[len(marker):] for line in lines if line.startswith(marker)), None)
    if not record:
        raise RuntimeError('The existing OS did not return a valid inventory report')
    return validate_target(json.loads(record), expected_guid, expected_model)
