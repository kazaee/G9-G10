import tempfile
import unittest
from pathlib import Path
from unittest import mock

from src import virtual_media
from src.drivers.detect import parse_xmldata, GENERATIONS
from src.drivers.ribcl import RibclDriver, RibclError, parse_responses


class DetectTests(unittest.TestCase):
    def test_xmldata_generations(self):
        for product, generation in (('Integrated Lights-Out 2 (iLO 2)', 2),
                                    ('Integrated Lights-Out 3 (iLO 3)', 3),
                                    ('Integrated Lights-Out 4 (iLO 4)', 4),
                                    ('Integrated Lights-Out 5 (iLO 5)', 5)):
            info = parse_xmldata(f'<RIMP><MP><PN>{product}</PN><FWRI>1.0</FWRI></MP>'
                                 f'<HSI><SPN>ProLiant DL360</SPN><SBSN>CZ1</SBSN></HSI></RIMP>')
            self.assertEqual(info['generation'], generation)
            self.assertEqual(info['model'], 'ProLiant DL360')
        self.assertEqual(GENERATIONS[3], 'ribcl')
        self.assertEqual(GENERATIONS[4], 'redfish')


class RibclTests(unittest.TestCase):
    def setUp(self):
        self.driver = RibclDriver({'address': '10.0.0.2', 'username': 'u'}, 'p', 3)

    def test_error_status_raises(self):
        parse_responses("<RESPONSE STATUS=\"0x0000\" MESSAGE='No error' />")
        with self.assertRaises(RibclError):
            parse_responses("<RESPONSE STATUS=\"0x0001\" MESSAGE='Syntax error' />")

    def test_boot_once_requires_inserted_media(self):
        self.driver.call = mock.Mock(return_value='<IMAGE_INSERTED VALUE="NO"/>')
        with self.assertRaises(RuntimeError):
            self.driver.boot_cd_once()

    def test_boot_once_sets_boot_once_then_restarts(self):
        replies = iter(['<IMAGE_INSERTED VALUE="YES"/>', '', '<IMAGE_INSERTED VALUE="YES"/>'
                        '<VM_BOOT_OPTION VALUE="BOOT_ONCE"/>', '<GET_HOST_POWER HOST_POWER="ON"/>', ''])
        sent = []
        self.driver.call = lambda body: sent.append(body) or next(replies)
        self.driver.boot_cd_once()
        self.assertIn('BOOT_ONCE', sent[1])
        self.assertIn('RESET_SERVER', sent[-1])


class ExternalMediaTests(unittest.TestCase):
    def test_publishes_to_external_server_and_cleans_up(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            iso = tmp / 'source.iso'
            iso.write_bytes(b'abcdef')
            config = tmp / 'media-server.yaml'
            config.write_text(f'base_url: http://media.local/p/\npublish_dir: {tmp / "pub"}\n')
            with mock.patch.object(virtual_media, 'EXTERNAL_CONFIG', config):
                server = virtual_media.MediaServer(iso, '127.0.0.1', 0, 'tok', tmp / 'log',
                                                   patches=[(1, b'ZZ')])
            server.start()
            self.assertEqual(server.url, 'http://media.local/p/tok/ubuntu.iso')
            self.assertEqual((tmp / 'pub/tok/ubuntu.iso').read_bytes(), b'aZZdef')
            self.assertEqual(iso.read_bytes(), b'abcdef')
            server.close()
            self.assertFalse((tmp / 'pub/tok').exists())


if __name__ == '__main__':
    unittest.main()
