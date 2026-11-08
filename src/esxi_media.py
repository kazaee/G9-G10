"""Build a per-host ESXi optical installer without changing the vendor modules."""
import io
import ipaddress
import re
import secrets
import string
from pathlib import Path

import pycdlib


def generate_root_password():
    """Generate a random password accepted by ESXi's character-class policy."""
    classes = (string.ascii_uppercase, string.ascii_lowercase,
               string.digits, '!@%+')
    middle = [secrets.choice(group) for group in classes]
    middle += [secrets.choice('ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789!@%+')
               for _ in range(18)]
    secrets.SystemRandom().shuffle(middle)
    return secrets.choice(string.ascii_uppercase) + ''.join(middle) + secrets.choice(string.digits)


def validate_root_password(password):
    if not re.fullmatch(r'[A-Za-z0-9_!@#%+.-]{12,39}', password):
        raise ValueError('ESXi root password must be 12-39 ASCII letters, digits or _!@#%+.-')
    # passwdqc can discount characters at either edge when counting classes.
    groups = sum(any(char in group for char in password[1:-1]) for group in
                 (string.ascii_lowercase, string.ascii_uppercase,
                  string.digits, '_!@#%+.-'))
    if groups < 3:
        raise ValueError('ESXi root password needs at least three character types in its interior')
    if 'root' in password.lower():
        raise ValueError('ESXi root password must not contain the account name')


def kickstart(disk, mac, address, gateway, dns, hostname, password):
    wwn = (disk.get('wwn') or '').lower().removeprefix('0x')
    if not re.fullmatch(r'[0-9a-f]{32}', wwn):
        raise ValueError('ESXi requires a verified 16-byte logical-disk WWN')
    if not re.fullmatch(r'(?:[0-9a-f]{2}:){5}[0-9a-f]{2}', mac.lower()):
        raise ValueError('Select a verified NIC MAC for ESXi management')
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9.-]{0,62}', hostname):
        raise ValueError('Invalid ESXi hostname')
    validate_root_password(password)
    interface = ipaddress.ip_interface(address)
    if interface.version != 4:
        raise ValueError('ESXi management address must be IPv4')
    gateway = ipaddress.ip_address(gateway)
    if gateway.version != 4 or gateway not in interface.network:
        raise ValueError('ESXi gateway must be in the management subnet')
    nameservers = ','.join(str(ipaddress.ip_address(item)) for item in dns)
    if not nameservers:
        raise ValueError('ESXi management DNS is required')
    return '\n'.join([
        'vmaccepteula',
        'rootpw ' + password,
        'install --disk=naa.' + wwn + ' --overwritevmfs',
        # The installer may not have a driver/link for the selected NIC.  Do
        # not let that prevent the local-disk installation.  Resolve the MAC
        # to ESXi's own vmnic name after its installed drivers have loaded.
        '%firstboot --interpreter=busybox',
        'exec > /var/log/provisioning-network.log 2>&1',
        'set -x',
        "NICS=$(localcli network nic list)",
        "PREFERRED=$(printf '%s\\n' \"$NICS\" | awk 'tolower($0) ~ /" + mac.lower() + "/ && tolower($5) == \"up\" {print $1; exit}')",
        "LINKED=$(printf '%s\\n' \"$NICS\" | awk '$1 ~ /^vmnic[0-9]+$/ && tolower($5) == \"up\" {print $1}')",
        'NIC="$PREFERRED"',
        'if [ -z "$NIC" ]; then',
        '  COUNT=$(printf "%s\\n" "$LINKED" | awk "NF {n++} END {print n+0}")',
        '  if [ "$COUNT" -eq 1 ]; then NIC="$LINKED"; fi',
        'fi',
        'if [ -z "$NIC" ]; then echo "No unique linked NIC; selected MAC ' + mac.lower() + ' was not linked"; exit 1; fi',
        'echo "Management uplink: $NIC"',
        'UPLINKS=$(localcli network vswitch standard list --vswitch-name=vSwitch0 | awk \'/^[[:space:]]*Uplinks:/ {sub(/^[[:space:]]*Uplinks:[[:space:]]*/, ""); gsub(/,/, " "); print; exit}\')',
        'case " $UPLINKS " in *" $NIC "*) ;; *) localcli network vswitch standard uplink add --uplink-name="$NIC" --vswitch-name=vSwitch0 || exit 1 ;; esac',
        'localcli network vswitch standard policy failover set --active-uplinks="$NIC" --vswitch-name=vSwitch0 || exit 1',
        'localcli network ip interface ipv4 set --interface-name=vmk0 --ipv4=' + str(interface.ip) +
        ' --netmask=' + str(interface.netmask) + ' --type=static || exit 1',
        'localcli network ip route ipv4 add --gateway=' + str(gateway) + ' --network=default || exit 1',
        *('localcli network ip dns server add --server=' + name + ' || exit 1' for name in nameservers.split(',')),
        'localcli system hostname set --host=' + hostname + ' || exit 1',
        'for OLD in $UPLINKS; do',
        '  if [ "$OLD" != "$NIC" ]; then localcli network vswitch standard uplink remove --uplink-name="$OLD" --vswitch-name=vSwitch0 || exit 1; fi',
        'done',
        'localcli network vswitch standard list --vswitch-name=vSwitch0',
        'localcli network ip route ipv4 list',
        '%end',
        'reboot',
        '',
    ])


def build(source, destination, script):
    source, destination = Path(source), Path(destination)
    iso = pycdlib.PyCdlib()
    iso.open(str(source))
    if not iso.eltorito_boot_catalog or not any(s.platform_id == 0xef for s in iso.eltorito_boot_catalog.sections):
        iso.close()
        raise ValueError('ESXi source ISO has no UEFI boot entry')
    buffers = []
    for path in ('/BOOT.CFG;1', '/EFI/BOOT/BOOT.CFG;1'):
        content = io.BytesIO()
        iso.get_file_from_iso_fp(content, iso_path=path)
        data = content.getvalue()
        if not re.search(rb'(?m)^kernelopt=', data):
            iso.close()
            raise ValueError('ESXi boot.cfg has no kernelopt')
        data = re.sub(rb'(?m)^(kernelopt=.*)$', rb'\1 ks=cdrom:/KS.CFG', data, count=1)
        iso.rm_file(iso_path=path)
        stream = io.BytesIO(data)
        buffers.append(stream)
        iso.add_fp(stream, len(data), iso_path=path)
    stream = io.BytesIO(script.encode('ascii'))
    buffers.append(stream)
    iso.add_fp(stream, len(stream.getvalue()), iso_path='/KS.CFG;1')
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Some HPE images omit ISO9660 directory padding from the declared size;
    # pycdlib needs a few spare sectors when relocating the two boot.cfg files.
    iso.pvd.add_to_space_size(8 * 2048)
    iso.write(str(destination))
    iso.close()
    check = pycdlib.PyCdlib()
    check.open(str(destination))
    data = io.BytesIO()
    check.get_file_from_iso_fp(data, iso_path='/KS.CFG;1')
    if data.getvalue() != script.encode('ascii'):
        raise RuntimeError('Kickstart was not preserved in the generated ISO')
    for path in ('/BOOT.CFG;1', '/EFI/BOOT/BOOT.CFG;1'):
        data = io.BytesIO()
        check.get_file_from_iso_fp(data, iso_path=path)
        if b'ks=cdrom:/KS.CFG' not in data.getvalue():
            raise RuntimeError('ESXi BIOS/UEFI boot.cfg was not updated')
    check.close()
