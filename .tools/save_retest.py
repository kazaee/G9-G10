import json
from pathlib import Path
from datetime import datetime,timezone
live_path=Path('generated/live-inventory.json')
live=json.loads(live_path.read_text())
discovery=json.loads(Path('logs/2026-10-01_09-53-03/discovery.json').read_text())
live['server_serial']=discovery['system']['SerialNumber']
live['server_identity_source']='iLO discovery from the same probe run'
live_path.write_text(json.dumps(live,indent=2))
report={'status':'LIVE_RETEST_COMPLETE_NIC_SELECTION_UNRESOLVED','installation':'not-started',
 'server_serial':live['server_serial'],'ilo_address':'193.141.127.2',
 'requested_network':{'address':'193.141.127.102/24','gateway':'193.141.127.254','dns':['4.2.2.4']},
 'nics':live['nics'],'nic_diagnostics':live['nic_diagnostics'],'existing_systems':live['existing_systems'],
 'existing_installer_log_date_utc':datetime.fromtimestamp(live['existing_systems'][0]['installer_log_times']['curtin-install.log'],timezone.utc).isoformat(),
 'existing_network_note':'The existing netplan matches en* and assigns the same static address to multiple physical interfaces.',
 'ui_entrypoint':'Start-Provisioner.cmd','ui_real_ilo_connection_test':'passed','tests_passed':26,
 'pending':['Identify intended NIC using switch MAC/physical port or establish a detectable link','Prepare updated ISO and review disks before the single INSTALL confirmation'],
 'evidence_directory':'logs/2026-10-01_09-53-03'}
for name in ('generated/hardware-retest-report.json','logs/2026-10-01_09-53-03/hardware-retest-report.json'):
    Path(name).write_text(json.dumps(report,indent=2))
print('Saved fresh hardware and existing-OS report')
