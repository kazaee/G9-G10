"""Authenticate the newly generated OS account on the actual iLO console."""
import json, re, time
from pathlib import Path
import yaml
from src.serial_capture import SerialCapture

text=Path(r'C:\Users\Administrator\.codex\attachments\766e1f35-74bc-4124-b705-07cb94ce51e2\Pasted text.txt').read_text(encoding='utf-8')
ilo_password=re.search(r'iLO password:\s*([^\r\n]+)',text).group(1)
config=yaml.safe_load(Path('generated/effective-config.yaml').read_text())
credentials=json.loads(Path('generated/login-credentials.json').read_text())
run_id=json.loads(Path('generated/installation-manifest.json').read_text())['run_id']
log=Path('.tools/installed-console-login.log')
serial=SerialCapture(config['ilo'],ilo_password,log).start()
position=log.stat().st_size

def send(value):
    serial.process.stdin.write(value.encode()+b'\r'); serial.process.stdin.flush()

def wait(pattern,timeout=45):
    global position
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        raw=log.read_bytes()[position:]
        clean=re.sub(r'\x1b\[[0-?]*[ -/]*[@-~]','',raw.decode(errors='replace'))
        if re.search(pattern,clean):
            position+=len(raw)
            return clean
        time.sleep(.3)
    raise RuntimeError('Console did not reach the expected authentication stage')

try:
    send('')
    wait(r'login:\s*$')
    send(credentials['username'])
    wait(r'Password:\s*$')
    send(credentials['password'])
    wait(re.escape(credentials['username'])+r'@'+re.escape(config['network']['hostname'])+r':[^\n]*\$\s*$')
    send('cat /etc/provision-result.json')
    wait(r'"run_id":\s*"'+re.escape(run_id)+'"')
    send("printf '\\nPROVISION_%s\\n' LOGIN_OK; sudo -k; sudo -p 'PROVISION_PASSWORD:' id -u")
    sudo_output=wait(r'PROVISION_PASSWORD:\s*$|\r?\n0\r?\n')
    sudo_password_required=bool(re.search(r'PROVISION_PASSWORD:\s*$',sudo_output))
    if sudo_password_required:
        send(credentials['password'])
        wait(r'\r?\n0\r?\n')
    send('sudo -n /usr/sbin/sshd -T')
    wait(r'\r?\npasswordauthentication yes\r?\n')
    send('ss -lnt')
    wait(r':22\s')
    retry_checked=False
    if config['network'].get('deferred'):
        send("sudo -n systemctl start provision-network.service && printf '\\nPROVISION_%s\\n' NETWORK_SERVICE_OK")
        wait(r'PROVISION_NETWORK_SERVICE_OK',timeout=90)
        send('systemctl is-active provision-network.timer')
        wait(r'\r?\nactive\r?\n')
        retry_checked=True
    send("ip -br addr; ip route; printf '\\nPROVISION_%s\\n' NETWORK_SHOWN")
    network_console=wait(r'PROVISION_NETWORK_SHOWN')
    live=json.loads(Path('generated/live-inventory.json').read_text())
    hdd=next(d for d in live['lsblk']['blockdevices'] if d.get('wwn')=='0x600508b1001cb60bf3476b74719bc055')
    hdd_path='/dev/disk/by-id/wwn-0x600508b1001cb60bf3476b74719bc055'
    send('sudo -n lsblk -dn -o PTUUID '+hdd_path)
    wait(re.escape(hdd['ptuuid']))
    send('sudo -n lsblk -dn -o PARTUUID '+hdd_path+'-part1')
    wait(re.escape(hdd['children'][0]['partuuid']))
    send('exit')
    result={'run_id':run_id,'username':credentials['username'],'hostname':config['network']['hostname'],
        'console_password_login':True,'sudo_root_access':True,'sudo_password_verified':sudo_password_required,
        'ssh_password_enabled':True,'ssh_listening':True,
        'network_retry_service_checked':retry_checked,
        'network_console':network_console,'untouched_hdd_gpt_uuid':hdd['ptuuid'],
        'untouched_hdd_partition_uuid':hdd['children'][0]['partuuid'],'time':time.time()}
    Path('generated/console-login-verification.json').write_text(json.dumps(result,indent=2))
    print(json.dumps(result),flush=True)
finally:
    serial.close()
