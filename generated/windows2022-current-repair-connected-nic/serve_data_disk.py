from pathlib import Path
import secrets,threading
from src.virtual_media import MediaServer
root=Path('generated/windows2022-current-repair-connected-nic')
server=MediaServer(root/'data-disk.iso','185.255.88.7',8081,secrets.token_urlsafe(24),root/'data-disk-http.log')
server.start()
(root/'data-url.txt').write_text(server.url)
threading.Event().wait()
