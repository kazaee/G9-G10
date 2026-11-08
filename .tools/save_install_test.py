import json,time,sys
from pathlib import Path

run=Path(sys.argv[1]) if len(sys.argv)>1 else Path('logs/2026-10-01_20-53-21')
manifest=json.loads((run/'installation-manifest.json').read_text())
result=json.loads((run/'final-result.json').read_text()) if (run/'final-result.json').exists() else {'status':'installation-in-progress','installation':'not-yet-verified'}
report={
    'updated_at':time.time(), 'server_serial':'HZ75NU7565','ilo_address':'193.141.127.2',
    'workflow':'Running app.py HTTP handlers used for connect, live discovery, prepare and install',
    'browser_verification':'Actual Edge rendering and DOM state inspected; automated clicks were unavailable',
    'fixes':['Clear primary action before live discovery','Wait cursor only for active operations','Elapsed time and next step guidance',
        'Exclusive Windows listening port','Read-only iLO retries','Nonfatal progress polling timeouts',
        'Verify media state after ambiguous command timeout','Offline installation without guessing a disconnected NIC',
        'Generated administrator password and installed-account checks'],
    'automated_tests_passed':37,'unit_tests_are_not_hardware_installation_proof':True,
    'disk_binding_fix':'After WWN/capacity preflight, map reviewed by-id paths to the canonical paths required by Subiquity',
    'live_discovery':'logs/2026-10-01_15-20-50/live-inventory.json',
    'prepare':'logs/2026-10-02_00-34-48/final-result.json',
    'installation_run':str(run),'run_id':manifest['run_id'],'result':result,
    'network':manifest['network'],'selected_disks':[d['path'] for d in manifest['disks']],
    'credentials_file':'generated/login-credentials.json',
    'verification_file':str(run/'verification.json') if (run/'verification.json').exists() else None,
    'console_authentication':json.loads(Path('generated/console-login-verification.json').read_text()) if Path('generated/console-login-verification.json').exists() else None,
}
Path('generated/application-install-test.json').write_text(json.dumps(report,indent=2))
print(json.dumps({'status':result['status'],'report':'generated/application-install-test.json'}))
