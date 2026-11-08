import json
from pathlib import Path
p=Path('generated/installation-manifest.json')
if p.exists():
    data=json.loads(p.read_text())
    data['installation']='superseded-not-approved'
    data['superseded_reason']='Fresh NIC detection, updated DNS and administrator password configuration required'
    p.write_text(json.dumps(data,indent=2))
from src.credentials import password_hash
hashed=password_hash('Test-fixture-only-123!')
assert hashed.startswith('$6$')
print('Actual OpenSSL password hashing verified; no plaintext printed')
