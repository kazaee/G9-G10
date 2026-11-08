import copy
import unittest
from app import Application


class InventoryCacheTests(unittest.TestCase):
    def snapshot(self):
        return {'system': {'SerialNumber': 'server-a'}, 'resources': {
            '/drive': {'@odata.type': '#Drive.v1_0_0.Drive',
                       'SerialNumber': 'disk-a', 'CapacityBytes': 100000000000,
                       'Model': 'SSD'},
            '/nic': {'@odata.type': '#EthernetInterface.v1_0_0.EthernetInterface',
                     'MACAddress': 'aa:bb:cc:dd:ee:ff'}}}

    def test_same_hardware_can_reuse_linux_inventory(self):
        old = self.snapshot()
        self.assertTrue(Application.valid_live_cache(copy.deepcopy(old), old,
                        {'uefi': True, 'server_serial': 'server-a'}))

    def test_disk_replacement_invalidates_linux_inventory(self):
        old = self.snapshot()
        current = copy.deepcopy(old)
        current['resources']['/drive']['SerialNumber'] = 'disk-b'
        self.assertFalse(Application.valid_live_cache(current, old,
                         {'uefi': True, 'server_serial': 'server-a'}))

    def test_nic_replacement_invalidates_linux_inventory(self):
        old = self.snapshot()
        current = copy.deepcopy(old)
        current['resources']['/nic']['MACAddress'] = '11:22:33:44:55:66'
        self.assertFalse(Application.valid_live_cache(current, old,
                         {'uefi': True, 'server_serial': 'server-a'}))

    def test_other_server_cannot_reuse_inventory(self):
        old = self.snapshot()
        current = copy.deepcopy(old)
        current['system']['SerialNumber'] = 'server-b'
        self.assertFalse(Application.valid_live_cache(current, old,
                         {'uefi': True, 'server_serial': 'server-a'}))
