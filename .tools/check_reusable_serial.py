import json,os,time
from pathlib import Path
import yaml
from src.serial_capture import SerialCapture
from src.ilo import Ilo
config=yaml.safe_load(Path('config/test.yaml').read_text())
run=Path('logs/2026-10-01_09-53-03')
client=Ilo(config['ilo'],os.environ['ILO_PASSWORD'],run/'restored-state-api.log')
cached=json.loads(Path('generated/discovery.json').read_text())
system=client.get(cached['system_path'])
media=[client.get(path) for path,data in cached['media']]
evidence={'PowerState':system['PowerState'],'PostState':system.get('Oem',{}).get('Hp',{}).get('PostState'), 'media':[{'Inserted':m.get('Inserted'),'Image':m.get('Image')} for m in media]}
(run/'restored-server-state.json').write_text(json.dumps(evidence,indent=2))
print(json.dumps(evidence),flush=True)
capture=SerialCapture(config['ilo'],os.environ['ILO_PASSWORD'],run/'restored-os-console.log').start()
print('Reusable VSP capture connected successfully',flush=True)
try:
    time.sleep(30)
finally:
    capture.close()
print('Capture closed cleanly',flush=True)
