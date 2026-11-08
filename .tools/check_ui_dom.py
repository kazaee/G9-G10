"""Read-only browser rendering check of the actual running local application."""
import json, subprocess
from html.parser import HTMLParser
from pathlib import Path

class Buttons(HTMLParser):
    def __init__(self):
        super().__init__(); self.buttons={}; self.current=None
    def handle_starttag(self,tag,attrs):
        attrs=dict(attrs)
        if tag=='button' and attrs.get('id'):
            self.current=attrs['id']; self.buttons[self.current]={'disabled':'disabled' in attrs,'text':''}
    def handle_data(self,data):
        if self.current: self.buttons[self.current]['text']+=data
    def handle_endtag(self,tag):
        if tag=='button': self.current=None

args=[r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe','--headless=new','--disable-gpu','--no-first-run',
      '--disable-background-networking','--user-data-dir='+str(Path('.tools/ui-browser').resolve()),
      '--dump-dom','--virtual-time-budget=5000','http://127.0.0.1:8766/?token=GOqZeRhHySJMsmVXPFpflKVrwChewFW8yg3dMC6Tz5k']
r=subprocess.run(args,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=40,check=True)
p=Buttons();p.feed(r.stdout.decode('utf-8'))
Path('.tools/ui-dom-result.json').write_text(json.dumps(p.buttons,ensure_ascii=True,indent=2))
print(json.dumps(p.buttons,ensure_ascii=True))
