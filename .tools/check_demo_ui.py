import json
import re
import subprocess
from pathlib import Path

edge = r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe'
url = 'http://127.0.0.1:8765/?token=Nk0YR4YdUBEKQev0XkyjpArZDaKRWr2qWD6l1dh7tC0'
args = [edge, '--headless=new', '--disable-gpu', '--no-first-run',
        '--disable-background-networking', '--user-data-dir=' + str(Path('.tools/demo-browser2').resolve()),
        '--dump-dom', '--virtual-time-budget=6000', url]
result = subprocess.run(args, capture_output=True, timeout=35, check=True)
source = result.stdout.decode(errors='replace')
badge = re.search(r'<span id="badge"[^>]*>(.*?)</span>', source)
select = re.search(r'<select id="mac".*?</select>', source, re.S)
print(json.dumps({'badge': badge.group(1) if badge else None,
    'power_button': 'id="power-send"' in source,
    'media_button': 'id="media-boot"' in source,
    'console_button': 'id="console-open"' in source,
    'selected_mac': 'value="8c:dc:d4:b7:e3:08" selected' in (select.group(0) if select else '')}, ensure_ascii=True))
