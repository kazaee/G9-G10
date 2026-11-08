import json,re,sys,time
from pathlib import Path
import yaml
from src.ilo import Ilo
content=Path(r'C:\Users\Administrator\.codex\attachments\766e1f35-74bc-4124-b705-07cb94ce51e2\Pasted text.txt').read_text(encoding='utf-8')
password=re.search(r'iLO password:\s*([^\r\n]+)',content).group(1)
config=yaml.safe_load(Path('config/test.yaml').read_text())
ilo=Ilo(config['ilo'],password,Path('.tools/recovery-ilo.log'))
discovery=json.loads(Path('generated/discovery.json').read_text())
ilo.system_path=discovery['system_path']; ilo.system=discovery['system']
if len(sys.argv)>1:
    print('Reset request:', sys.argv[1]); print(ilo.reset(sys.argv[1]))
for path in (ilo.system_path,'/redfish/v1/Managers/1/VirtualMedia/2/'):
    data=ilo.get(path)
    print(json.dumps(dict(path=path,power=data.get('PowerState'),boot=data.get('Boot',{}).get('BootSourceOverrideTarget'),
        post=data.get('Oem',{}).get('Hp',{}).get('PostState'),inserted=data.get('Inserted'),image=data.get('Image')),indent=2))
