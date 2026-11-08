"""Serve one ISO with byte ranges and mount it through discovered actions."""
import http.server
import re
import socket
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit


class ExclusiveMediaServer(http.server.ThreadingHTTPServer):
    # On Windows, SO_REUSEADDR can let unrelated panel processes accept the
    # same port. Requests then hit the wrong per-run token and return 404.
    allow_reuse_address = False

    def server_bind(self):
        if hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


class MediaServer:
    def __init__(self, iso, host, port, token, log, extras=None, patches=None):
        self.iso = iso.resolve()
        self.route = f'/{token}/ubuntu.iso'
        self.url = f'http://{host}:{port}{self.route}'
        self.extra_urls = {name: f'http://{host}:{port}/{token}/{name}' for name in (extras or {})}
        self.files = {self.route: self.iso}
        self.files.update({f'/{token}/{name}': Path(path).resolve() for name, path in (extras or {}).items()})
        self.patches = patches or []
        self.events = []
        self.downloads = 0
        self.bytes_served = 0
        self.iso_bytes_served = 0
        owner = self

        class Handler(http.server.BaseHTTPRequestHandler):
            protocol_version = 'HTTP/1.1'
            def handle(self):
                try:
                    super().handle()
                except ConnectionError:
                    # iLO may close a keep-alive socket between range requests.
                    pass

            def log_message(self, fmt, *args):
                with log.open('a', encoding='utf-8') as f:
                    f.write(f'{datetime.now(timezone.utc).isoformat()} {self.client_address[0]} {fmt % args} Range={self.headers.get("Range", "none")}\n')

            def do_HEAD(self):
                self.send_iso(False)

            def do_GET(self):
                self.send_iso(True)

            def do_POST(self):
                if self.path != f'/{token}/event':
                    self.send_error(404)
                    return
                try:
                    size = int(self.headers.get('Content-Length', '0'))
                except ValueError:
                    self.send_error(400)
                    return
                if size < 0 or size > 2_000_000:
                    self.send_error(413)
                    return
                body = self.rfile.read(size).decode('utf-8', errors='replace')
                with log.open('a', encoding='utf-8') as f:
                    f.write(body + '\n')
                self.send_response(200)
                self.send_header('Content-Length', '0')
                self.end_headers()
                owner.events.append(body)

            def send_iso(self, content):
                source = owner.files.get(urlsplit(self.path).path)
                if source is None:
                    self.send_error(404)
                    return
                length = source.stat().st_size
                start, end = 0, length - 1
                range_header = self.headers.get('Range')
                if range_header:
                    match = re.fullmatch(r'bytes=(\d*)-(\d*)', range_header)
                    if not match or not any(match.groups()):
                        self.send_error(416)
                        return
                    left, right = match.groups()
                    if left:
                        start = int(left)
                        end = min(int(right), end) if right else end
                    else:
                        start = max(0, length - int(right))
                    if start > end or start >= length:
                        self.send_response(416)
                        self.send_header('Content-Range', f'bytes */{length}')
                        self.send_header('Content-Length', '0')
                        self.end_headers()
                        return
                self.send_response(206 if range_header else 200)
                self.send_header('Content-Type', 'application/octet-stream')
                self.send_header('Accept-Ranges', 'bytes')
                self.send_header('Content-Length', str(end - start + 1))
                if range_header:
                    self.send_header('Content-Range', f'bytes {start}-{end}/{length}')
                self.end_headers()
                if content:
                    owner.downloads += 1
                    try:
                        with source.open('rb') as f:
                            f.seek(start)
                            remaining = end - start + 1
                            while remaining:
                                data = f.read(min(1024 * 1024, remaining))
                                if not data:
                                    break
                                if source == owner.iso:
                                    chunk_start = f.tell() - len(data)
                                    for patch_start, patch_data in owner.patches:
                                        left = max(chunk_start, patch_start)
                                        right = min(chunk_start + len(data), patch_start + len(patch_data))
                                        if left < right:
                                            modified = bytearray(data)
                                            modified[left - chunk_start:right - chunk_start] = patch_data[left - patch_start:right - patch_start]
                                            data = modified
                                self.wfile.write(data)
                                owner.bytes_served += len(data)
                                if source == owner.iso:
                                    owner.iso_bytes_served += len(data)
                                remaining -= len(data)
                    except (ConnectionError, OSError):
                        pass

        self.server = ExclusiveMediaServer(('0.0.0.0', port), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def start(self):
        self.thread.start()
        print('[INFO] iLO media URL:', self.url)

    def close(self):
        self.server.shutdown()
        self.server.server_close()


def cd_device(inventory):
    choices = [(p, d) for p, d in inventory['media'] if set(d.get('MediaTypes', [])) & {'CD', 'DVD'}]
    if len(choices) != 1:
        raise RuntimeError('Expected one CD/DVD virtual-media device')
    return choices[0]


def floppy_device(inventory):
    choices = [(p, d) for p, d in inventory['media'] if 'Floppy' in d.get('MediaTypes', [])]
    if len(choices) != 1:
        raise RuntimeError('Expected one iLO floppy/USB virtual-media device')
    return choices[0]


def action(data, suffix):
    groups = [data.get('Actions', {})]
    groups.extend(v.get('Actions', {}) for v in data.get('Oem', {}).values())
    return next((v['target'] for group in groups for k, v in group.items() if k.endswith(suffix)), None)


def eject(ilo, path):
    data = ilo.get(path)
    if not data.get('Inserted') and not data.get('Image'):
        return
    target = action(data, '.EjectMedia') or action(data, '.EjectVirtualMedia')
    try:
        if target:
            ilo.request('post', target, {})
        else:
            ilo.request('patch', path, {'Image': None})
    except (OSError, RuntimeError):
        # Read back before the alternate PATCH: the first action may have
        # applied despite its timeout or iLO 500 response.
        state = ilo.get(path)
        if not state.get('Inserted') and not state.get('Image'):
            print('[OK] Ejection confirmed by readback after ambiguous response')
            return
        if not target:
            raise
        # iLO 4 documents Image as PATCHable and null as no mounted image.
        ilo.request('patch', path, {'Image': None})
    state = ilo.get(path)
    if state.get('Inserted') or state.get('Image'):
        raise RuntimeError('iLO still reports virtual media inserted after ejection')


def mount(ilo, inventory, url):
    return mount_device(ilo, cd_device(inventory), url)


def mount_device(ilo, device, url):
    path, data = device
    if data.get('Inserted') or data.get('Image'):
        eject(ilo, path)
    target = action(data, '.InsertMedia') or action(data, '.InsertVirtualMedia')
    try:
        if target:
            body = {'Image': url}
            if target == action(data, '.InsertMedia'):
                body.update(Inserted=True, WriteProtected=True)
            ilo.request('post', target, body)
        else:
            ilo.request('patch', path, {'Image': url})
    except OSError:
        print('[INFO] Media command response timed out; checking actual insertion state')
    for _ in range(12):
        state = ilo.get(path)
        if state.get('Inserted') and state.get('Image') == url:
            print('[OK] Virtual media reports Inserted=true')
            return path, state
        time.sleep(2)
    raise RuntimeError('iLO did not report the ISO inserted. Check iLO-to-media-host routing, firewall and HTTP requests.')


def boot_values(boot, property_name, legacy_name):
    """Prefer Redfish annotations (iLO 5), retaining iLO 4 field support."""
    key = property_name + '@Redfish.AllowableValues'
    values = boot[key] if key in boot else boot.get(legacy_name, [])
    return values if isinstance(values, list) else []


def boot_once(ilo, media_path, *, require_virtual_cd=False, allow_post_power_cycle=False):
    """A diagnostic reboot may recover the explicit POST lock with one cold cycle."""
    try:
        return _boot_once(ilo, media_path, require_virtual_cd=require_virtual_cd)
    except RuntimeError as exc:
        if not re.search(r'\biLO\.\d+\.\d+\.UnableToModifyDuringSystemPOST\b', str(exc)):
            raise
        if not allow_post_power_cycle:
            raise RuntimeError('iLO cannot change virtual-media boot settings during POST. '
                               'No automatic power-off was requested. Power the server off, '
                               'then retry the approved boot operation.') from exc
        state = ilo.get(ilo.system_path)
        if state.get('PowerState') == 'On':
            print('[INFO] POST blocks boot configuration; diagnostic recovery: one ForceOff, '
                  'wait for Off, then configure media and power on.', flush=True)
            ilo.reset('ForceOff')
            deadline = time.monotonic() + 60
            while True:
                state = ilo.get(ilo.system_path)
                if state.get('PowerState') == 'Off':
                    break
                if time.monotonic() >= deadline:
                    raise RuntimeError('Diagnostic POST recovery timed out waiting for power Off; '
                                       'boot settings were not retried and power-on was not requested.') from exc
                time.sleep(2)
        elif state.get('PowerState') != 'Off':
            raise RuntimeError('Cannot recover POST lock with unknown/transitional power state; '
                               'no power change was requested.') from exc
        print('[INFO] Power Off confirmed; retrying virtual-CD boot configuration once.', flush=True)
        # Revalidate mounted media and every boot setting. Never loop on a second failure.
        return _boot_once(ilo, media_path, require_virtual_cd=require_virtual_cd)


def _boot_once(ilo, media_path, *, require_virtual_cd=False):
    data = ilo.get(media_path)
    if not data.get('Inserted') or not data.get('Image'):
        raise RuntimeError('Virtual CD is not inserted; refusing to reboot')
    system = ilo.get(ilo.system_path)
    boot = system.get('Boot', {})
    supported = boot_values(boot, 'BootSourceOverrideTarget', 'BootSourceOverrideSupported')
    uefi_devices = boot_values(boot, 'UefiTargetBootSourceOverride', 'UefiTargetBootSourceOverrideSupported')
    diagnostic = (f'BOOT_CAPABILITIES power={system.get("PowerState")} '
                  f'mode={boot.get("BootSourceOverrideMode")} '
                  f'targets={supported!r} uefi_targets={uefi_devices!r}')
    print('[INFO]', diagnostic, flush=True)
    if getattr(ilo, 'log', None):
        with ilo.log.open('a', encoding='utf-8') as record:
            record.write(diagnostic + '\n')
    media_oem = data.get('Oem', {})
    vendor = next((key for key in ('Hpe', 'Hp')
                   if 'BootOnNextServerReset' in media_oem.get(key, {})), None)
    if vendor and (require_virtual_cd or vendor == 'Hpe'):
        # Some iLO 4 firmware accepts the media-specific flag but still boots
        # the hard disk. Stage the system's one-time optical override as well.
        # Neither acknowledgement proves that firmware actually loaded the ISO.
        if 'Cd' not in supported:
            raise RuntimeError('iLO does not advertise a CD boot override; refusing to reboot')
        ilo.request('patch', media_path, {'Oem': {vendor: {'BootOnNextServerReset': True}}})
        staged = ilo.get(media_path)
        if staged.get('Oem', {}).get(vendor, {}).get('BootOnNextServerReset') is not True:
            raise RuntimeError('iLO did not retain virtual-CD boot-on-next-reset; refusing to reboot')
        print(f'[INFO] Virtual CD boot-on-next-reset confirmed (Oem.{vendor})', flush=True)
        request = {'BootSourceOverrideEnabled': 'Once', 'BootSourceOverrideTarget': 'Cd'}
        ilo.request('patch', ilo.system_path, {'Boot': request})
        selected = ilo.get(ilo.system_path)
        if any(selected.get('Boot', {}).get(key) != value for key, value in request.items()):
            raise RuntimeError('iLO did not retain the one-time CD boot override; refusing to reboot')
        if getattr(ilo, 'log', None):
            with ilo.log.open('a', encoding='utf-8') as record:
                record.write('BOOT_DIAGNOSTIC selected=VirtualMedia.BootOnNextServerReset+Cd '
                             f'virtual_media_inserted={staged.get("Inserted")}\n')
        ilo.reset('On' if selected.get('PowerState') == 'Off' else 'ForceRestart')
        print('[OK] One-time CD boot requested through iLO Virtual Media and system override')
        return
    if require_virtual_cd and 'CD.Virtual.2.1' not in uefi_devices:
        raise RuntimeError('iLO does not expose an explicit virtual-CD boot target; refusing ambiguous generic Cd boot')
    if 'UefiTarget' in supported and 'CD.Virtual.2.1' in uefi_devices:
        # Use only a target advertised by this iLO at this moment.  A target
        # from documentation can disappear when virtual USB is also mounted.
        target = 'CD.Virtual.2.1'
        # iLO 4 rejects a single PATCH containing both fields: the UEFI
        # device must already be stored before selecting UefiTarget.
        ilo.request('patch', ilo.system_path,
                    {'Boot': {'UefiTargetBootSourceOverride': target}})
        staged = ilo.get(ilo.system_path).get('Boot', {})
        if staged.get('UefiTargetBootSourceOverride') != target:
            raise RuntimeError('iLO did not retain the virtual-CD UEFI device; refusing to reboot')
        request = {'BootSourceOverrideEnabled': 'Once',
                   'BootSourceOverrideTarget': 'UefiTarget'}
    elif 'Cd' in supported:
        # iLO 4 may advertise the virtual CD only through its generic Cd
        # source, particularly with the Windows answer USB mounted.
        request = {'BootSourceOverrideEnabled': 'Once', 'BootSourceOverrideTarget': 'Cd'}
    else:
        raise RuntimeError('iLO does not advertise a virtual-CD boot override')
    if getattr(ilo, 'log', None):
        with ilo.log.open('a', encoding='utf-8') as record:
            record.write(f'BOOT_DIAGNOSTIC selected={request["BootSourceOverrideTarget"]} '
                         f'uefi_cd_advertised={"CD.Virtual.2.1" in uefi_devices} '
                         f'virtual_media_inserted={data.get("Inserted")}\n')
    ilo.request('patch', ilo.system_path, {'Boot': request})
    state = ilo.get(ilo.system_path)
    if any(state.get('Boot', {}).get(key) != value for key, value in request.items()):
        raise RuntimeError('iLO did not retain the exact virtual-CD boot override; refusing to reboot')
    if request['BootSourceOverrideTarget'] == 'UefiTarget' and state['Boot'].get('UefiTargetBootSourceOverride') != target:
        raise RuntimeError('iLO changed the selected virtual-CD UEFI device; refusing to reboot')
    ilo.reset('On' if state.get('PowerState') == 'Off' else 'ForceRestart')
    print('[OK] One-time virtual CD boot requested:', request['BootSourceOverrideTarget'])


def prefer_hard_disk_on_next_boot(ilo):
    """Prevent mounted installer media from winning the next Windows reboot."""
    system = ilo.get(ilo.system_path)
    boot = system.get('Boot', {})
    if 'Hdd' not in boot_values(boot, 'BootSourceOverrideTarget', 'BootSourceOverrideSupported'):
        raise RuntimeError('iLO does not support a one-time hard-disk boot')
    if (boot.get('BootSourceOverrideEnabled') == 'Once'
            and boot.get('BootSourceOverrideTarget') == 'Hdd'):
        return
    ilo.request('patch', ilo.system_path, {'Boot': {
        'BootSourceOverrideEnabled': 'Once', 'BootSourceOverrideTarget': 'Hdd'}})
    boot = ilo.get(ilo.system_path).get('Boot', {})
    if boot.get('BootSourceOverrideEnabled') != 'Once' or boot.get('BootSourceOverrideTarget') != 'Hdd':
        raise RuntimeError('iLO did not retain the one-time hard-disk boot')
