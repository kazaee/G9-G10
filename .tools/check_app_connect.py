import json,re,urllib.request,time
from pathlib import Path
request_text=Path(r'C:\Users\Administrator\.codex\attachments\766e1f35-74bc-4124-b705-07cb94ce51e2\Pasted text.txt').read_text(encoding='utf-8')
password=re.search(r'iLO password:\s*([^\r\n]+)',request_text).group(1)
headers={'Content-Type':'application/json','X-App-Token':'FcS8W3HbQqFMdynRoM9IrrZ1lQCK-FC7FqjmFaw2RPU'}
req=urllib.request.Request('http://127.0.0.1:8765/api/connect',data=json.dumps({'address':'193.141.127.2','username':'ILO','password':password,'verify_tls':False}).encode(),headers=headers)
print('UI connect request:',urllib.request.urlopen(req).status,flush=True)
for _ in range(60):
    state=json.load(urllib.request.urlopen(urllib.request.Request('http://127.0.0.1:8765/api/state',headers=headers)))
    assert password not in json.dumps(state)
    if not state['busy']:
        print(json.dumps({'stage':state['stage'],'server':state.get('server'),'message':state['message']},indent=2))
        break
    time.sleep(2)
else:
    print('UI connection still running')
