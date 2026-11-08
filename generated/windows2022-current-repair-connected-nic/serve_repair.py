from pathlib import Path
import secrets,threading
from src.virtual_media import MediaServer
root=Path('generated/windows2022-current-repair-connected-nic')
token=secrets.token_urlsafe(24)
server=MediaServer(root/'repair.iso','185.255.88.7',8081,token,root/'repair-http.log')
server.start()
(root/'repair-url.txt').write_text(server.url)
threading.Event().wait()
