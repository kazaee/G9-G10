"""Read-only G6 discovery ISO. The early command never returns to storage setup."""
import json
import yaml
from .lo100_inventory import REMOTE_SCRIPT


def live_script(probe_id):
    collector = REMOTE_SCRIPT.replace("'existing-os-inventory'", "'live-inventory'").replace("'existing-os-ssh-readonly'", "'lo100-diagnostic'")
    collector = collector[:collector.index("print('LO100_INVENTORY_JSON=")]
    collector=collector.replace('rows=[]', "subprocess.call(['udevadm','settle','--timeout=60'])\nrows=[]")
    return collector + "\nresult['probe_id']=" + repr(probe_id) + r"""
import base64,zlib,time,termios
payload=('INVENTORY_ZLIB_BASE64='+base64.b64encode(zlib.compress(json.dumps(result).encode())).decode()+'\n').encode()
while True:
    print(payload.decode(),flush=True)
    for device in ('/dev/ttyS0','/dev/ttyS1'):
        for speed in (termios.B9600,termios.B115200):
            try:
                fd=os.open(device,os.O_WRONLY|os.O_NOCTTY|os.O_NONBLOCK)
                try:
                    attrs=termios.tcgetattr(fd)
                    attrs[0]=0;attrs[1]=0;attrs[2]=termios.CS8|termios.CLOCAL|termios.CREAD;attrs[3]=0
                    attrs[4]=attrs[5]=speed
                    termios.tcsetattr(fd,termios.TCSANOW,attrs)
                    os.set_blocking(fd,True)
                    offset=0
                    while offset<len(payload):offset+=os.write(fd,payload[offset:])
                    termios.tcdrain(fd)
                finally:os.close(fd)
            except OSError:pass
    time.sleep(15)
"""


def build(source, destination, probe_id):
    from .iso import build as remaster
    import pycdlib
    iso=pycdlib.PyCdlib();iso.open(str(source))
    try:
        catalog=iso.eltorito_boot_catalog
        if not catalog or catalog.validation_entry.platform_id!=0 or catalog.initial_entry.boot_indicator!=0x88:
            raise ValueError('G6 diagnostic source requires Legacy BIOS boot support')
        if 'Ubuntu-Server 24.04' not in iso.pvd.volume_identifier.decode(errors='replace'):
            raise ValueError('Use Ubuntu Server 24.04 for the G6 diagnostic')
    finally:iso.close()
    seed={'autoinstall':{'version':1,'refresh-installer':{'update':False},
        'early-commands':['python3 /cdrom/lo100-probe.py; exit 1'],
        'error-commands':['sleep infinity']}}
    remaster(source,destination,'#cloud-config\n'+yaml.safe_dump(seed),
             extra={'lo100-probe.py':live_script(probe_id)},graphical_console_only=True)
    return destination
