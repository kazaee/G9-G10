from pathlib import Path
import io
import json
import re
import pycdlib
from src.windows_postinstall import render

root = Path('generated/windows2022-current-postfix')
root.mkdir(exist_ok=True)
source = Path('generated/current-postinstall-extracted.ps1').read_text()
match = re.search(r"ConvertFrom-Json -InputObject @'\n(.*?)\n'@", source, re.S)
if not match:
    raise RuntimeError('Could not read this installation answer image')
cfg = json.loads(match.group(1))
if cfg['address'] != '193.141.127.102' or cfg['data_disk_size'] != 4000753475584:
    raise RuntimeError('Current installation identity changed')
files = render(':'.join(re.findall('..', cfg['mac'])),
    f"{cfg['address']}/{cfg['prefix']}", cfg['gateway'], cfg['dns'],
    cfg['backup_password'], rdp_port=cfg['rdp_port'],
    report_url=cfg['report_url'])
ps = files['POSTINST.PS1']
assert '"data_disk_size": null' in ps
(root / 'POSTINST.PS1').write_text(ps, encoding='utf-8')
cmd = ('@echo off\r\n%SystemRoot%\\System32\\WindowsPowerShell\\v1.0\\powershell.exe '
       '-NoProfile -ExecutionPolicy Bypass -File "%~d0\\POSTINST.PS1"\r\n'
       'echo Review C:\\Windows\\Setup\\Scripts\\provision-postinstall.json\r\npause\r\n').encode()
iso = pycdlib.PyCdlib()
iso.new(interchange_level=3, joliet=3, vol_ident='POSTFIX')
body = ps.encode('utf-8')
iso.add_fp(io.BytesIO(body), len(body), iso_path='/POSTINST.PS1;1', joliet_path='/POSTINST.PS1')
iso.add_fp(io.BytesIO(cmd), len(cmd), iso_path='/RUNME.CMD;1', joliet_path='/RUNME.CMD')
iso.write(str(root / 'postfix.iso'))
iso.close()
print('Prepared isolated current-Windows repair ISO; data disk operation omitted.')
