from pathlib import Path
p=Path('ui/index.html')
s=p.read_text(encoding='utf-8')
s=s.replace("$('status').textContent=(names[s.stage]||s.stage)+(s.message?'\\n'+s.message:'');$('status').classList.toggle('error',s.stage==='error');", "if(s.stage==='ready'&&lastStage!=='ready')dirty=false;lastStage=s.stage;$('status').textContent=localError||((names[s.stage]||s.stage)+(dirty&&s.review?'\\nتنظیمات تغییر کرده‌اند؛ ISO را دوباره آماده کنید.':(s.message?'\\n'+s.message:'')));$('status').classList.toggle('error',s.stage==='error'||!!localError);")
s=s.replace("$('install').disabled=s.busy||!s.review;", "$('install').disabled=s.busy||!s.review||dirty;")
p.write_text(s,encoding='utf-8')
