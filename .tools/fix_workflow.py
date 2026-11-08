from pathlib import Path
p = Path('ui/index.html')
s = p.read_text(encoding='utf-8')
s = s.replace('cursor:wait}.row', 'cursor:not-allowed}body.busy button:disabled{cursor:wait}.row')
s = s.replace("lastStage='',localError='';", "lastStage='',localError='',hasLive=false;")
s = s.replace("$('prepare').onclick=()=>action('/api/prepare',settings());", "$('prepare').onclick=()=>action(hasLive?'/api/prepare':'/api/probe',settings());")
s = s.replace("$('prepare').disabled=s.busy||!s.nics;", "hasLive=!!s.nics;$('prepare').disabled=s.busy||!s.server;$('prepare').textContent=hasLive?'بررسی طرح و ساخت ISO نصب':'شناسایی سرور و پیشنهاد پارتیشن';document.body.classList.toggle('busy',s.busy);if(s.busy&&s.started_at)$('status').textContent+='\\nزمان سپری‌شده: '+Math.floor((Date.now()/1000-s.started_at)/60)+' دقیقه';")
p.write_text(s, encoding='utf-8')
