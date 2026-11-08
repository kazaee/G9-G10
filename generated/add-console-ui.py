from pathlib import Path

path = Path('ui/index.html')
html = path.read_text(encoding='utf-8')
anchor = '<section class="flow-card tools-card" id="management-section">'
position = html.index('</h2>', html.index(anchor)) + len('</h2>')
block = '''<div class="console-launch"><div><span class="section-index">HTML5 / REMOTE SCREEN</span><h3>کنسول گرافیکی iLO</h3><p>تصویر و صفحه‌کلید سرور، حتی وقتی ویندوز اجراست، در پنجرهٔ رسمی iLO باز می‌شود. ورود به iLO در همان پنجره انجام می‌شود.</p></div><a id="graphical-console" class="console-link" href="#" target="_blank" rel="noopener noreferrer">باز کردن کنسول گرافیکی ↗</a></div>
<div class="verification-box"><div><h3>بررسی نصب ویندوز فعلی</h3><p>پس از ورود موفق به ویندوز در کنسول گرافیکی، عبارت VERIFIED را وارد کنید. پنل روشن‌بودن سرور و پاسخ واقعی RDP روی پورت 33096 را هم می‌سنجد.</p></div><div class="verification-actions"><input id="windows-verify-confirm" autocomplete="off" placeholder="VERIFIED" aria-label="تأیید مشاهدهٔ ورود به ویندوز"><button id="windows-verify" class="secondary operation management">ثبت نتیجهٔ نصب</button></div></div>'''
html = html[:position] + block + html[position:]
html = html.replace('کنسول گرافیکی و بازیابی رمز عبور هنوز در این نسخه پیاده‌سازی نشده‌اند.',
    'کنسول گرافیکی از دکمهٔ بالای این بخش در پنجرهٔ جدا باز می‌شود؛ بازیابی رمز عبور هنوز پیاده‌سازی نشده است.')
html = html.replace("'awaiting-verification':'", "'unverified':'تأیید نصب دریافت نشد', 'awaiting-verification':'", 1)
needle = "$('console-open').onclick=()=>action('/api/console/start',{});"
html = html.replace(needle, "$('windows-verify').onclick=()=>action('/api/verify-windows',{confirmation:$('windows-verify-confirm').value.trim()});\n" + needle, 1)
needle = "if(s.server){$('server').textContent="
html = html.replace(needle, "if(s.ilo_address){try{const address=new URL(s.ilo_address.startsWith('https://')?s.ilo_address:'https://'+s.ilo_address);if(address.protocol==='https:'&&!address.username&&!address.password)$('graphical-console').href=address.origin+'/irc.html';}catch(e){}}\n" + needle, 1)
needle = "$('graphical-console').onclick="
assert needle not in html
html = html.replace("$('console-open').onclick=", "$('graphical-console').onclick=e=>{if($('graphical-console').getAttribute('href')==='#'){e.preventDefault();$('status').textContent='ابتدا به iLO متصل شوید.';}};\n$('console-open').onclick=", 1)
path.write_text(html, encoding='utf-8')
