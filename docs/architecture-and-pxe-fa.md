# معماری جدید و راهنمای بوت شبکه (PXE / iPXE)

## ۱. معماری جدید در یک نگاه

```
            ┌──────────────── جامپ‌سرور (پنل / CLI) ────────────────┐
            │  app.py / provision.py / fleet.py                      │
            │        │                                               │
            │   src/drivers  ← یک رابط برای همهٔ نسل‌ها              │
            │     ├─ redfish.py  → iLO 4 (G8, G9) و iLO 5 (G10)       │
            │     └─ ribcl.py    → iLO 2 (G6) و iLO 3 (G7)            │
            └───────────┬───────────────────────────┬────────────────┘
                        │ فرمان (HTTPS 443)          │ کپی فایل‌ها
                        ▼                           ▼
                    iLO سرورها  ───── HTTP ────▶  سرور رسانه (nginx)
                                 ISO می‌کشد      + در حالت PXE: DHCP/TFTP
```

ایدهٔ اصلی: **جامپ فقط فرمان می‌دهد؛ فایل را یک سرور جدا که iLOها به آن دسترسی دارند تحویل می‌دهد.** این همان چیزی است که مشکل پورت ۸۰۸۰ را برای همیشه حل می‌کند.

## ۲. تغییراتی که در کد انجام شد

| چه چیزی | فایل | کاربرد |
|---|---|---|
| لایهٔ درایور | `src/drivers/` | یک رابط مشترک (`insert_cd`، `boot_cd_once`، `boot_pxe_once`، `power`) برای همهٔ نسل‌ها |
| تشخیص خودکار نسل | `src/drivers/detect.py` | بدون رمز، با `/xmldata?item=all` نسل iLO و مدل سرور را می‌خواند |
| درایور G6/G7 | `src/drivers/ribcl.py` | RIBCL روی TLS پورت 443 (iLO 2 و 3 فاقد Redfish هستند) |
| درایور G8–G10 | `src/drivers/redfish.py` | روی همان کد تست‌شدهٔ قبلی سوار است؛ چیزی از منطق قبلی حذف نشد |
| سرور رسانهٔ جدا | `src/virtual_media.py` + `config/media-server.example.yaml` | اگر `config/media-server.yaml` وجود داشته باشد، ISO در پوشهٔ nginx گذاشته می‌شود و iLO از آنجا می‌خواند. اگر نباشد همه چیز مثل قبل است |
| لیست سرورها | `config/servers.example.yaml` + `fleet.py` | بررسی فقط-خواندنی همهٔ سرورها: نسل، مدل، درایور |
| تست | `tests/test_drivers.py` | ۵ تست جدید؛ کل مجموعه ۲۱۹ تست موفق |

نسخهٔ پشتیبان کامل قبل از تغییر: `/home/balloon/G10-G9/G9-G10.backup-*`

**هنوز روی سخت‌افزار تست نشده:** درایور RIBCL (G6/G7) و حالت سرور رسانهٔ جدا. پنل (`app.py`) هنوز مستقیم از کد Redfish قدیمی استفاده می‌کند؛ مهاجرت پنل به `src/drivers` قدم بعدی است.

نکته: در حالت سرور رسانهٔ جدا، شمارنده‌های «ISO reads / MiB served» پنل صفر می‌مانند چون فایل را nginx می‌دهد. پیشرفت را در `access.log` خود nginx ببینید.

### دستورهای جدید

```bash
cp config/servers.example.yaml config/servers.yaml      # IP iLOها را بنویسید
python fleet.py                                          # تشخیص نسل همه، بدون رمز
python fleet.py --login                                  # + ورود و وضعیت برق
```

---

## ۳. PXE و iPXE به زبان ساده

### Virtual Media چه می‌کند (روش فعلی)
iLO یک «CD مجازی» به سرور وصل می‌کند و ISO را **تکه‌تکه از طریق خود iLO** می‌کشد. مشکلات: iLO باید به سرور رسانه دسترسی داشته باشد، سرعت پایین است (مخصوصاً iLO 2 و 3)، و هر نسل رفتار متفاوتی دارد.

### PXE چه می‌کند
کارت شبکهٔ خود سرور (نه iLO) هنگام بوت از شبکه می‌پرسد «من چه چیزی بوت کنم؟». یک سرور DHCP جواب می‌دهد و فایل بوت را از TFTP/HTTP می‌دهد. iLO فقط یک کار می‌کند: **«بار بعد از شبکه بوت کن» و ریبوت.** همهٔ نسل‌ها از G6 تا G10 این را پشتیبانی می‌کنند.

