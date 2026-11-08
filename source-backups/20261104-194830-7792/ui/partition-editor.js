(() => {
  const root = document.getElementById('partition-editor');
  const planField = document.getElementById('plan');
  let serverPlan = '', state = null, plan = null, nextName = 1;
  const GiB = 1024 ** 3;
  const make = (tag, text = '', className = '') => {
    const node = document.createElement(tag);
    node.textContent = text;
    if (className) node.className = className;
    return node;
  };
  const parseSize = text => /^(?:|-1|باقی‌مانده|remaining)$/i.test(text.trim()) ? -1 : text.trim().toUpperCase();
  const sizeGiB = size => {
    if (size === -1) return -1;
    const match = String(size).match(/^([1-9][0-9]*)([KMGT])$/i);
    return match ? Number(match[1]) * ({K: 1 / 1024 ** 2, M: 1 / 1024, G: 1, T: 1024})[match[2].toUpperCase()] : 0;
  };
  const mountOptions = [['/','/ (ریشه)'],['/boot','/boot'],['/boot/efi','/boot/efi'],
    ['/home','/home'],['/var','/var'],['/tmp','/tmp'],['/backup','/backup']];
  const validName = name => /^[A-Za-z][A-Za-z0-9_-]*$/.test(name) && !['efi', 'bios'].includes(name);
  const validMount = path => /^\/(?:[A-Za-z0-9_.-]+\/?)*$/.test(path) &&
    (path === '/' || !path.endsWith('/')) && !path.includes('..');
  const disks = () => state?.storage?.installable || [];
  const chosen = () => disks().find(d => d.path === plan?.boot_disk);
  const diskPlan = path => plan.disks.find(d => d.path === path);
  function diskFreeGiB(path) {
    const disk = disks().find(d => d.path === path);
    const parts = diskPlan(path)?.partitions || [];
    if (!disk) return 0;
    const fixed = parts.filter(p => p.size !== -1).reduce((sum, p) => sum + sizeGiB(p.size), 0);
    return Math.max(0, disk.size / GiB - 8 / 1024 - fixed);
  }
  function lvmFreeGiB() {
    let capacity = 0;
    if (plan.software_raid) {
      const members = plan.software_raid.members.map(path => disks().find(d => d.path === path)).filter(Boolean);
      const sizes = members.map(d => d.size / GiB - 1);
      capacity = !sizes.length ? 0 : plan.software_raid.level === 1 ? Math.min(...sizes) : sizes.reduce((a, b) => a + b, 0);
      capacity -= 2;
    } else {
      for (const disk of plan.disks) for (const part of disk.partitions)
        if (part.vg === 'vg0') capacity += (part.size === -1 ? diskFreeGiB(disk.path) : sizeGiB(part.size)) - 8 / 1024;
    }
    return Math.max(0, capacity - plan.lvm.filter(v => v.size !== -1).reduce((sum, v) => sum + sizeGiB(v.size), 0));
  }
  const freeSizeText = (kind, path) => `${Math.floor(kind === 'lvm' ? lvmFreeGiB() : diskFreeGiB(path))}G`;
  const notify = text => { const box = document.getElementById('editor-message'); if (box) box.textContent = text; };
  function ensureFirmware() {
    if (plan.software_raid) return;
    for (const disk of plan.disks) {
      disk.partitions = disk.partitions.filter(p => !['efi','bios'].includes(p.name) || disk.path === plan.boot_disk);
    }
    const boot = diskPlan(plan.boot_disk);
    if (!boot) return;
    if (plan.boot_mode === 'uefi' && !boot.partitions.some(p => p.mount === '/boot/efi'))
      boot.partitions.unshift({name: 'efi', size: '1G', filesystem: 'fat32', mount: '/boot/efi', flag: 'boot'});
    if (plan.boot_mode === 'bios' && !boot.partitions.some(p => p.flag === 'bios_grub'))
      boot.partitions.unshift({name: 'bios', size: '1M', flag: 'bios_grub'});
  }
  function commit() {
    ensureFirmware();
    plan.untouched = disks().filter(d => !diskPlan(d.path)).map(d => d.path);
    planField.value = JSON.stringify(plan, null, 2);
    planField.dispatchEvent(new Event('input', {bubbles: true}));
    render();
  }
  function singleDisk(disk) {
    const mode = state?.plan_data?.boot_mode || plan?.boot_mode || 'uefi';
    const efi = mode === 'uefi'
      ? [{name: 'efi', size: '1G', filesystem: 'fat32', mount: '/boot/efi', flag: 'boot'}]
      : [{name: 'bios', size: '1M', flag: 'bios_grub'}];
    const swap = Math.floor(disk.size / GiB) >= 1024 ? '16G' : '8G';
    plan = {version: 1, boot_mode: mode, boot_disk: disk.path,
      reason: 'Selected in the visual storage editor',
      disks: [{path: disk.path, ptable: 'gpt', partitions: [...efi,
        {name: 'boot', size: '2G', filesystem: 'ext4', mount: '/boot'},
        {name: 'swap', size: swap, filesystem: 'swap'},
        {name: 'root', size: -1, filesystem: 'ext4', mount: '/'}]}],
      lvm: [],
      untouched: disks().filter(d => d.path !== disk.path).map(d => d.path)};
    commit();
  }
  function toggleDisk(disk) {
    if (plan.software_raid) return notify('برای تغییر اعضای RAID، ابتدا به طرح تک‌دیسک برگردید.');
    const existing = diskPlan(disk.path);
    if (existing) {
      if (plan.disks.length === 1) return notify('دست‌کم یک دیسک باید برای نصب انتخاب شود.');
      if (disk.path === plan.boot_disk) return notify('ابتدا دیسک بوت دیگری انتخاب کنید.');
      if (existing.partitions.length) return notify('پارتیشن‌های این دیسک را پیش از کنارگذاشتن آن حذف کنید.');
      plan.disks = plan.disks.filter(d => d.path !== disk.path);
    } else {
      plan.disks.push({path: disk.path, ptable: 'gpt', partitions: []});
    }
    commit();
  }
  function changeBoot(path) {
    plan.boot_disk = path;
    commit();
  }
  function splitLayout(ssd, dataDisk) {
    const mode = plan.boot_mode;
    const firmware = mode === 'uefi'
      ? {name: 'efi', size: '1G', filesystem: 'fat32', mount: '/boot/efi', flag: 'boot'}
      : {name: 'bios', size: '1M', flag: 'bios_grub'};
    plan = {version: 1, boot_mode: mode, boot_disk: ssd.path,
      reason: 'Boot, swap and backup on SSD; root on separate data disk',
      disks: [
        {path: ssd.path, ptable: 'gpt', partitions: [
          firmware,
          {name: 'boot', size: '2G', filesystem: 'ext4', mount: '/boot'},
          {name: 'swap', size: '16G', filesystem: 'swap'},
          {name: 'backup', size: -1, filesystem: 'ext4', mount: '/backup'}]},
        {path: dataDisk.path, ptable: 'gpt', partitions: [
          {name: 'root', size: -1, filesystem: 'ext4', mount: '/'}]}],
      lvm: [], untouched: []};
    commit();
  }
  function raidOne(paths, level) {
    if (paths.length !== 2) return notify('برای RAID دقیقاً دو NVMe انتخاب کنید.');
    const members = paths.map(path => disks().find(d => d.path === path));
    if (members.some(d => !d || !(String(d.bus).toLowerCase() === 'nvme' || d.path.includes('/nvme-'))))
      return notify('RAID نرم‌افزاری در این نسخه فقط برای دو دیسک NVMe شناسایی‌شده فعال است.');
    const capacity = Math.min(...members.map(d => d.size));
    if (capacity < 32 * GiB) return notify('هر NVMe باید دست‌کم ۳۲ GiB باشد.');
    const swap = Math.floor(capacity / GiB) >= 1024 ? '16G' : '8G';
    plan = {version: 1, boot_mode: 'uefi', boot_disk: paths[0],
      reason: 'Two NVMe devices, Linux software RAID' + level,
      software_raid: {level, members: paths},
      disks: members.map((d, i) => ({path: d.path, wwn: d.wwn || '', ptable: 'gpt',
        partitions: i === 0 ? [{name: 'efi', size: '1G', filesystem: 'fat32', mount: '/boot/efi', flag: 'boot'}] : []})),
      lvm: [{name: 'swap', vg: 'vg0', size: swap, filesystem: 'swap'},
            {name: 'root', vg: 'vg0', size: -1, filesystem: 'ext4', mount: '/'}],
      untouched: disks().filter(d => !paths.includes(d.path)).map(d => d.path)};
    commit();
  }
  function input(label, value, onChange, disabled = false) {
    const box = make('label', label);
    const field = document.createElement('input');
    field.value = value ?? '';
    field.disabled = disabled;
    field.addEventListener('change', () => onChange(field.value));
    box.append(field);
    return box;
  }
  function select(label, choices, value, onChange, disabled = false) {
    const box = make('label', label);
    const field = document.createElement('select');
    for (const [val, title] of choices) field.add(new Option(title, val));
    field.value = value;
    field.disabled = disabled;
    field.addEventListener('change', () => onChange(field.value));
    box.append(field);
    return box;
  }
  function mountPicker(value, onChange, disabled = false) {
    const box = make('label', 'مسیر مونت');
    const choices = [...mountOptions, ['custom','مسیر دلخواه…']];
    const picker = document.createElement('select');
    for (const [path, title] of choices) picker.add(new Option(title, path));
    const known = mountOptions.some(([path]) => path === value);
    picker.value = known ? value : 'custom';
    picker.disabled = disabled;
    const custom = document.createElement('input');
    custom.placeholder = '/my-data';
    custom.value = known ? '' : (value || '');
    custom.hidden = picker.value !== 'custom' || disabled;
    custom.disabled = disabled;
    picker.addEventListener('change', () => {
      custom.hidden = picker.value !== 'custom';
      if (picker.value !== 'custom') onChange(picker.value);
      else custom.focus();
    });
    custom.addEventListener('change', () => onChange(custom.value.trim()));
    box.append(picker, custom);
    box.getMount = () => picker.value === 'custom' ? custom.value.trim() : picker.value;
    return box;
  }
  function heading(title) { root.append(make('h4', title)); }
  function diskCards() {
    heading('دیسک‌های شناسایی‌شده');
    const grid = make('div', '', 'disk-grid');
    for (const disk of disks()) {
      const active = plan.disks.some(d => d.path === disk.path);
      const card = make('div', '', 'disk-card' + (active ? ' selected' : ''));
      const title = make('h4', `${disk.media || 'Disk'} · ${(disk.size / GiB).toFixed(1)} GiB`);
      card.append(title, make('div', `${disk.model || ''} · ${disk.bus || ''}`));
      card.append(make('code', disk.path));
      if (disk.raid) card.append(make('div', `RAID سخت‌افزاری: ${disk.raid.raid || 'logical volume'}`, 'members'));
      if (disk.members?.length) card.append(make('div', disk.members.map(m =>
        `${m.model || ''} ${m.serial || ''} ${m.location || ''}`).join(' | '), 'members'));
      const use = make('button', active ? 'کنار گذاشتن این دیسک' : 'افزودن به طرح نصب', 'secondary');
      use.type = 'button';
      use.disabled = !!plan.software_raid;
      use.onclick = () => toggleDisk(disk);
      card.append(use);
      if (active && !plan.software_raid) {
        const boot = make('button', disk.path === plan.boot_disk ? 'دیسک بوت' : 'انتخاب به‌عنوان دیسک بوت', 'secondary');
        boot.type = 'button'; boot.disabled = disk.path === plan.boot_disk;
        boot.onclick = () => changeBoot(disk.path); card.append(boot);
      }
      grid.append(card);
    }
    root.append(grid);
    if (!plan.software_raid && (plan.lvm.length || plan.disks.some(d => d.partitions.some(p => p.vg)))) {
      const simple = make('button', 'شروع طرح ساده بدون LVM روی دیسک بوت', 'secondary');
      simple.type = 'button';
      simple.onclick = () => singleDisk(chosen());
      root.append(simple);
    }
    const ssds = disks().filter(d => d.media === 'SSD');
    const other = disks().filter(d => d.media !== 'SSD');
    if (ssds.length && other.length && !plan.software_raid) {
      const quick = make('button', 'طرح SSD + SAS: بوت و بکاپ روی SSD، ریشه روی SAS', 'secondary');
      quick.type = 'button'; quick.onclick = () => splitLayout(ssds[0], other[0]);
      root.append(quick);
    }
  }
  function raidPicker() {
    const candidates = disks().filter(d => String(d.bus).toLowerCase() === 'nvme' || d.path.includes('/nvme-'));
    if (candidates.length < 2) return;
    const box = make('div', '', 'raid-picker');
    box.append(make('h4', 'RAID نرم‌افزاری برای دو NVMe'));
    box.append(make('p', 'دو NVMe را انتخاب کنید. هر دو پاک می‌شوند؛ EFI روی هر دیسک و /boot روی RAID1 ساخته می‌شود. RAID0 برای / افزونگی ندارد.', 'muted'));
    for (const disk of candidates) {
      const label = make('label', `${disk.model || 'NVMe'} · ${(disk.size / GiB).toFixed(1)} GiB`);
      const check = document.createElement('input');
      check.type = 'checkbox'; check.value = disk.path; check.className = 'raid-member';
      check.checked = !!plan.software_raid?.members.includes(disk.path);
      label.prepend(check); box.append(label);
    }
    const level = select('سطح RAID برای /', [['1','RAID1 — آینه‌ای'],['0','RAID0 — نواری، بدون افزونگی']], String(plan.software_raid?.level ?? 1), () => {});
    box.append(level);
    const button = make('button', 'ساخت طرح RAID', 'secondary');
    button.type = 'button';
    button.onclick = () => raidOne([...box.querySelectorAll('.raid-member:checked')].map(x => x.value), Number(level.querySelector('select').value));
    box.append(button);
    if (plan.software_raid) {
      const back = make('button', 'بازگشت به تک‌دیسک', 'secondary');
      back.type = 'button'; back.onclick = () => singleDisk(chosen()); box.append(back);
    }
    root.append(box);
  }
  function row(item, index, kind, diskPath = '') {
    const holder = make('div', '', 'part-row');
    const locked = item.name === 'efi' || item.name === 'bios';
    const pv = !!item.vg && kind === 'partition';
    holder.append(input('نام', item.name, value => {
      const name = value.trim();
      const peers = kind === 'lvm' ? plan.lvm : diskPlan(diskPath).partitions;
      if (!validName(name) || peers.some(other => other !== item && other.name === name)) {
        render(); notify('نام باید با حرف انگلیسی آغاز شود، فقط حروف/عدد/_/- داشته باشد و روی همین دیسک یکتا باشد.'); return;
      }
      item.name = name; commit();
    }, locked));
    const autoSize = item.size === -1;
    const sizeBox = input(autoSize ? 'اندازه (خودکار)' : 'اندازه',
      autoSize ? freeSizeText(kind, diskPath) : String(item.size ?? ''), value => {
        const parsed = parseSize(value);
        if (parsed !== -1 && !/^[1-9][0-9]*[KMGT]$/.test(parsed)) {
          render(); notify('اندازه را مانند 100G وارد کنید؛ برای استفاده از همهٔ فضای آزاد، کادر را خالی بگذارید.'); return;
        }
        item.size = parsed; commit();
      }, locked);
    if (autoSize && !locked) sizeBox.append(make('small', 'عدد تقریبی است؛ کادر را خالی کنید تا کل فضای آزاد این دیسک استفاده شود.', 'muted'));
    holder.append(sizeBox);
    holder.append(select('فایل‌سیستم', [['ext4','ext4'],['xfs','xfs'],['fat32','FAT32'],['swap','swap'],['none','بدون فایل‌سیستم']],
      item.filesystem || 'none', value => {
        if (value === 'none') delete item.filesystem; else item.filesystem = value;
        if (value === 'swap') delete item.mount;
        commit();
      }, locked || pv));
    if (locked || pv || item.filesystem === 'swap') {
      holder.append(input('مسیر مونت', item.mount || (item.filesystem === 'swap' ? '[swap]' : '—'), () => {}, true));
    } else {
      holder.append(mountPicker(item.mount || '', value => {
        const occupied = [...plan.disks.flatMap(d => d.partitions), ...plan.lvm]
          .some(other => other !== item && other.mount === value);
        if (!validMount(value) || occupied) {
          render(); notify('مسیر مونت باید معتبر و یکتا باشد؛ مانند /backup.'); return;
        }
        item.mount = value; commit();
      }));
    }
    const remove = make('button', 'حذف', 'secondary');
    remove.type = 'button';
    remove.disabled = locked;
    if (locked) remove.title = 'پارتیشن EFI/BIOS برای بوت لازم است و خودکار ساخته می‌شود.';
    remove.onclick = () => { if (kind === 'lvm') plan.lvm.splice(index, 1);
      else diskPlan(diskPath).partitions.splice(index, 1); commit(); };
    holder.append(remove);
    return holder;
  }
  function bar(path) {
    const disk = disks().find(d => d.path === path);
    if (!disk) return;
    const total = disk.size / GiB;
    const pieces = diskPlan(path)?.partitions || [];
    const fixed = pieces.filter(p => p.size !== -1).reduce((sum, p) => sum + sizeGiB(p.size), 0);
    const strip = make('div', '', 'spacebar');
    for (const piece of pieces) {
      const length = piece.size === -1 ? Math.max(0, total - fixed) : sizeGiB(piece.size);
      const color = piece.name === 'efi' ? 'efi' : piece.name === 'boot' ? 'boot' : piece.name === 'swap' ? 'swap' : 'data';
      const segment = make('span', '', color);
      segment.style.width = `${Math.max(0, length / total * 100)}%`;
      segment.title = `${piece.mount || piece.name}: ${piece.size === -1 ? 'حدود ' + freeSizeText('partition', path) + ' (خودکار)' : piece.size}`;
      strip.append(segment);
    }
    root.append(strip);
    root.append(make('p', `ظرفیت دیسک: ${total.toFixed(1)} GiB · اندازه‌های ثابت: ${fixed.toFixed(1)} GiB · فضای خودکار: حدود ${freeSizeText('partition', path)}`, 'muted'));
  }
  function addForm() {
    heading('افزودن پارتیشن یا حجم');
    const form = make('div', '', 'part-row add-row');
    const type = select('نوع پیشرفته', [['direct','پارتیشن معمولی'],['pv','فضای خام LVM (PV)'],['lvm','حجم LVM']], 'direct', () => {});
    if (plan.software_raid) type.querySelector('select').disabled = true;
    if (plan.software_raid) type.querySelector('select').value = 'lvm';
    const target = select('دیسک مقصد', plan.disks.map(d => [d.path, disks().find(x => x.path === d.path)?.model || d.path]), plan.boot_disk, () => {}, !!plan.software_raid);
    const nameField = input('نام پارتیشن', '', () => {});
    nameField.querySelector('input').placeholder = 'مثلاً backup';
    const size = input('اندازه (خالی = کل فضای آزاد)', '', () => {});
    const fs = select('فایل‌سیستم', [['ext4','ext4'],['xfs','xfs'],['swap','swap']], 'ext4', () => {});
    const mount = mountPicker('/backup', () => {});
    const advanced = make('details', '', 'advanced-storage');
    advanced.append(make('summary', 'گزینه‌های پیشرفتهٔ LVM'),
      make('p', 'LVM مدیریت انعطاف‌پذیر حجم‌هاست و با RAID نرم‌افزاری فرق دارد. برای نصب معمولی، پارتیشن مستقیم بسازید.', 'muted'), type);
    const updateForm = () => {
      const kind = type.querySelector('select').value;
      const path = target.querySelector('select').value;
      size.querySelector('input').placeholder = `حدود ${freeSizeText(kind, path)} آزاد`;
      mount.hidden = kind === 'pv' || fs.querySelector('select').value === 'swap';
    };
    target.querySelector('select').addEventListener('change', updateForm);
    type.querySelector('select').addEventListener('change', updateForm);
    fs.querySelector('select').addEventListener('change', updateForm);
    updateForm();
    const add = make('button', 'افزودن'); add.type = 'button';
    add.onclick = () => {
      const path = mount.getMount();
      const value = parseSize(size.querySelector('input').value);
      const system = fs.querySelector('select').value;
      const kind = type.querySelector('select').value;
      const currentMounts = [...plan.disks.flatMap(d => d.partitions), ...plan.lvm].map(p => p.mount).filter(Boolean);
      if (!(value === -1 || (typeof value === 'string' && /^[1-9][0-9]*[KMGT]$/.test(value)))) return notify('اندازه را مانند 100G وارد کنید یا برای کل فضای آزاد خالی بگذارید.');
      if (kind !== 'pv' && system !== 'swap' && (!validMount(path) || currentMounts.includes(path)))
        return notify('مسیر مونت باید یکتا و معتبر باشد؛ مثلاً /backup.');
      const suggested = kind === 'pv' ? `pv${nextName++}` : system === 'swap' ? 'swap' : path === '/' ? 'root' : path.split('/').filter(Boolean).pop();
      const name = nameField.querySelector('input').value.trim() || suggested;
      const peers = kind === 'lvm' ? plan.lvm : diskPlan(target.querySelector('select').value).partitions;
      if (!validName(name) || peers.some(p => p.name === name))
        return notify('نام باید با حرف انگلیسی آغاز شود و روی دیسک مقصد یکتا باشد؛ مثلاً backup.');
      const item = {name, size: value, filesystem: system};
      if (system !== 'swap') item.mount = path;
      if (kind === 'pv') {
        const parts = diskPlan(target.querySelector('select').value).partitions;
        if (value === -1 && parts.some(p => p.size === -1))
          return notify('این دیسک از قبل بخشی با اندازهٔ «باقی‌مانده» دارد.');
        const fill = parts.findIndex(p => p.size === -1);
        parts.splice(fill < 0 ? parts.length : fill, 0, {name, size: value, vg: 'vg0'});
      } else if (kind === 'direct' && !plan.software_raid) {
        const parts = diskPlan(target.querySelector('select').value).partitions;
        if (value === -1 && parts.some(p => p.size === -1))
          return notify('این دیسک از قبل بخشی با اندازهٔ «باقی‌مانده» دارد؛ ابتدا آن را حذف یا اندازه‌اش را محدود کنید.');
        const fill = parts.findIndex(p => p.size === -1);
        parts.splice(fill < 0 ? parts.length : fill, 0, item);
      } else {
        if (!plan.software_raid && !plan.disks.some(d => d.partitions.some(p => p.vg === 'vg0')))
          return notify('برای LVM ابتدا روی یکی از دیسک‌ها PV تعریف کنید؛ برای نصب ساده «پارتیشن مستقیم» را انتخاب کنید.');
        item.vg = 'vg0';
        if (value === -1 && plan.lvm.some(v => v.size === -1))
          return notify('در LVM یک حجم دیگر باقی‌ماندهٔ فضا را مصرف می‌کند؛ ابتدا آن را حذف یا محدود کنید.');
        const last = plan.lvm.findIndex(v => v.size === -1);
        plan.lvm.splice(last < 0 ? plan.lvm.length : last, 0, item);
      }
      commit(); notify(`${kind === 'pv' ? 'PV' : path || 'swap'} به طرح افزوده شد.`);
    };
    form.append(target, nameField, size, fs, mount, add);
    root.append(form);
    if (!plan.software_raid) root.append(advanced);
  }
  function render() {
    if (!state || !plan) return;
    root.replaceChildren();
    diskCards(); raidPicker();
    const info = make('div', '', 'editor-message'); info.id = 'editor-message';
    info.textContent = plan.software_raid ? `RAID${plan.software_raid.level} نرم‌افزاری: هر دو NVMe پاک می‌شوند.` :
      'فقط دیسک‌های افزوده‌شده به طرح پاک می‌شوند. EFI روی دیسک بوت خودکار حفظ می‌شود.';
    root.append(info);
    heading(plan.software_raid ? 'بخش‌های بوت روی RAID' : 'پارتیشن‌های دیسک‌ها');
    if (plan.software_raid) {
      root.append(make('p', `EFI: 1G روی هر NVMe · /boot: 2G روی RAID1 · / روی RAID${plan.software_raid.level} + LVM`, 'muted'));
    } else {
      for (const disk of plan.disks) {
        const actual = disks().find(d => d.path === disk.path);
        heading(`${actual?.media || 'دیسک'} · ${actual?.model || disk.path}${disk.path === plan.boot_disk ? ' · دیسک بوت' : ''}`);
        bar(disk.path);
        disk.partitions.forEach((item, i) => root.append(row(item, i, 'partition', disk.path)));
      }
    }
    if (plan.software_raid || plan.lvm.length || plan.disks.some(d => d.partitions.some(p => p.vg))) {
      heading(plan.software_raid ? 'حجم‌های سیستم روی RAID نرم‌افزاری' : 'حجم‌های LVM (پیشرفته)');
      if (!plan.software_raid) root.append(make('p', 'PV فضای خامِ پشت حجم‌های LVM است و مسیر مونت ندارد. برای طرح معمولی، دکمهٔ «شروع طرح ساده بدون LVM» را بزنید.', 'muted'));
      plan.lvm.forEach((item, i) => root.append(row(item, i, 'lvm')));
    }
    addForm();
  }
  window.partitionEditor = {singleDisk() {
    if (!plan || !chosen()) return notify('ابتدا شناسایی زندهٔ دیسک‌ها را انجام دهید.');
    singleDisk(chosen());
    document.getElementById('storage-section')?.scrollIntoView({behavior: 'smooth'});
  }, sync(s) {
    if (!s.plan_data || !s.storage) return;
    state = s;
    if (s.plan !== serverPlan) {
      serverPlan = s.plan;
      plan = structuredClone(s.plan_data);
      render();
    }
  }};
  if (typeof refresh === 'function') refresh().catch(() => {});
})();
