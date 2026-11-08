import tempfile
import unittest
from pathlib import Path
from src.install_diagnostics import analyze


class InstallDiagnosticsTests(unittest.TestCase):
    def test_kernel_crash_distinguished_from_missing_completion(self):
        with tempfile.TemporaryDirectory() as directory:
            serial = Path(directory) / 'serial.log'
            http = Path(directory) / 'installer.log'
            serial.write_text('[ 805.1] BUG: unable to handle page fault\n'
                '[ 805.2] CPU: 1 PID: 42 Comm: rsync\n'
                '[ 805.3] RIP: ovl_iterate_merged [overlay]\n')
            http.write_text('GET /token/ubuntu.iso HTTP/1.1" 206 - Range=bytes=0-511\n')
            result = analyze(serial, http)
            self.assertEqual(len(result['kernel_crashes']), 1)
            self.assertIn('rsync', ' '.join(result['kernel_crashes'][0]['detail']))
            self.assertEqual(result['media']['requested_bytes'], 512)
            self.assertEqual(result['media']['http_errors'], 0)
