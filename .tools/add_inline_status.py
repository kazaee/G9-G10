from pathlib import Path
p=Path('ui/index.html');s=p.read_text(encoding='utf-8')
needle='<div class="actions"><button id="prepare"'
assert needle in s
s=s.replace(needle,'<p id="action-hint" class="muted" role="status"></p>\n'+needle)
s=s.replace("$('status').textContent=e.message;$('status').classList.add('error');", "$('status').textContent=e.message;$('action-hint').textContent=e.message;$('status').classList.add('error');")
s=s.replace("$('install').disabled=s.busy||!s.review||dirty;", "$('install').disabled=s.busy||!s.review||dirty;$('action-hint').textContent=$('status').textContent;")
p.write_text(s,encoding='utf-8')
