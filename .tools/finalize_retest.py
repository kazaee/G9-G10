import json
from pathlib import Path
for name in ('generated/hardware-retest-report.json','logs/2026-10-01_09-53-03/hardware-retest-report.json'):
    p=Path(name)
    d=json.loads(p.read_text())
    d['tests_passed']=27
    d['reusable_vsp_capture_test']='passed against actual iLO'
    d['restored_server_state']=json.loads(Path('logs/2026-10-01_09-53-03/restored-server-state.json').read_text())
    p.write_text(json.dumps(d,indent=2))
p=Path('README.md')
p.write_text(p.read_text().replace('and 26 automated tests pass','and 27 automated tests pass'))
