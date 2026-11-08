from pathlib import Path
import tempfile
import unittest
from src.progress import discovery_boot_failure, serial_console_argument


class DiscoveryBootTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.log = Path(self.temp.name) / 'serial.log'

    def test_com1_banner_selects_correct_kernel_console(self):
        self.log.write_text('Virtual Serial Port Active: COM1\n')
        self.assertEqual(serial_console_argument(self.log), 'console=ttyS0,115200n8')
        self.log.write_text('Virtual Serial Port Active: COM2\n')
        self.assertEqual(serial_console_argument(self.log), '')
        self.assertEqual(serial_console_argument(None), '')

    def test_old_pxe_and_no_boot_messages_before_new_boot_are_ignored(self):
        self.log.write_text('No bootable devices were detected.\nStart PXE over IPv4.\n')
        offset = self.log.stat().st_size
        with self.log.open('a') as handle:
            handle.write('Early system initialization\n')
        self.assertIsNone(discovery_boot_failure(self.log, 200, 0, offset=offset))
        with self.log.open('a') as handle:
            handle.write('No bootable devices were detected.\n')
        self.assertIn('no bootable', discovery_boot_failure(self.log, 200, 0, offset=offset))

    def test_no_bootable_devices_stops_waiting_even_after_small_iso_reads(self):
        self.log.write_text('\x1b[21;01HNo bootable devices were detected.\nPlease attach a UEFI bootable device.')
        self.assertIn('no bootable devices', discovery_boot_failure(self.log, 100, 4096))

    def test_pxe_fallback_is_reported_after_grace_period(self):
        self.log.write_text('>>Start PXE over IPv4.\n')
        self.assertIsNone(discovery_boot_failure(self.log, 100, 0))
        self.assertIn('fell back', discovery_boot_failure(self.log, 181, 0))

    def test_actual_linux_boot_supersedes_older_firmware_messages(self):
        self.log.write_text('No bootable devices were detected.\n>>Start PXE over IPv4.\nLinux version 6.8.0\n')
        self.assertIsNone(discovery_boot_failure(self.log, 1500, 1))

    def test_new_firmware_failure_supersedes_previous_linux_boot(self):
        self.log.write_text('Linux version 6.8.0\nNo bootable devices were detected.\n')
        self.assertIn('no bootable', discovery_boot_failure(self.log, 200, 1000))

    def test_no_reads_timeout_does_not_interrupt_slow_but_active_iso_transfer(self):
        self.assertIsNone(discovery_boot_failure(None, 899, 0))
        self.assertIn('No ISO data', discovery_boot_failure(None, 900, 0))
        self.assertIsNone(discovery_boot_failure(None, 1800, 1024))

    def test_probe_start_is_enough_even_when_media_counters_are_unavailable(self):
        self.log.write_text('[PROBE] Linux discovery started; disks will not be modified')
        self.assertIsNone(discovery_boot_failure(self.log, 1800, 0))

    def test_configurable_no_read_timeout(self):
        self.assertIsNone(discovery_boot_failure(None, 1000, 0, no_read_timeout=1200))
        self.assertIn('No ISO data', discovery_boot_failure(None, 1200, 0, no_read_timeout=1200))
