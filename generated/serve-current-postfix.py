from pathlib import Path
from secrets import token_urlsafe
from src.virtual_media import MediaServer

root = Path('generated/windows2022-current-postfix')
token = token_urlsafe(22)
server = MediaServer(root / 'postfix.iso', '185.255.88.7', 8082, token,
                     root / 'media.log')
(root / 'media-url.txt').write_text(server.url, encoding='ascii')
server.start()
server.thread.join()
