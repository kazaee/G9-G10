"""iLO 4 (G8/G9) and iLO 5 (G10): adapter over the tested Redfish code."""
from pathlib import Path

from ..ilo import Ilo
from .. import virtual_media
from .base import IloDriver


class RedfishDriver(IloDriver):
    protocol = 'redfish'

    def __init__(self, config, password, log=Path('logs/ilo.log')):
        Path(log).parent.mkdir(parents=True, exist_ok=True)
        self.ilo = Ilo(config, password, Path(log))
        self.inventory = self.ilo.discover()
        self.generation = self.inventory['ilo_profile']['generation']
        self.cd_path = None

    def power_state(self):
        return self.ilo.get(self.ilo.system_path).get('PowerState')

    def power(self, action):
        self.ilo.reset(action)

    def insert_cd(self, url):
        self.cd_path, state = virtual_media.mount(self.ilo, self.inventory, url)
        return state

    def eject_cd(self):
        path = self.cd_path or virtual_media.cd_device(self.inventory)[0]
        virtual_media.eject(self.ilo, path)

    def boot_cd_once(self):
        if not self.cd_path:
            raise RuntimeError('Insert the ISO before requesting a CD boot')
        virtual_media.boot_once(self.ilo, self.cd_path, allow_post_power_cycle=True)

    def boot_pxe_once(self):
        boot = self.ilo.get(self.ilo.system_path).get('Boot', {})
        if 'Pxe' not in virtual_media.boot_values(boot, 'BootSourceOverrideTarget',
                                                  'BootSourceOverrideSupported'):
            raise RuntimeError('iLO does not advertise a one-time PXE boot')
        request = {'BootSourceOverrideEnabled': 'Once', 'BootSourceOverrideTarget': 'Pxe'}
        self.ilo.request('patch', self.ilo.system_path, {'Boot': request})
        stored = self.ilo.get(self.ilo.system_path).get('Boot', {})
        if any(stored.get(key) != value for key, value in request.items()):
            raise RuntimeError('iLO did not retain the one-time PXE boot; refusing to reboot')
        self.restart_or_on()

    def describe(self):
        system = self.inventory['system']
        return {**super().describe(), 'model': system.get('Model'),
                'serial': system.get('SerialNumber'),
                'firmware': self.inventory['ilo_profile']['firmware']}
