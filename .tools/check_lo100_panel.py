"""Exercise the panel in a real browser with a simulated controller. No hardware I/O."""
import json
import re
import subprocess
import tempfile
import threading
from pathlib import Path
from unittest.mock import patch
from app import Application, LocalServer, make_handler, ROOT


def main():
    profile = {'controller': 'LO100', 'model': 'DL160 G6', 'firmware': '4.26', 'advanced_license': True, 'system_guid':'8156aeda-8dfe-d511-8553-f4ce46866fde'}
    app = Application(); app.launch = lambda task: task()
    app.select_lo100_cd = lambda confirmation: {"simulated": True}
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory); app.run_directory = lambda: root
        def prepare(source, destination, hostname, password, **kwargs):
            destination.mkdir(); iso = destination / 'test.iso'; iso.write_bytes(b'test')
            (destination / 'login-credentials.json').write_text(json.dumps({'username': 'provision', 'password': password}))
            return iso
        script = """<script>window.addEventListener('load',async()=>{try{
          await action('/api/connect',{controller:'lo100',address:'172.20.224.30',username:'ILO',password:'test-secret'});await refresh();
          document.body.dataset.g6Connected=$('lo100-workflow').hidden?'hidden':'visible';
          document.body.dataset.g6ModernTools=$('management-section').hidden?'hidden':'visible';
          document.body.dataset.g6Port=$('media-port').value;
          await $('lo100-inventory').onclick();await refresh();
          document.body.dataset.g6Inventory=lastStage;
          document.body.dataset.g6Partition=$('storage-section').hidden?'hidden':'visible';
          await $('lo100-media-test').onclick();await refresh();
          $('os-password').value='test-password-123';await $('prepare').onclick();await refresh();
          document.body.dataset.g6Ready=lastStage;document.body.dataset.g6User=$('os-user').value;
          document.body.dataset.g6Guarded=$('install').disabled?'yes':'no';
          $('lo100-media-ready').checked=true;$('confirmation').value='INSTALL';await refresh();
          await $('install').onclick();await refresh();document.body.dataset.g6AfterBoot=lastStage;
          $('lo100-verified').value='VERIFIED';await $('lo100-verify').onclick();await refresh();document.body.dataset.g6Final=lastStage;
          if(localError)document.body.dataset.g6Error=localError;
          await fetch('/__test/switch-modern');await refresh();
          document.body.dataset.modernTools=$('management-section').hidden?'hidden':'visible';
          document.body.dataset.modernUser=$('os-user').value;
          document.body.dataset.modernWindows=$('os').querySelector('option[value="windows2022"]').disabled?'disabled':'enabled';
        }catch(e){document.body.dataset.g6Error=e.message;}});</script>"""
        class Handler(make_handler(app)):
            def do_GET(self):
                if self.path == '/__test/switch-modern':
                    app.lo100_profile = None; app.reviewed = None
                    app.discovery = {'system': {'Model': 'DL360 Gen10', 'SerialNumber': 'g10-test', 'PowerState': 'On'}, 'resources': {}, 'media': []}
                    app.state.update(stage='connected')
                    return self.send(200, {'ok': True})
                if self.path.split('?')[0] == '/':
                    source = (ROOT / 'ui/index.html').read_text(encoding='utf-8').replace('__TOKEN__', app.token).replace('</body>', script + '</body>')
                    return self.send(200, source.encode('utf-8'), 'text/html; charset=utf-8')
                return super().do_GET()
        live = {'inventory_source':'lo100-diagnostic','system_uuid':profile['system_guid'],
                'server_model':'DL160 G6','server_serial':'test-g6','uefi':False,
                'lsblk':{'blockdevices':[{'name':'sda','type':'disk','size':100*1024**3,'model':'SSD','serial':'test-disk','wwn':'0x1234','tran':'scsi','rota':False}]},
                'by_id':{'wwn-0x1234':'/dev/sda'},'nics':[{'name':'eth0','mac':'aa:bb:cc:dd:ee:ff','carrier':True}],
                'candidates':[],'selected_nic':None}
        probe = {'target':'172.20.224.30','media_host':'172.20.224.253','port':443,'status':'inconclusive','callback_observed':False}
        def discover_readonly():
            app.discovery['system']['SerialNumber']='test-g6'
            app.accept_lo100_inventory(live)
            return {}
        cache=root/'live-inventory.json';cache.write_text(json.dumps(live))
        with patch.object(app,'read_lo100_hardware',side_effect=discover_readonly), patch.object(app,'lo100_cache_path',return_value=cache), patch('src.lo100_media_probe.probe',return_value=probe), patch('src.lo100.Lo100') as legacy, patch('app.Ilo') as modern, patch('src.lo100_ubuntu.prepare_ubuntu', side_effect=prepare), patch('app.source_for', return_value=root / 'source.iso'):
            legacy.return_value.inspect.return_value = profile
            legacy.return_value.boot_install.return_value = {'requested_action': 'HardReset', 'installation_verified': False}
            server = LocalServer(('127.0.0.1', 0), Handler)
            threading.Thread(target=server.serve_forever, daemon=True).start()
            try:
                args = ['C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', '--headless=new', '--disable-gpu', '--no-first-run', '--disable-background-networking', '--user-data-dir=' + str(root / 'browser'), '--dump-dom', '--virtual-time-budget=7000', f'http://127.0.0.1:{server.server_port}/']
                result = subprocess.run(args, capture_output=True, timeout=35)
                checks = dict(re.findall(r'(data-(?:g6|modern)-[a-z-]+)="([^\"]*)"', result.stdout.decode('utf-8', errors='replace')))
                print(json.dumps(checks))
                assert checks.get('data-g6-port') == '443', checks
                assert checks.get('data-g6-inventory') == 'discovered', checks
                assert checks.get('data-g6-partition') == 'visible', checks
                assert checks.get('data-g6-ready') == 'ready', checks
                assert checks.get('data-g6-after-boot') == 'awaiting-verification', checks
                assert checks.get('data-g6-final') == 'completed', checks
                assert checks.get('data-g6-user') == 'provision', checks
                assert checks.get('data-g6-guarded') == 'no', checks
                assert 'data-g6-error' not in checks, checks
                assert checks.get('data-modern-tools') == 'visible', checks
                assert checks.get('data-modern-user') == 'root', checks
                assert checks.get('data-modern-windows') == 'enabled', checks
                assert legacy.return_value.boot_install.call_count == 1
                modern.assert_not_called()
                print('PASS: browser panel flow; one simulated boot; no real controller calls.')
            finally:
                server.shutdown(); server.server_close()


if __name__ == '__main__':
    main()
