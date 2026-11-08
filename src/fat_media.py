"""Portable 64 MiB FAT16 answer media; no mounted disks or administrator tools."""
import re
import secrets
import struct
from pathlib import Path


def answer_usb_image(destination, files, label='OEMDRV'):
    if not re.fullmatch(r'[A-Z0-9_]{1,11}', label):
        raise ValueError('Invalid FAT label')
    sector, sectors, cluster, fat_sectors, root_sectors = 512, 131072, 4, 128, 32
    data_start = 1 + 2 * fat_sectors + root_sectors
    fat = bytearray(fat_sectors * sector)
    struct.pack_into('<HH', fat, 0, 0xfff8, 0xffff)
    directory = bytearray(root_sectors * sector)
    directory[:11] = label.ljust(11).encode('ascii'); directory[11] = 8
    payloads, next_cluster, names = [], 2, set()
    if len(files) > 511:
        raise ValueError('Too many answer files')
    for index, (name, value) in enumerate(files.items(), 1):
        if not re.fullmatch(r'[A-Za-z0-9_]{1,8}(?:[.][A-Za-z0-9_]{1,3})?', name):
            raise ValueError('Answer files require root-level FAT 8.3 names')
        base, _, extension = name.upper().partition('.')
        short = base.ljust(8) + extension.ljust(3)
        if short in names: raise ValueError('Duplicate FAT filename')
        names.add(short)
        value = value.encode('utf-8') if isinstance(value, str) else bytes(value)
        count = (len(value) + sector * cluster - 1) // (sector * cluster)
        if next_cluster + count - 2 > (sectors - data_start) // cluster:
            raise ValueError('Answer files exceed image capacity')
        offset = index * 32
        directory[offset:offset+11] = short.encode('ascii'); directory[offset+11] = 0x20
        struct.pack_into('<H', directory, offset+26, next_cluster if count else 0)
        struct.pack_into('<I', directory, offset+28, len(value))
        for number in range(count):
            current = next_cluster + number
            struct.pack_into('<H', fat, current * 2, 0xffff if number == count-1 else current+1)
        payloads.append(((data_start + (next_cluster-2) * cluster) * sector, value))
        next_cluster += count
    boot = bytearray(512); boot[:3] = bytes([0xeb,0x3c,0x90]); boot[3:11] = b'MSDOS5.0'
    struct.pack_into('<HBHBHHBHHHII', boot, 11, sector, cluster, 1, 2, 512, 0, 0xf8, fat_sectors, 63, 255, 0, sectors)
    boot[36] = 0x80; boot[38] = 0x29
    struct.pack_into('<I', boot, 39, secrets.randbits(32))
    boot[43:54] = label.ljust(11).encode('ascii'); boot[54:62] = b'FAT16   '; boot[510:512] = bytes([0x55,0xaa])
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open('xb') as output:
        output.truncate(sector * sectors)
        output.seek(0); output.write(boot)
        output.seek(sector); output.write(fat); output.write(fat); output.write(directory)
        for offset, value in payloads:
            output.seek(offset); output.write(value)
    return destination
