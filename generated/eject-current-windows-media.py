from pathlib import Path
from src.ilo import Ilo
from src.virtual_media import eject

root = Path('generated/windows2022-current-postfix')
ilo = Ilo({'address': '193.141.127.2', 'username': 'ILO', 'verify_tls': False},
          '1L04p360', root / 'ilo.log')
inventory = ilo.discover()
for path, _ in inventory['media']:
    state = ilo.get(path)
    image = state.get('Image') or ''
    if ('185.255.88.7:8082/' in image or
            ('185.255.88.7:8080/' in image and '/answer.img' in image)):
        eject(ilo, path)
        print('Ejected', path)
    elif image:
        print('Left unrelated media attached:', path)
