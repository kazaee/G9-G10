from pathlib import Path
import re

path = Path('ui/index.html')
html = path.read_text(encoding='utf-8')
assert 'class="hero-card"' not in html

panel = html.index('<section class="panel">')
os_heading = html.index('<h2>', panel)
network_heading = html.index('<h3>', os_heading)
account_heading = html.index('<h3 id="admin-heading">', network_heading)
storage_heading = html.index('<h3>', account_heading + 4)
review = html.index('<section id="review"', storage_heading)
management = html.index('<details><summary>', review)
logs_heading = html.index('<h3>', management)
panel_end = html.index('</section></main>', logs_heading)

def heading_text(at):
    match = re.match(r'<h[23][^>]*>(.*?)</h[23]>', html[at:])
    assert match
    return re.sub(r'<[^>]+>', '', match.group(1))

nav_items = [
    ('os-section', heading_text(os_heading)),
    ('network-section', heading_text(network_heading)),
    ('account-section', heading_text(account_heading)),
    ('storage-section', heading_text(storage_heading)),
    ('review', heading_text(html.index('<h3>', review))),
    ('management-section', 'ابزارهای مدیریت'),
    ('activity-section', heading_text(logs_heading)),
]
nav = '<nav class="side-nav" aria-label="راهبری پنل"><span class="nav-caption">بخش‌های پنل</span>'
for number, (target, label) in enumerate(nav_items, 1):
    nav += f'<a href="#{target}"><b>{number:02}</b><span>{label}</span><span class="nav-arrow" aria-hidden="true">↗</span></a>'
nav += '</nav>'
footer = html.index('<footer>', panel - 500)
html = html[:footer] + nav + html[footer:]

# Recompute offsets after inserting the navigation block.
panel = html.index('<section class="panel">')
os_heading = html.index('<h2>', panel)
network_heading = html.index('<h3>', os_heading)
account_heading = html.index('<h3 id="admin-heading">', network_heading)
storage_heading = html.index('<h3>', account_heading + 4)
review = html.index('<section id="review"', storage_heading)
management = html.index('<details><summary>', review)
logs_heading = html.index('<h3>', management)
panel_end = html.index('</section></main>', logs_heading)

inserts = {
    panel + len('<section class="panel">'): '<section class="hero-card"><div class="hero-intro"><span class="eyebrow">SERVER PROVISIONING / CONTROL CENTER</span><h2>مرکز مدیریت نصب سرور</h2><p>از شناسایی سخت‌افزار تا بازبینی دیسک‌ها، نصب و گزارش نهایی</p></div>',
    os_heading: '</section><section class="flow-card" id="os-section"><div class="section-index">01 / IMAGE</div>',
    network_heading: '</section><section class="flow-card" id="network-section"><div class="section-index">02 / NETWORK</div>',
    account_heading: '</section><section class="flow-card" id="account-section"><div class="section-index">03 / ACCESS</div>',
    storage_heading: '</section><section class="flow-card" id="storage-section"><div class="section-index">04 / STORAGE</div>',
    review: '</section>',
    management: '<section class="flow-card tools-card" id="management-section"><div class="section-index">05 / MANAGEMENT</div><h2>ابزارهای مدیریت سرور</h2>',
    logs_heading: '</section><section class="flow-card" id="activity-section"><div class="section-index">06 / ACTIVITY</div>',
    panel_end: '</section>',
}
for pos, value in sorted(inserts.items(), reverse=True):
    html = html[:pos] + value + html[pos:]

path.write_text(html, encoding='utf-8')
