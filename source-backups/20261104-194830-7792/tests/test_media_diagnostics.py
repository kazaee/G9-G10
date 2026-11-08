import unittest
from unittest.mock import Mock, patch
from src.media_diagnostics import collect, media_state, route_source


class MediaDiagnosticsTests(unittest.TestCase):
    def test_status_excludes_image_url_and_unknown_oem_secrets(self):
        result = media_state({'Image': 'http://user:secret@example/token/iso', 'Inserted': True,
                              'Oem': {'Hpe': {'BootOnNextServerReset': True, 'Secret': 'not-for-report'}}})
        self.assertTrue(result['image_configured'])
        self.assertTrue(result['boot_on_next_reset']['Hpe'])
        self.assertNotIn('secret', str(result).lower())
        self.assertNotIn('/token/', str(result))

    def test_udp_route_lookup_does_not_send_data(self):
        sock = Mock()
        sock.getsockname.return_value = ('172.20.224.10', 12345)
        context = Mock()
        context.__enter__ = Mock(return_value=sock)
        context.__exit__ = Mock(return_value=False)
        with patch('src.media_diagnostics.socket.getaddrinfo', return_value=[(2, 2, 17, '', ('172.20.224.42', 443))]), patch('src.media_diagnostics.socket.socket', return_value=context):
            self.assertEqual(route_source('https://172.20.224.42'), '172.20.224.10')
        sock.send.assert_not_called()
        sock.sendto.assert_not_called()

    def test_collect_reads_advertised_bios_and_boot_sources_without_mutations(self):
        ilo = Mock(system_path='/sys')
        records = {'/sys': {'PowerState': 'On', 'Boot': {'BootSourceOverrideTarget': 'Cd'}, 'Bios': {'@odata.id': '/bios'}},
                   '/bios': {'Attributes': {'UsbBoot': 'Disabled', 'Secret': 'hidden'},
                             'Oem': {'Hpe': {'Links': {'Boot': {'@odata.id': '/boot'}}}}},
                   '/boot': {'BootSources': [{'BootString': 'iLO Virtual CD', 'UEFIDevicePath': 'device/path'}]},
                   '/cd': {'Inserted': True, 'ConnectedVia': 'URI', 'Image': 'http://example/token/iso'}}
        ilo.get.side_effect = records.__getitem__
        with patch('src.media_diagnostics.route_source', return_value='172.20.224.10'), patch('builtins.print'):
            result = collect(ilo, {'media': [('/cd', {})]}, {'ilo': {'address': '172.20.224.42'}, 'media': {'host': '185.255.88.7'}})
        self.assertEqual(result['bios'], {'UsbBoot': 'Disabled'})
        self.assertEqual(result['boot_sources'][0]['BootString'], 'iLO Virtual CD')
        self.assertEqual(result['ilo_to_media_connectivity'], 'unverified')
        ilo.request.assert_not_called()
        ilo.reset.assert_not_called()

    def test_optional_read_failures_are_recorded(self):
        ilo = Mock(system_path='/sys')
        ilo.get.side_effect = RuntimeError('HTTP 503')
        with patch('src.media_diagnostics.route_source', side_effect=OSError('no route')), patch('builtins.print'):
            result = collect(ilo, {'media': []}, {'ilo': {'address': 'target'}, 'media': {'host': 'host'}})
        self.assertEqual(len(result['errors']), 2)
        ilo.reset.assert_not_called()
