"""Small CD/DVD remaster. El Torito BIOS/UEFI boot entries are retained.

This output is for iLO virtual optical media, not a hybrid USB disk image.
"""
import io
from pathlib import Path
import pycdlib


def build(source, destination, user_data, extra=None, kernel_args=''):
    iso = pycdlib.PyCdlib()
    iso.open(str(source))
    buffers = []

    def add(path, data, replace=False):
        if isinstance(data, str):
            data = data.encode()
        if replace:
            record = iso.get_record(rr_path=path)
            iso.rm_file(iso_path=iso.full_path_from_dirrecord(record))
        iso_path = '/'.join(part.upper().replace('-', '_') for part in path.split('/')) + ';1'
        stream = io.BytesIO(data)
        buffers.append(stream)
        iso.add_fp(stream, len(data), iso_path=iso_path, rr_name=path.rsplit('/', 1)[-1])

    grub = '''set timeout=3
set default=0
menuentry "Automated Ubuntu server" {
    set gfxpayload=keep
    linux /casper/vmlinuz autoinstall ds=nocloud\\;s=/cdrom/nocloud/ fsck.mode=skip console=tty0 console=ttyS1,115200n8 ---
    initrd /casper/initrd
}
'''
    if kernel_args:
        grub = grub.replace(' ---', ' ' + kernel_args + ' ---')
    add('/boot/grub/grub.cfg', grub, True)
    # GRUB loopback entry must use the same unattended arguments.
    try:
        iso.get_record(rr_path='/boot/grub/loopback.cfg')
    except pycdlib.pycdlibexception.PyCdlibInvalidInput:
        pass
    else:
        add('/boot/grub/loopback.cfg', grub, True)
    iso.add_directory(iso_path='/NOCLOUD', rr_name='nocloud')
    add('/nocloud/user-data', user_data)
    add('/nocloud/meta-data', 'instance-id: ilo-provisioner\n')
    for name, data in (extra or {}).items():
        add('/' + name, data)
    # Existing hashes for modified files would cause false media-check failures.
    md5 = io.BytesIO()
    iso.get_file_from_iso_fp(md5, rr_path='/md5sum.txt')
    lines = [line for line in md5.getvalue().decode().splitlines()
             if not any(name in line for name in ('boot/grub/grub.cfg', 'boot/grub/loopback.cfg', 'boot.catalog'))]
    add('/md5sum.txt', '\n'.join(lines) + '\n', True)
    print('[INFO] Writing customized ISO:', destination, flush=True)
    iso.write(str(destination))
    iso.close()
    verify = pycdlib.PyCdlib()
    verify.open(str(destination))
    check = io.BytesIO()
    verify.get_file_from_iso_fp(check, rr_path='/nocloud/user-data')
    expected = user_data.encode() if isinstance(user_data, str) else user_data
    assert check.getvalue() == expected
    assert any(s.platform_id == 0xef for s in verify.eltorito_boot_catalog.sections), 'UEFI entry missing'
    verify.close()
    print('[OK] ISO seed and UEFI boot entry verified', flush=True)
