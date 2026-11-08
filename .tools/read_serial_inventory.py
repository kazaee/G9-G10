import base64
import json
from pathlib import Path
import re
import zlib
text = Path('.tools/serial-debug.log').read_text(errors='replace')
items = re.findall(r'INVENTORY_ZLIB_BASE64=([A-Za-z0-9+/=]+)', text)
if not items:
    print('No inventory marker yet')
else:
    result = json.loads(zlib.decompress(base64.b64decode(items[-1])))
    Path('generated/live-inventory.json').write_text(json.dumps(result, indent=2))
    Path('logs/2026-10-01_00-28-28/live-inventory.json').write_text(json.dumps(result, indent=2))
    print(json.dumps({k:result[k] for k in ('selected_nic', 'candidates', 'nics', 'uefi', 'route')}, indent=2))
    print(json.dumps([{k:d.get(k) for k in ('name','size','type','model','serial','wwn')} for d in result['lsblk']['blockdevices'] if d['type']=='disk'], indent=2))
