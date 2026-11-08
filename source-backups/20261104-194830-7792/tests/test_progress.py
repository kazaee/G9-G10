import unittest
import urllib.error
import urllib.request
import tempfile
import time
from pathlib import Path
from unittest.mock import Mock, patch
from src.progress import report
from src.ilo import StandardTransport
from src.virtual_media import MediaServer, eject, mount


class ProgressTests(unittest.TestCase):
    def test_media_event_acknowledges_windows_completion_report(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            image = root / 'media.iso'
            image.write_bytes(b'0' * 2048)
            server = MediaServer(image, '127.0.0.1', 0, 'job-token', root / 'media.log')
            server.start()
            try:
                url = f'http://127.0.0.1:{server.server.server_port}/job-token/event'
                body = b'{"event":"WINDOWS_POSTINSTALL","success":true}'
                request = urllib.request.Request(url, data=body, method='POST')
                self.assertEqual(urllib.request.urlopen(request, timeout=5).status, 200)
                for _ in range(20):
                    if server.events:
                        break
                    time.sleep(.01)
                self.assertEqual(server.events, [body.decode()])
            finally:
                server.close()

    def test_transient_status_timeout_does_not_abort_media_boot(self):
        ilo = Mock()
        ilo.get.side_effect = urllib.error.URLError(TimeoutError('TLS handshake timed out'))
        with patch('builtins.print') as output:
            report(ilo, 258)
        self.assertIn('continue', output.call_args.args[0])

    def test_retries_reads_but_does_not_repeat_power_or_media_mutations(self):
        transport = StandardTransport('https://example.invalid', 'test', 'test', False)
        response = Mock(status=200)
        response.read.return_value = b'{}'
        context = Mock(status=200)
        context.read.return_value = b'{}'
        context.__enter__ = Mock(return_value=response)
        context.__exit__ = Mock(return_value=False)
        transport.opener = Mock()
        transport.opener.open.side_effect = [TimeoutError(), context]
        with patch('src.ilo.time.sleep'):
            self.assertEqual(transport.get('/test').status, 200)
        self.assertEqual(transport.opener.open.call_count, 2)
        transport.opener.open.reset_mock()
        transport.opener.open.side_effect = TimeoutError()
        with self.assertRaises(TimeoutError):
            transport.post('/reset', body={})
        self.assertEqual(transport.opener.open.call_count, 1)

    def test_eject_timeout_is_success_only_if_readback_confirms_empty_media(self):
        ilo = Mock()
        media = {'Inserted': True, 'Image': 'http://mounted',
                 'Actions': {'#VirtualMedia.EjectMedia': {'target': '/eject'}}}
        ilo.get.side_effect = [media, {'Inserted': False, 'Image': ''}]
        ilo.request.side_effect = TimeoutError()
        eject(ilo, '/media')
        self.assertEqual(ilo.request.call_count, 1)
        ilo.request.reset_mock()
        ilo.request.side_effect = [RuntimeError('HTTP 500'), None]
        ilo.get.side_effect = [media, {'Inserted': True, 'Image': 'http://still-mounted'},
                               {'Inserted': False, 'Image': None}]
        eject(ilo, '/media')
        self.assertEqual([call.args[0] for call in ilo.request.call_args_list], ['post', 'patch'])

    def test_mount_timeout_checks_state_without_repeating_insert(self):
        ilo = Mock()
        url = 'http://example.invalid/iso'
        media = {'MediaTypes': ['CD'], 'Actions': {'#VirtualMedia.InsertMedia': {'target': '/insert'}}}
        ilo.request.side_effect = TimeoutError()
        ilo.get.return_value = {'Inserted': True, 'Image': url}
        self.assertEqual(mount(ilo, {'media': [('/media', media)]}, url)[1]['Image'], url)
        self.assertEqual(ilo.request.call_count, 1)
