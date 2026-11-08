"""Status polling must not interrupt an active virtual-media boot."""
import urllib.error
import re
from pathlib import Path


def serial_console_argument(log):
    """The captured VSP banner identifies COM1/COM2 before the boot starts."""
    if not log or not Path(log).exists():
        return ''
    with Path(log).open('rb') as handle:
        banner = handle.read(16384).decode(errors='replace')
    return 'console=ttyS0,115200n8' if re.search(r'Virtual Serial Port\s+(?:is\s+)?Active:\s*COM1\b', banner, re.I) else ''


def discovery_boot_failure(log, elapsed, bytes_served, no_read_timeout=900, *, offset=0):
    """Report firmware boot failure without confusing slow ISO reads with a hang."""
    text = ''
    if log and Path(log).exists():
        with Path(log).open('rb') as handle:
            handle.seek(0, 2)
            handle.seek(max(offset, handle.tell() - 262144, 0))
            text = handle.read().decode(errors='replace').lower()
    text = re.sub(r'\x1b\[[0-?]*[ -/]*[@-~]', '', text)
    linux = max(text.rfind('linux version '), text.rfind('[probe] linux discovery started'),
                text.rfind('inventory_zlib_base64='))
    no_boot = text.rfind('no bootable devices were detected')
    pxe = max(text.rfind('start pxe over ipv'), text.rfind("matching the 'uefi http boot' policy"))
    hint = ('Check Virtual Media Boot on Next Reset, ISO UEFI bootability, and the media URL '
            'from the iLO network. Inspect installer.log HTTP requests and ilo.log boot diagnostics. '
            'Discovery has not started; no installation or disk wipe was requested.')
    if no_boot > linux:
        return 'Firmware reports no bootable devices; the diagnostic ISO did not boot. ' + hint
    if pxe > linux and elapsed >= 180:
        return 'Firmware fell back to PXE/HTTP network boot instead of the diagnostic ISO. ' + hint
    if linux < 0 and bytes_served == 0 and elapsed >= no_read_timeout:
        return 'No ISO data was served after the boot request. Check POST/console and iLO-to-media-host routing/firewall. ' + hint
    return None


def report(ilo, reads, bytes_served=None):
    try:
        state = ilo.get(ilo.system_path)
    except (OSError, urllib.error.URLError, RuntimeError) as exc:
        print('[PROGRESS] iLO status temporarily unavailable; ISO service and evidence capture continue:', type(exc).__name__, flush=True)
        return
    oem = state.get('Oem', {})
    print('[PROGRESS]', state.get('PowerState'), oem.get('Hpe', oem.get('Hp', {})).get('PostState'),
          'ISO reads:', reads, 'MiB served:', round(bytes_served / 1024**2, 1) if bytes_served is not None else 'unknown', flush=True)
