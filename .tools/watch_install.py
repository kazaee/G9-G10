import json,re,sys
from pathlib import Path
from datetime import datetime
root=Path(sys.argv[1]) if len(sys.argv)>1 else Path('logs/ui-2026-10-02_00-36-18-b921')
def tail(path):
    with path.open('rb') as f:
        f.seek(max(0,path.stat().st_size-65536)); text=f.read().decode(errors='replace')
    text=re.sub(r'\x1b(?:\[[0-?]*[ -/]*[@-~]|.)','',text)
    return [s.strip()[:600] for s in text.splitlines() if s.strip() and 'ZLIB_BASE64' not in s and 'password' not in s.lower()]
print(json.dumps({'time':datetime.now().isoformat(timespec='seconds'),
    'process':tail(root/'process.log')[-4:],'console':tail(root/'serial.log')[-7:]},indent=2))
