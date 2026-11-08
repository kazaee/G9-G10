import json
from pathlib import Path
live = json.loads(Path('generated/live-inventory.json').read_text())
report = {
    'status': 'BLOCKED_OS_NETWORK_NO_CARRIER',
    'installation': 'not-started',
    'disks_erased': False,
    'live_boot_verified': 'Ubuntu 24.04.3 UEFI',
    'inventory_transport': 'iLO COM2 serial console',
    'nics': live['nics'],
    'linux_disk_wwns': [d['wwn'] for d in live['lsblk']['blockdevices'] if d['type'] == 'disk'],
    'unit_tests_passed': 14,
    'required_next_step': 'Establish physical link on an OS NIC to the configured subnet, then repeat live discovery. Review the SSD partition plan and obtain the single INSTALL confirmation before installation.',
    'evidence': 'logs/2026-10-01_00-28-28/live-inventory.json',
    'server_after_discovery': 'powered off; virtual media ejected'
}
for path in ('generated/hardware-test-report.json','logs/2026-10-01_00-28-28/hardware-test-report.json'):
    Path(path).write_text(json.dumps(report,indent=2))
print('Saved hardware-test-report.json')
