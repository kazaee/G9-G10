import json, re, sys, urllib.request
from pathlib import Path
TOKEN='GOqZeRhHySJMsmVXPFpflKVrwChewFW8yg3dMC6Tz5k'
def request(path, body=None):
    req=urllib.request.Request('http://127.0.0.1:8766/api/'+path,
        data=json.dumps(body).encode() if body is not None else None,
        headers={'X-App-Token':TOKEN,'Content-Type':'application/json'})
    return json.load(urllib.request.urlopen(req,timeout=60))
mode=sys.argv[1]
if mode=='connect':
    content=Path(r'C:\Users\Administrator\.codex\attachments\766e1f35-74bc-4124-b705-07cb94ce51e2\Pasted text.txt').read_text(encoding='utf-8')
    password=re.search(r'iLO password:\s*([^\r\n]+)',content).group(1)
    print(request('connect',{'address':'193.141.127.2','username':'ILO','password':password}))
elif mode in ('probe','prepare'):
    state=request('state')
    body=dict(state['network'],dns=', '.join(state['network']['dns']),confirm_reboot=True,offline=True,os_username='provision',mac='',plan=state.get('plan',''))
    print(request(mode,body))
elif mode=='install': print(request('install',{'confirmation':'INSTALL'}))
else:
    state=request('state')
    state['log']=state['log'][-8:]
    state.pop('storage',None)
    state.pop('existing_systems',None)
    state.pop('plan',None)
    if mode!='review': state.pop('review',None)
    print(json.dumps(state,ensure_ascii=True,indent=2))
