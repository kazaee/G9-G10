"""G6 workflow isolated from the existing Redfish installation paths."""
import json
import secrets
import threading
import time
import uuid
from pathlib import Path
import yaml
from .storage import inventory, recommend
from .lo100 import Lo100
from .lo100_inventory import validate_target


class Lo100Workflow:
    def lo100_cache_path(self):
        # Controller identity, never an IP-only cache shared between machines.
        key = str(uuid.UUID(self.lo100_profile['system_guid']))
        return Path(__file__).resolve().parent.parent / 'generated/lo100' / key / 'live-inventory.json'

    def accept_lo100_inventory(self, live):
        validate_target(live,self.lo100_profile['system_guid'],self.lo100_profile.get('model'))
        serial=self.discovery['system'].get('SerialNumber')
        if not serial or serial != live['server_serial']:
            raise ValueError('Diagnostic serial number differs from the connected chassis')
        inv=inventory(self.discovery,live)
        plan=recommend(inv,live['uefi'])
        self.live=live
        self.discovery['system']['Boot']={'BootSourceOverrideMode':'UEFI' if live['uefi'] else 'Legacy'}
        self.reviewed=None
        self.state.update(stage='discovered',plan=yaml.safe_dump(plan,sort_keys=False),
            message='دیسک‌های واقعی شناسایی شدند؛ پارتیشن‌بندی را بررسی کنید و شبکه را تنظیم یا فعلاً رد کنید. اطلاعات هنگام نصب دوباره با خود سرور و دیسک‌ها تطبیق داده می‌شود.')

    def read_lo100_hardware(self):
        from .lo100_clp import hardware
        controller=Lo100(self.config['ilo']['address'],self.config['ilo']['username'],self.password)
        report=hardware(controller,self.config['ilo']['username'],self.password)
        self.discovery['system']['SerialNumber']=report['server_serial']
        self.state['lo100_hardware']=report
        return report

    def select_lo100_cd(self, confirmation):
        from .lo100_ipmi import Lo100IPMI
        controller=Lo100(self.config['ilo']['address'],self.config['ilo']['username'],self.password)
        with Lo100IPMI(controller.host,self.config['ilo']['username'],self.password) as client:
            result=client.cd_once(confirmation=confirmation,media_confirmed=True)
        self.state['lo100_boot_selection']=result
        return result

    def discover_lo100(self,body):
        if not self.lo100_profile:raise ValueError('Connect to LO100 first')
        if self.state.get('stage')=='discovering':raise ValueError('Diagnostic capture is already running')
        reboot=body.get('confirm_reboot') is True
        manual=body.get('lo100_diagnostic_media_ready') is True
        host=str(body.get('media_host') or self.config['media']['host']).strip()
        port=int(body.get('media_port') or 443)
        if not 1<=port<=65535:raise ValueError('Invalid media port')
        self.config['media'].update(host=host,port=port)
        def task():
            self.live=self.reviewed=None
            self.prepared_credentials=self.completed_credentials=None
            self.state.pop('plan',None);self.state.pop('lo100_preparation',None)
            self.read_lo100_hardware()
            cache=self.lo100_cache_path()
            if not reboot:
                if cache.is_file():
                    try:
                        self.accept_lo100_inventory(json.loads(cache.read_text(encoding='utf-8')))
                        self.state['message']='اطلاعات تأییدشدهٔ قبلی همین GUID و شماره‌سریال بدون ریبوت بازیابی شد. پارتیشن و شبکه را تنظیم کنید؛ برای شناسایی تازه گزینهٔ ریبوت تشخیصی را انتخاب کنید.'
                        return
                    except (ValueError,KeyError,TypeError):pass
                self.state.update(stage='connected',message='مشخصات شاسی بدون ریبوت خوانده شد؛ LO100 شناسهٔ دیسک‌ها را نداده و شناسایی قبلی معتبری موجود نیست. گزینهٔ ریبوت تشخیصی را علامت بزنید و شناسایی را اجرا کنید؛ رمز سیستم‌عامل قبلی لازم نیست.')
                return
            from .os_catalog import source_for
            from .lo100_diagnostic import build
            pending=getattr(self,'lo100_diagnostic',None)
            if manual:
                if pending and pending.get('boot_requested'):
                    raise ValueError('برای تلاش تازه، گزینهٔ اتصال دستی تشخیصی را بردارید و ISO تشخیصی جدید بسازید؛ فرمان قبلی خودکار تکرار نمی‌شود.')
                if not pending or pending['target']!=self.config['ilo']['address'] or not Path(pending['iso']).is_file():
                    raise ValueError('Prepare diagnostic media for this target first')
            else:
                reusable=(pending and not pending.get('boot_requested') and pending['target']==self.config['ilo']['address'] and pending.get('guid')==self.lo100_profile['system_guid'] and pending.get('serial')==self.discovery['system']['SerialNumber'] and Path(pending['iso']).is_file())
                if not reusable:
                    run=self.run_directory()
                    nonce=secrets.token_hex(16)
                    root=Path(__file__).resolve().parent.parent
                    image=build(source_for('ubuntu24',root/'ISO'),run/'lo100-diagnostic.iso',nonce)
                    pending={'target':self.config['ilo']['address'],'iso':str(image.resolve()),'probe_id':nonce,'run':str(run),'guid':self.lo100_profile['system_guid'],'serial':self.discovery['system']['SerialNumber']}
                    self.lo100_diagnostic=pending
                    self.state['lo100_diagnostic']=dict(pending)
                if not self.mount_lo100_automatic(pending['iso']):return
            from .lo100_clp import Lo100CLP
            capture=Lo100CLP(Lo100(self.config['ilo']['address'],self.config['ilo']['username'],self.password).host,self.config['ilo']['username'],self.password)
            serial=Path(pending['run'])/'lo100-serial.log'
            try:
                capture.login();capture.capture_serial(serial)
                self.state.update(stage='discovering',message='رسانه آماده است؛ فرمان بوت تشخیصی ارسال می‌شود. منتظر گزارش واقعی دیسک‌ها از کنسول سریال هستیم؛ دیسک‌ها پاک نمی‌شوند.')
                self.select_lo100_cd('DISCOVER')
                pending['boot_requested']=True
                # An uncertain response must not trigger a second power command.
                from .lo100 import BootRequestUncertain
                try:Lo100(self.config['ilo']['address'],self.config['ilo']['username'],self.password).boot_install(confirmation='INSTALL',media_confirmed=True)
                except BootRequestUncertain:self.state['message']='پاسخ فرمان بوت نامشخص است؛ فرمان تکرار نشد. دریافت گزارش سریال ادامه دارد.'
            except Exception:
                capture.close();raise
            self.lo100_capture=capture
            threading.Thread(target=self.watch_lo100_diagnostic,args=(pending,serial,capture),daemon=True).start()
        self.launch(task)

    def mount_lo100_automatic(self,image):
        from .lo100_native_media import NativeMedia
        previous=getattr(self,'lo100_native',None)
        if previous:previous.close();self.lo100_native=None
        controller=Lo100(self.config['ilo']['address'],self.config['ilo']['username'],self.password)
        media=NativeMedia(controller,self.config['ilo']['username'],image,self.config['media']['host'],self.config['media']['port'])
        try:
            media.start()
        except (OSError,RuntimeError,ValueError) as exc:
            media.close()
            self.state['lo100_native_media']=dict(media.evidence(),failure=str(exc))
            reason='کنترلر هنوز مجوز رسانه را به جلسهٔ خودکار نداده است. اگر کنسول Java دیگری باز است، آن را به‌صورت عادی ببندید و دوباره امتحان کنید؛ یا از همان کنسول برای اتصال دستی استفاده کنید. ' if media.control_state_seen and not (media.storage_allowed and media.full_control) else ''
            self.state.update(stage='diagnostic-media-required' if image==(getattr(self,'lo100_diagnostic',None) or {}).get('iso') else 'ready',
                message=reason+'اتصال خودکار ISO روی پورت انتخابی تأیید نشد؛ هیچ ریبوتی درخواست نشد. جزئیات در گزارش رسانه است. پورت دیگری را امتحان کنید یا ISO نمایش‌داده‌شده را در کنسول Java متصل و گزینهٔ اتصال دستی را تأیید کنید.')
            return False
        self.lo100_native=media
        self.state['lo100_native_media']=media.evidence()
        return True

    def watch_lo100_diagnostic(self,pending,serial,capture):
        from .live_report import from_serial
        try:
            deadline=time.monotonic()+1800
            while time.monotonic()<deadline:
                if getattr(self,'lo100_diagnostic',None) is not pending:return
                native=getattr(self,'lo100_native',None)
                if native:self.state['lo100_native_media']=native.evidence()
                live=from_serial(serial,pending['probe_id'])
                if live:
                    self.accept_lo100_inventory(live)
                    cache=self.lo100_cache_path();cache.parent.mkdir(parents=True,exist_ok=True)
                    cache.write_text(json.dumps(live,indent=2),encoding='utf-8')
                    self.state.pop('lo100_diagnostic',None)
                    self.lo100_diagnostic=None
                    if native:native.close();self.lo100_native=None
                    return
                time.sleep(2)
            self.state.update(stage='error',message='گزارش تشخیصی معتبر دریافت نشد؛ شناسایی موفق اعلام نشد. بوت CD و کنسول سریال را بررسی کنید. دکمهٔ شناسایی باعث ریبوت مجدد خودکار نمی‌شود.')
        except Exception as exc:self.state.update(stage='error',message=str(exc))
        finally:capture.close()
