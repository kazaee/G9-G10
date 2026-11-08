"""Operations the provisioning flow needs, independent of the controller protocol.

G6 (iLO 2) and G7 (iLO 3) have no usable Redfish, so they speak RIBCL.
G8/G9 (iLO 4) and G10 (iLO 5) speak Redfish. Workflows call only these methods.
"""


class IloDriver:
    generation = None
    protocol = ''

    def power_state(self):
        """Return 'On' or 'Off'."""
        raise NotImplementedError

    def power(self, action):
        """action: 'On', 'ForceOff' or 'ForceRestart'."""
        raise NotImplementedError

    def insert_cd(self, url):
        """Mount an HTTP ISO URL on the virtual CD and confirm insertion."""
        raise NotImplementedError

    def eject_cd(self):
        raise NotImplementedError

    def boot_cd_once(self):
        """Make the virtual CD the next boot device and restart (or power on)."""
        raise NotImplementedError

    def boot_pxe_once(self):
        """Make network (PXE) the next boot device and restart (or power on)."""
        raise NotImplementedError

    def restart_or_on(self):
        self.power('On' if self.power_state() == 'Off' else 'ForceRestart')

    def describe(self):
        return {'generation': self.generation, 'protocol': self.protocol}
