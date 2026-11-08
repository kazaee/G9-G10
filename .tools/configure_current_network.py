"""Set the user-selected static NIC on the verified installed Ubuntu via iLO VSP."""
import base64
import json
import re
import time
from pathlib import Path
from src.serial_capture import SerialCapture

ROOT = Path(__file__).resolve().parents[1]
attachment = Path(r'C:\Users\Administrator\.codex\attachments\766e1f35-74bc-4124-b705-07cb94ce51e2\Pasted text.txt')
secret = re.search(r'iLO password:\s*([^\r\n]+)', attachment.read_text(encoding='utf-8')).group(1)
credentials = json.loads((ROOT / 'generated/login-credentials.json').read_text())
manifest = json.loads((ROOT / 'generated/installation-manifest.json').read_text())
selection = json.loads((ROOT / 'generated/nic-selection.json').read_text())
assert manifest['run_id'] == '702813e7e0605c28769955ac7f1d64ea'
assert selection['server_serial'] == 'HZ75NU7565' and selection['mac'] == '8c:dc:d4:b7:e3:08'
net = manifest['network']
data = {'network': {'version': 2, 'ethernets': {'management': {
    'match': {'macaddress': selection['mac']}, 'optional': True,
    'dhcp4': False, 'dhcp6': False, 'addresses': [net['address']],
    'routes': [{'to': 'default', 'via': net['gateway']}],
    'nameservers': {'addresses': net['dns']}}}}}
payload = base64.b64encode(json.dumps(data).encode()).decode()
log = ROOT / '.tools/current-network-console.log'
serial = SerialCapture({'address': '193.141.127.2', 'username': 'ILO', 'ssh_port': 22}, secret, log).start()
position = log.stat().st_size

def send(value):
    serial.process.stdin.write(value.encode() + b'\r')
    serial.process.stdin.flush()

def wait(pattern, timeout=50):
    global position
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        raw = log.read_bytes()[position:]
        clean = re.sub(r'\x1b\[[0-?]*[ -/]*[@-~]', '', raw.decode(errors='replace'))
        if re.search(pattern, clean):
            position += len(raw)
            return clean
        time.sleep(.3)
    raise RuntimeError('Console did not reach the expected stage; no further commands sent')

try:
    send('')
    initial = wait(r'login:\s*$|provision@ubuntu-test:[^\n]*\$\s*$')
    if not re.search(r'provision@ubuntu-test:[^\n]*\$\s*$', initial):
        send(credentials['username'])
        wait(r'Password:\s*$')
        send(credentials['password'])
        wait(r'provision@ubuntu-test:[^\n]*\$\s*$')
    send('cat /etc/provision-result.json')
    wait(r'"run_id":\s*"' + re.escape(manifest['run_id']) + '"')
    send("sudo -k; sudo -p 'PROVISION_PASSWORD:' id -u")
    response = wait(r'PROVISION_PASSWORD:\s*$|\r?\n0\r?\n')
    if re.search(r'PROVISION_PASSWORD:\s*$', response):
        send(credentials['password'])
        wait(r'\r?\n0\r?\n')
    send("sudo -n systemctl disable --now provision-network.timer; sudo -n systemctl stop provision-network.service; printf '\\nPROVISION_TIMER_STOPPED\\n'")
    wait(r'\r?\nPROVISION_TIMER_STOPPED\r?\n')
    send("printf '%s' '" + payload + "' | base64 -d | sudo -n tee /etc/netplan/90-provision-management.yaml >/dev/null; sudo -n chmod 600 /etc/netplan/90-provision-management.yaml; sudo -n netplan generate; printf '\\nPROVISION_NETPLAN_GENERATED\\n'")
    wait(r'\r?\nPROVISION_NETPLAN_GENERATED\r?\n', timeout=60)
    send("sudo -n netplan apply; printf '\\nPROVISION_NETPLAN_APPLIED\\n'")
    wait(r'\r?\nPROVISION_NETPLAN_APPLIED\r?\n', timeout=90)
    selected = base64.b64encode(json.dumps({'mac': selection['mac'], 'interface': selection['name']}).encode()).decode()
    send("printf '%s' '" + selected + "' | base64 -d | sudo -n tee /etc/provision-network/selected.json >/dev/null; sudo -n cat /etc/netplan/90-provision-management.yaml; printf '\\nPROVISION_NETPLAN_DONE\\n'")
    observed = wait(r'\r?\nPROVISION_NETPLAN_DONE\r?\n')
    if selection['mac'] not in observed or net['address'] not in observed or net['gateway'] not in observed:
        raise RuntimeError('Written netplan did not match reviewed network settings')
    result = {'run_id': manifest['run_id'], 'interface': selection['name'], 'mac': selection['mac'],
              'address': net['address'], 'gateway': net['gateway'], 'dns': net['dns'],
              'netplan_written': True, 'netplan_generated': True, 'netplan_applied': True,
              'reachability_verified': False}
    (ROOT / 'generated/current-network-result.json').write_text(json.dumps(result, indent=2))
    print(json.dumps(result))
finally:
    serial.close()
