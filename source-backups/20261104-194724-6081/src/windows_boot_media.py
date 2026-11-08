"""Replace only the Windows DVD UEFI boot image with its vendor no-prompt variant."""
import io
from pathlib import Path

import pycdlib


def no_prompt_patch(source):
    source = Path(source)
    iso = pycdlib.PyCdlib()
    iso.open(str(source))
    try:
        normal, no_prompt = io.BytesIO(), io.BytesIO()
        iso.get_file_from_iso_fp(normal, udf_path='/efi/microsoft/boot/efisys.bin')
        iso.get_file_from_iso_fp(no_prompt, udf_path='/efi/microsoft/boot/efisys_noprompt.bin')
        sections = [entry for section in iso.eltorito_boot_catalog.sections
                    if section.platform_id == 0xef for entry in section.section_entries]
        if len(sections) != 1 or len(normal.getvalue()) != len(no_prompt.getvalue()):
            raise ValueError('Windows ISO UEFI boot layout cannot be safely patched')
        offset = sections[0].load_rba * 2048
        with source.open('rb') as image:
            image.seek(offset)
            if image.read(len(normal.getvalue())) != normal.getvalue():
                raise ValueError('Windows UEFI boot catalog does not reference efisys.bin')
    finally:
        iso.close()
    return offset, no_prompt.getvalue()
