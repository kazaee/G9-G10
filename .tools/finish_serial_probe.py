import json
from pathlib import Path
import urllib.request
report = json.loads(Path('generated/live-inventory.json').read_text())
report['report_transport'] = 'ilo-serial-relayed-locally'
body = json.dumps(report).encode()
request = urllib.request.Request('http://127.0.0.1:8080/EE0uKbpvod-a4jLea3TXdyfV/event', data=body, headers={'Content-Type':'application/json'})
print('Relaying actual serial inventory to finish discovery cleanly:', urllib.request.urlopen(request, timeout=5).status)