### iPXE چیست
PXE معمولی فقط TFTP بلد است که کند است. iPXE یک بوت‌لودر کوچک است که اول با PXE بارگذاری می‌شود و بعد **فایل‌های بزرگ (kernel، initrd، ISO) را با HTTP سریع** می‌گیرد و می‌تواند اسکریپت داشته باشد؛ مثلاً بر اساس MAC هر سرور نصب مخصوص خودش را انتخاب کند.

### روند یک نصب با PXE
1. پنل برای سرور یک فایل تنظیمات (autoinstall/kickstart) می‌سازد و با نام MAC آن در سرور رسانه می‌گذارد.
2. پنل از طریق iLO: «بوت یک‌باره از شبکه» + ریبوت (`boot_pxe_once`).
3. کارت شبکهٔ سرور DHCP می‌گیرد → iPXE را دانلود می‌کند.
4. iPXE اسکریپت `boot.ipxe?mac=...` را از nginx می‌گیرد → kernel و initrd نصب‌کننده را بوت می‌کند.
5. نصب‌کننده تنظیمات مخصوص همان سرور را از HTTP می‌خواند و نصب خودکار انجام می‌شود.
6. پس از نصب، گزارش «تمام شد» به پنل ارسال می‌شود و سرور از دیسک بوت می‌شود.

### PXE در برابر Virtual Media
| | Virtual Media | PXE/iPXE |
|---|---|---|
| وابستگی به نسل iLO | زیاد | کم (فقط یک فرمان بوت) |
| سرعت | کند | سریع (HTTP مستقیم از کارت شبکه) |
| نیاز شبکه | iLO ← سرور رسانه | کارت شبکهٔ سرور ← سرور PXE (هم‌VLAN یا DHCP relay) |
| مناسب برای | سرور تکی، شبکهٔ بدون DHCP | تعداد زیاد، نصب روزانه |

پیشنهاد: هر دو را نگه دارید. PXE روش اصلی، Virtual Media برای جاهایی که PXE ممکن نیست.

---

## ۴. کارهایی که شما باید انجام دهید (قدم به قدم)

### مرحلهٔ الف: سرور رسانه (برای Virtual Media هم لازم است؛ اول این را انجام دهید)
1. یک VM لینوکسی کوچک (Ubuntu 24.04، ۲ هسته، ۴ گیگ RAM، ۲۰۰ گیگ دیسک) بسازید.
2. آن را به **شبکهٔ مدیریت iLOها** و **شبکهٔ پرایوت سرورها** وصل کنید (دو کارت شبکه یا مسیریابی).
3. nginx نصب کنید:
   ```bash
   sudo apt install -y nginx
   ```
4. پوشه بسازید و nginx را تنظیم کنید (`/etc/nginx/sites-available/default`):
   ```
   location /provision/ { alias /srv/provision/; autoindex off; }
   ```
   سپس:
   ```bash
   sudo mkdir -p /srv/provision && sudo systemctl reload nginx
   ```
5. از تیم شبکه بخواهید: **TCP 80 از رنج IP همهٔ iLOها به این VM مجاز شود.**
6. تست از خود iLO: یک ISO کوچک را دستی در iLO با URL `http://IP-VM/provision/test.iso` mount کنید. اگر Inserted شد، مسیر درست است.
7. پوشهٔ `/srv/provision` را برای جامپ قابل نوشتن کنید (SMB/NFS share یا اجرای پنل روی خود همین VM). سپس در پروژه:
   ```bash
   cp config/media-server.example.yaml config/media-server.yaml
   ```
   و `base_url` و `publish_dir` را پر کنید. از این لحظه، روش دستی وصل‌کردن ISO لازم نیست.

> ساده‌ترین حالت: پنل را روی همین VM لینوکسی اجرا کنید. `publish_dir` یک پوشهٔ محلی می‌شود و فایروال ویندوز دیگر وجود ندارد.

