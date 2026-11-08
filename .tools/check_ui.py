import subprocess
from pathlib import Path
args=[r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe','--headless=new','--disable-gpu','--no-first-run','--disable-background-networking','--user-data-dir='+str(Path('.tools/ui-browser').resolve()),'--window-size=1440,1700','--screenshot='+str(Path('.tools/ui-preview.png').resolve()),'--virtual-time-budget=5000','http://127.0.0.1:8766/?token=GOqZeRhHySJMsmVXPFpflKVrwChewFW8yg3dMC6Tz5k']
try:
    result=subprocess.run(args,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=40)
    print('Browser exit:',result.returncode,'Screenshot exists:',Path('.tools/ui-preview.png').exists())
except subprocess.TimeoutExpired:
    print('Browser capture timed out')
