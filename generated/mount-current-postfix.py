from pathlib import Path
from src.ilo import Ilo
from src.virtual_media import cd_device, mount_device

root = Path('generated/windows2022-current-postfix')
ilo = Ilo({'address': '193.141.127.2', 'username': 'ILO', 'verify_tls': False},
          '1L04p360', root / 'ilo.log')
inventory = ilo.discover()
if inventory['system'].get('PowerState') != 'On':
    raise RuntimeError('Server is not running; refusing media change')
path, current = cd_device(inventory)
print('Current CD:', current.get('Image'))
url = (root / 'media-url.txt').read_text(encoding='ascii').strip()
mount_device(ilo, (path, current), url)
print('Repair CD mounted without reboot')