### مرحلهٔ ب: آماده‌سازی PXE
1. **تصمیم دربارهٔ DHCP** (مهم‌ترین قدم، با تیم شبکه):
   - اگر شبکهٔ پرایوت نصب DHCP ندارد → dnsmasq روی همین VM نقش DHCP کامل را می‌گیرد.
   - اگر DHCP دارد → از **حالت proxy-DHCP** در dnsmasq استفاده کنید. فقط اطلاعات بوت را اضافه می‌کند و با DHCP موجود تداخل ندارد.
   - اگر سرورها در VLAN دیگری هستند → روی سوییچ/روتر، `ip helper-address` (DHCP relay) به IP این VM.
2. نصب ابزارها:
   ```bash
   sudo apt install -y dnsmasq ipxe
   ```
3. فایل‌های iPXE را در TFTP بگذارید:
   ```bash
   sudo mkdir -p /srv/tftp && sudo cp /usr/lib/ipxe/{undionly.kpxe,ipxe.efi} /srv/tftp/
   ```
4. تنظیم dnsmasq (`/etc/dnsmasq.d/pxe.conf`)، نمونه برای proxy-DHCP:
   ```
   port=0
   interface=ens19
   dhcp-range=10.20.0.0,proxy
   enable-tftp
   tftp-root=/srv/tftp
   dhcp-match=set:efi,option:client-arch,7
   dhcp-match=set:efi,option:client-arch,9
   dhcp-userclass=set:ipxe,iPXE
   pxe-service=tag:!ipxe,tag:!efi,x86PC,"BIOS",undionly.kpxe
   pxe-service=tag:!ipxe,tag:efi,x86-64_EFI,"UEFI",ipxe.efi
   dhcp-boot=tag:ipxe,http://10.20.0.5/provision/boot.ipxe
   ```
   G6/G7 و بعضی G8ها Legacy BIOS هستند (undionly.kpxe)؛ G9/G10 معمولاً UEFI (ipxe.efi). این فایل هر دو را پوشش می‌دهد.
5. اسکریپت iPXE نمونه (`/srv/provision/boot.ipxe`):
   ```
   #!ipxe
   dhcp
   chain http://10.20.0.5/provision/hosts/${net0/mac:hexhyp}.ipxe || shell
   ```
   و برای هر سرور، پنل فایلی مثل `hosts/aa-bb-cc-dd-ee-ff.ipxe` می‌سازد (نمونهٔ Ubuntu):
   ```
   #!ipxe
   kernel http://10.20.0.5/provision/ubuntu/vmlinuz ip=dhcp url=http://10.20.0.5/provision/ubuntu.iso autoinstall ds=nocloud-net;s=http://10.20.0.5/provision/seed/aa-bb-cc-dd-ee-ff/
   initrd http://10.20.0.5/provision/ubuntu/initrd
   boot
   ```
6. تست دستی روی یک سرور: از iLO کنسول را باز کنید، F12 (Network Boot) بزنید و ببینید منوی iPXE بالا می‌آید.
7. در BIOS سرورها: کارت شبکهٔ نصب باید **PXE Enabled** باشد (در G9/G10: System Configuration → BIOS → Network Options؛ در G6/G7: RBSU با F9).

### مرحلهٔ ج: کاری که در کد انجام می‌شود (قدم بعدی من)
- یک ماژول `src/pxe.py` که برای هر سرور فایل `hosts/<mac>.ipxe` و seed مخصوص را در `publish_dir` می‌سازد.
- در پنل، گزینهٔ «روش بوت: Virtual Media / PXE». در حالت PXE به‌جای `insert_cd` + `boot_cd_once`، فقط `boot_pxe_once` صدا زده می‌شود.
- پس از نصب، فایل `hosts/<mac>.ipxe` حذف می‌شود تا سرور دوباره نصب نشود (بدون فایل، iPXE به دیسک برمی‌گردد).
- تأیید نصب (سریال/SSH/گزارش) بدون تغییر از کد فعلی استفاده می‌شود.

## ۵. ترتیب پیشنهادی
1. مرحلهٔ الف (سرور رسانه + باز کردن پورت 80 برای iLOها) ← مشکل فعلی را حل می‌کند.
2. `fleet.py` روی همهٔ سرورها برای دانستن نسل و firmware هرکدام.
3. تست Virtual Media با سرور رسانهٔ جدا روی G9 و G10 که قبلاً موفق بودند.
4. تست درایور RIBCL روی یک G7.
5. مرحلهٔ ب (PXE) روی یک سرور، سپس مرحلهٔ ج در کد.
