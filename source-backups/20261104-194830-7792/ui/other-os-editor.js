(() => {
  const select = document.getElementById('os');
  const root = document.getElementById('other-os-editor');
  const linux = document.getElementById('partition-editor');
  const advanced = document.getElementById('advanced-plan');
  const prepare = document.getElementById('prepare');
  const install = document.getElementById('install');
  let state = null;
  const choices = {};
  let windowsIndex = 2;
  let windowsDataDisk = '';
  let serverSerial = null;
  const selected = () => (select.value === 'windows2022' || select.value.startsWith('esxi'));
  function sync(next) {
    state = next;
    const serial = state.server?.SerialNumber || null;
    if (serial !== serverSerial) {
      delete choices.windows2022;
      windowsDataDisk = '';
      delete choices.esxi803; delete choices.esxi801; delete choices.esxi800; delete choices.esxi703;
      serverSerial = serial;
    }
    const special = selected();
    document.getElementById('debian-storage-guide').hidden = !select.value.startsWith('debian');
    document.getElementById('admin-heading').textContent = select.value === 'windows2022'
      ? 'حساب مدیر Windows (Administrator)' : select.value.startsWith('esxi')
      ? 'حساب مدیر ESXi (root)' : (select.value.startsWith('alma') || select.value.startsWith('debian'))
      ? 'حساب مدیر Linux (root)' : 'حساب مدیر Ubuntu';
    document.getElementById('admin-note').hidden = special;
    document.getElementById('os-user').parentElement.hidden = special;
    document.getElementById('os-user').value = 'root';
    document.getElementById('ssh-port-label').hidden = special;
    document.getElementById('rdp-port-label').hidden = select.value !== 'windows2022';
    document.getElementById('offline-label').hidden = special || (select.value.startsWith('alma') || select.value.startsWith('debian'));
    root.hidden = !special;
    linux.hidden = special;
    advanced.hidden = special;
    if (!special) {
      prepare.textContent = state.nics ? 'بررسی طرح و ساخت ISO نصب' : 'شناسایی سرور و پیشنهاد پارتیشن';
      return;
    }
    const os = select.value;
    document.getElementById('prepare').textContent = os === 'windows2022'
      ? 'بررسی دیسک و ساخت USB پاسخ نصب ویندوز' : 'بررسی دیسک و ساخت ISO نصب ESXi';
    prepare.disabled = !choices[os] || !state.detected_media?.[os] || state.busy;
    install.disabled = !state.review || state.review.os !== os || state.busy || dirty;
    const disks = state.storage?.installable || [];
    const media = state.detected_media?.[os];
    document.getElementById('os-media-status').textContent = media
      ? 'رسانهٔ شناسایی‌شده: ' + media : 'ISO این سیستم‌عامل پیدا نشد.';
    root.replaceChildren();
    const heading = document.createElement('h3');
    heading.textContent = os === 'windows2022' ? 'دیسک نصب درایو C ویندوز' : 'دیسک بوت و نصب ESXi';
    root.append(heading);
    const note = document.createElement('p');
    note.className = 'notice';
    note.textContent = !media ? 'ISO این سیستم‌عامل در پوشهٔ ISO یافت نشد.'
      : os.startsWith('esxi') ? 'ISO شناسایی شد: ' + media + '. یک دیسک و کارت شبکهٔ مدیریت را انتخاب کنید؛ سپس ISO نصب خودکار را آماده کنید.'
      : 'ISO شناسایی شد: ' + media + '. نسخهٔ Windows، یک دیسک و کارت شبکه را انتخاب کنید؛ سپس رسانهٔ نصب خودکار را آماده کنید.';
    root.append(note);
    if (os === 'windows2022') {
      const edition = document.createElement('label');
      edition.textContent = 'ویرایش Windows Server 2022';
      const picker = document.createElement('select');
      [[1,'Standard Evaluation · Core'],[2,'Standard Evaluation · Desktop Experience'],
       [3,'Datacenter Evaluation · Core'],[4,'Datacenter Evaluation · Desktop Experience']]
        .forEach(([value,title]) => picker.add(new Option(title,String(value))));
      picker.value = String(windowsIndex);
      picker.onchange = () => { windowsIndex = Number(picker.value); select.dispatchEvent(new Event('input')); };
      edition.append(picker); root.append(edition);
    }
    if (!disks.length) {
      const p = document.createElement('p');
      p.textContent = 'برای نمایش دیسک‌ها ابتدا شناسایی زندهٔ سرور را اجرا کنید.';
      root.append(p);
      return;
    }
    const grid = document.createElement('div');
    grid.className = 'disk-grid';
    disks.forEach(d => {
      const label = document.createElement('label');
      label.className = 'disk-card' + (choices[os] === d.path ? ' selected' : '');
      const input = document.createElement('input');
      input.type = 'radio'; input.name = 'single-target-disk';
      input.value = d.path; input.checked = choices[os] === d.path;
      input.onchange = () => {
        choices[os] = d.path;
        if (os === 'windows2022' && windowsDataDisk === d.path) windowsDataDisk = '';
        select.dispatchEvent(new Event('input')); sync(state);
      };
      const title = document.createElement('strong');
      title.textContent = `${d.media || 'Disk'} · ${(d.size / 1024 ** 3).toFixed(1)} GiB`;
      const path = document.createElement('code'); path.textContent = d.path;
      label.append(input, title, path); grid.append(label);
    });
    root.append(grid);
    if (os === 'windows2022') {
      const data = document.createElement('label');
      data.textContent = 'دیسک دادهٔ ویندوز (اختیاری؛ در صورت انتخاب کاملاً پاک می‌شود و با اولین حرف آزاد از E به بعد ساخته می‌شود)';
      const picker = document.createElement('select');
      picker.add(new Option('هیچ دیسک داده‌ای پاک نشود', ''));
      disks.filter(d => d.path !== choices.windows2022).forEach(d =>
        picker.add(new Option(`${d.media || 'Disk'} · ${(d.size / 1024 ** 3).toFixed(1)} GiB · ${d.path}`, d.path)));
      if (![...picker.options].some(option => option.value === windowsDataDisk)) windowsDataDisk = '';
      picker.value = windowsDataDisk;
      picker.onchange = () => {
        windowsDataDisk = picker.value;
        select.dispatchEvent(new Event('input')); sync(state);
      };
      data.append(picker); root.append(data);
    }
    const detail = document.createElement('p');
    detail.className = 'muted';
    detail.textContent = os === 'windows2022'
      ? 'دیسک C پاک می‌شود؛ اگر دیسک داده را هم انتخاب کنید، پس از اولین بوت آن دیسک نیز پاک و به NTFS با اولین حرف آزاد از E به بعد تبدیل می‌شود. فهرست دیسک‌های قابل پاک‌شدن را در بازبینی کنترل کنید.'
      : 'در نصب نهایی، فقط دیسک انتخابی پاک می‌شود؛ ESXi ساختار بوت و datastore را روی همان دیسک می‌سازد. کارت و آدرس شبکهٔ مدیریت باید تأیید شوند.';
    root.append(detail);
    const credential = document.createElement('p');
    credential.className = 'muted';
    credential.textContent = os === 'windows2022'
      ? 'رمز Administrator را در کادر بالا وارد کنید یا برای تولید رمز تصادفی خالی بگذارید. فایل پاسخ روی USB مجازی و روی دیسک این رایانه نگه داشته می‌شود.'
      : 'رمز root را در کادر بالا وارد کنید یا برای تولید رمز تصادفی خالی بگذارید. برای ESXi از حداقل ۱۲ نویسهٔ ASCII شامل حروف، اعداد و _!@#%+.- استفاده کنید.';
    root.append(credential);
  }
  select.addEventListener('change', () => { if (state) sync(state); });
  window.otherOsEditor = {sync, selected, targetDisk: () => choices[select.value] || '',
    dataDisk: () => select.value === 'windows2022' ? windowsDataDisk : '', windowsIndex: () => windowsIndex};
})();
