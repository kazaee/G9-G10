import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from src.ilo import manager_profile
from src.serial_capture import SerialCapture


class Session:
    banner = b'</>hpiLO-> '
    reply = b'Virtual Serial Port Active: COM2\n'
    instances = []

    def __init__(self, args, stdin, stdout, **kwargs):
        self.args, self.stdout, self.env = args, stdout, kwargs['env']
        self.terminated = False
        self.returncode = None
        self.instances.append(self)
        outer = self

        class Input(io.BytesIO):
            def write(self, value):
                outer.stdout.write(outer.reply)
                outer.stdout.flush()
                return super().write(value)
        self.stdin = Input()
        stdout.write(self.banner)
        stdout.flush()

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminated, self.returncode = True, 0

    def wait(self, timeout=None):
        return self.returncode


class CompatibilityTests(unittest.TestCase):
    def test_identity_is_not_firmware_version(self):
        for generation in (4, 5, 6):
            profile = manager_profile([('/m', {'Model': f'iLO {generation}', 'FirmwareVersion': '2.99'})])
            self.assertEqual(profile['generation'], generation)
        self.assertIsNone(manager_profile([('/m', {'FirmwareVersion': '5.00'})])['generation'])

    def test_ilo4_and_ilo5_and_unknown_activate(self):
        for generation in (4, 5, None):
            with self.subTest(generation=generation), tempfile.TemporaryDirectory() as directory:
                with patch('src.serial_capture.subprocess.Popen', Session), patch('src.serial_capture.time.sleep'):
                    capture = SerialCapture({'address': '192.0.2.1', 'username': 'admin',
                                             'generation': generation}, 'secret', Path(directory) / 'serial.log').start()
                    process = capture.process
                    self.assertIn('-T' if generation == 4 else '-tt', process.args)
                    self.assertEqual('HostKeyAlgorithms=+ssh-rsa' in process.args, generation == 4)
                    self.assertEqual(process.stdin.getvalue(), b'vsp\r\n')
                    self.assertNotIn('secret', ' '.join(process.args))
                    self.assertTrue(process.env['DISPLAY'])
                    capture.close()
                    self.assertTrue(process.terminated)

    def test_errors_are_actionable_and_keep_log(self):
        for banner, expected in [(b'Permission denied', 'login was rejected'),
                                 (b'Connection refused', 'SSH is unreachable'),
                                 (b'no matching host key type found', 'negotiation failed'),
                                 (b'Virtual Serial Port is currently in use by another session', 'occupied'),
                                 (b'Access denied', 'denied VSP')]:
            with self.subTest(banner=banner), tempfile.TemporaryDirectory() as directory:
                with patch.object(Session, 'banner', banner), patch('src.serial_capture.subprocess.Popen', Session):
                    log = Path(directory) / 'nested/serial.log'
                    capture = SerialCapture({'address': '192.0.2.1', 'username': 'admin'}, 'secret', log)
                    with self.assertRaisesRegex(RuntimeError, expected) as raised:
                        capture.start()
                    self.assertIn(str(log.resolve()), str(raised.exception))
                    self.assertTrue(log.exists())
                    self.assertTrue(capture.process.terminated)

    def test_missing_ssh_is_reported_and_resources_closed(self):
        with tempfile.TemporaryDirectory() as directory, patch('src.serial_capture.subprocess.Popen', side_effect=FileNotFoundError('ssh not found')):
            capture = SerialCapture({'address': '192.0.2.1', 'username': 'admin'}, 'secret', Path(directory) / 'serial.log')
            with self.assertRaisesRegex(RuntimeError, 'ssh not found.*No ISO boot'):
                capture.start()
            self.assertTrue(capture.output.closed)

    def test_diagnostic_redacts_password(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'serial.log'
            path.write_text('unexpected device output: test-secret')
            capture = SerialCapture({}, 'test-secret', path)
            self.assertNotIn('test-secret', str(capture._failure('Failed.')))

    def test_ssh_exit_before_activation_fails(self):
        class Exited(Session):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self.returncode = 255
        with tempfile.TemporaryDirectory() as directory, patch('src.serial_capture.subprocess.Popen', Exited):
            capture = SerialCapture({'address': '192.0.2.1', 'username': 'admin'}, 'secret', Path(directory) / 'serial.log')
            with self.assertRaisesRegex(RuntimeError, 'code 255'):
                capture.start()

    def test_timeout_does_not_send_command_before_cli_prompt(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(Session, 'banner', b'Welcome\n'), patch('src.serial_capture.subprocess.Popen', Session), patch('src.serial_capture.time.monotonic', side_effect=[0, 1, 46]), patch('src.serial_capture.time.sleep'):
            capture = SerialCapture({'address': '192.0.2.1', 'username': 'admin', 'generation': 5}, 'secret', Path(directory) / 'serial.log')
            with self.assertRaisesRegex(RuntimeError, 'CLI prompt'):
                capture.start()

