# بررسی بوت نشدن ISO روی iLO

کد کامل پروژه و اصلاحات iLO 4/5 در این مخزن قرار دارند. ISO، رمزها، config/test.yaml، generated و logs در گیت قرار نمی‌گیرند.

## به‌روزرسانی پروژهٔ موجود روی جامپ‌سرور ویندوز

ابتدا پنل و فرایند شناسایی قبلی را ببندید. از داخل ریشهٔ پروژهٔ موجود:

```powershell
git clone https://git.isvip.ir/ah.khazaee/newiloinstaller.git installer-update
powershell -NoProfile -ExecutionPolicy Bypass -File .\installer-update\Update-Provisioner.ps1 -TargetPath .
```

گیت هنگام نیاز، نام کاربری و توکن را می‌پرسد؛ توکن را در URL ننویسید. برای دفعات بعد:

```powershell
git -C installer-update pull --ff-only
powershell -NoProfile -ExecutionPolicy Bypass -File .\installer-update\Update-Provisioner.ps1 -TargetPath .
```

اسکریپت قبل از جایگزینی از فایل‌های موجود در source-backups نسخهٔ پشتیبان می‌گیرد. ISOها، تنظیمات محلی، لاگ‌ها و داده‌های تولیدشده را کپی یا حذف نمی‌کند. از همین پروژهٔ فعلی برنامه را مثل قبل اجرا کنید.

## نتیجهٔ لاگ فعلی

در لاگ تازه، BIOS رسانهٔ واقعی را با StructuredBootString برابر CD.Virtual.3.1 اعلام کرده و UEFIDevicePath آن در AllowableValues مقصد UEFI هم وجود دارد. اکنون برنامه پورت ثابت 2 را فرض نمی‌کند: BootSources را از لینک اعلام‌شدهٔ BIOS می‌خواند، ورودی CD.Virtual را به مسیر UEFI متناظر وصل می‌کند و فقط اگر آن مسیر توسط خود کنترلر مجاز باشد، همان را برای UefiTarget انتخاب می‌کند. بعد از هر PATCH مقدار ذخیره‌شده دوباره خوانده می‌شود. گزینهٔ عمومی USB یا SD به صورت حدسی انتخاب نمی‌شود. اگر هویت دقیق قابل حل نباشد، مسیر قبلی Cd با کنترل‌های موجود حفظ می‌شود.

در همین اجرا، آدرس محلی جامپ‌سرور برای مسیر iLO برابر 172.20.224.253 است ولی URL رسانه هنوز 185.255.88.7 را استفاده می‌کند. برای آزمایش مسیر مستقیم، در پنل آدرس میزبان ISO را 172.20.224.253 بگذارید و دسترسی TCP/8080 از شبکهٔ iLO به آن را فراهم کنید. لازم نیست IP سیستم‌عامل یا gateway سرور را تغییر بدهید. قابل‌دسترسی‌بودن این IP از خود iLO هنوز با خوانده‌شدن واقعی ISO باید تأیید شود.

بعد از به‌روزرسانی انتظار می‌رود پیام Resolved virtual-CD UEFI target مسیر دقیق USB رسانهٔ مجازی را نشان بدهد و پیام نهایی انتخاب بوت UefiTarget باشد. موفقیت تنظیم به‌تنهایی اثبات بوت نیست؛ ISO reads و MiB served هم باید افزایش پیدا کنند. چهار تست جدید این مسیر موفق شدند؛ مجموع ۱۲۵ تست شامل ۱۲۳ موفق و دو skip است. روی سخت‌افزار واقعی هنوز آزمایش نشده است.

تنظیم BootOnNextServerReset و override یک‌بارهٔ Cd تأیید شده، ولی ISO reads و MiB served صفر مانده‌اند. بنابراین سرویس برنامه داده‌ای از ISO ارسال نکرده است. این شواهد به‌تنهایی نمی‌گویند علت شبکه است یا firmware رسانه را انتخاب نکرده؛ تعویض ISO یا انتخاب تصادفی یک USB device path تشخیص مستند نیست.

در شناسایی بعدی این موارد خودکار جمع می‌شوند و در media-diagnostics.json کنار لاگ‌های همان اجرا قرار می‌گیرند:

- MEDIA_NETWORK: آدرس اعلام‌شده برای ISO و IP محلی انتخاب‌شده توسط مسیر سیستم‌عامل جامپ‌سرور برای دسترسی به iLO.
- MEDIA_STATE: وضعیت اتصال رسانه و تنظیم boot-on-next-reset، بدون URL دارای توکن.
- BOOT_BIOS: تنظیمات مرتبط USB/بوت، اگر در لینک BIOS اعلام‌شده توسط کنترلر قابل خواندن باشند.
- BOOT_SOURCES: نام و UEFI device pathهای واقعی، اگر کنترلر لینک این منبع را اعلام کند.

آدرس محلی مسیر شبکه فقط یک پیشنهاد است. ممکن است reverse route، NAT، فایروال، VLAN یا forwarding باعث شود از سمت iLO قابل دسترسی نباشد. برنامه آن را به صورت حدسی جایگزین نمی‌کند. در پنل، میزبان ISO را IP جامپ‌سروری قرار دهید که خود iLO بتواند به TCP/8080 آن وصل شود. موفقیت مرورگر یا اتصال HTTPS از جامپ‌سرور به iLO، مسیر معکوس را اثبات نمی‌کند.

یک بررسی بدون ریبوت هم وجود دارد. از ریشهٔ پروژه، فایل config همان اجرای ناموفق را به آن بدهید؛ این فایل آدرس و تنظیمات واردشده در پنل را دارد و رمز iLO در آن ذخیره نمی‌شود:

```powershell
python provision.py diagnose-media --config "logs\ui-REPLACE-WITH-RUN\config.yaml"
```

نام پوشهٔ واقعی اجرا را جایگزین کنید. رمز در prompt گرفته می‌شود. این دستور روی سرور فقط GET می‌فرستد؛ رسانه را متصل/جدا نمی‌کند و برق یا دیسک‌ها را تغییر نمی‌دهد. رسیدن به نتیجهٔ این دستور اثبات خوانده‌شدن ISO نیست. فایل media-diagnostics.json و installer.log همان اجرا برای بررسی بعدی مفیدند.

## رفتار بازیابی POST

فقط شناسایی با ریبوت تأییدشده اجازه دارد برای MessageId دقیق UnableToModifyDuringSystemPOST یک بار ForceOff بفرستد، Off را تأیید کند، بوت رسانه را تنظیم و سپس On درخواست کند. سایر خطاها و عملیات اجازهٔ خاموش‌کردن خودکار ندارند. اگر تنظیمات بعد از خاموشی شکست بخورد، سرور خاموش می‌ماند. گزارش‌های PXE قدیمی قبل از بوت جدید در تشخیص شکست استفاده نمی‌شوند.

## مستندات HPE

- [مهاجرت مدل بوت iLO 4 به iLO 5](https://servermanagementportal.ext.hpe.com/docs/redfishservices/ilos/ilo5/ilo5_adaptation)
- [منابع BootSources و BIOS](https://servermanagementportal.ext.hpe.com/docs/redfishservices/ilos/ilo5/ilo5_303/ilo5_hpe_resourcedefns303)
- [نیازمندی‌های رسانهٔ مجازی](https://support.hpe.com/hpesc/public/docDisplay?docId=a00105236en_us&docLocale=en_US&page=GUID-D7147C7F-2016-0901-06D0-0000000020DB.html)
