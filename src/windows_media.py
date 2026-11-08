"""Build the small iLO USB answer image for an original Windows Server DVD."""
import os
import subprocess
from pathlib import Path


def answer_usb_image(destination, files, label='ANSWER'):
    """Create a 64 MiB FAT32 raw image using Windows DiskPart's fixed VHD writer."""
    if label not in ('ANSWER', 'OEMDRV'):
        raise ValueError('Unsupported answer-media label')
    destination = Path(destination).resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        raise ValueError('Refusing to overwrite an existing answer image')
    letter = next((c for c in 'QWXYZ' if not Path(f'{c}:\\').exists()), None)
    if not letter:
        raise RuntimeError('No free temporary drive letter for the answer image')
    vhd = destination.with_suffix('.vhd')
    script = destination.with_suffix('.diskpart.txt')
    if vhd.exists() or script.exists():
        raise ValueError('Temporary Windows answer-image files already exist')
    attached = False
    def diskpart(commands):
        script.write_text('\n'.join(commands + ['exit', '']), encoding='ascii')
        result = subprocess.run(['diskpart', '/s', str(script)], capture_output=True, text=True, timeout=120)
        if result.returncode or 'error' in result.stdout.lower() or 'error' in result.stderr.lower():
            raise RuntimeError('DiskPart answer-image operation failed: ' + result.stdout[-1000:] + result.stderr[-1000:])
    try:
        diskpart([f'create vdisk file="{vhd}" maximum=64 type=fixed',
                  f'select vdisk file="{vhd}"', 'attach vdisk',
                  'create partition primary', f'format fs=fat32 quick label={label}',
                  f'assign letter={letter}'])
        attached = True
        root = Path(f'{letter}:\\')
        if not root.exists():
            raise RuntimeError('The answer USB image was not mounted')
        for name, data in files.items():
            if not name or '/' in name or '\\' in name or ':' in name:
                raise ValueError('Answer-image files must be placed in the root')
            (root / name).write_bytes(data.encode('utf-8') if isinstance(data, str) else data)
        for name, data in files.items():
            expected = data.encode('utf-8') if isinstance(data, str) else data
            if (root / name).read_bytes() != expected:
                raise RuntimeError('Answer-image readback failed: ' + name)
        diskpart([f'select vdisk file="{vhd}"', 'detach vdisk'])
        attached = False
        if vhd.stat().st_size != 64 * 1024 * 1024 + 512:
            raise RuntimeError('Unexpected fixed-VHD size; refusing to expose a malformed USB image')
        os.replace(vhd, destination)
        with destination.open('r+b') as image:
            image.truncate(64 * 1024 * 1024)
        return destination
    finally:
        if attached:
            try:
                diskpart([f'select vdisk file="{vhd}"', 'detach vdisk'])
            except Exception:
                pass
        if script.exists():
            script.unlink()
