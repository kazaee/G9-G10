"""Recover this probe's inventory from an optional iLO serial capture."""
import base64
import json
import re
import zlib


def from_serial(path, probe_id, marker='INVENTORY_ZLIB_BASE64', identity='probe_id', event='live-inventory'):
    if path is None or not path.exists():
        return None
    with path.open('rb') as stream:
        stream.seek(0, 2)
        stream.seek(max(0, stream.tell() - 512_000))
        text = stream.read().decode(errors='replace')
    for encoded in reversed(re.findall(re.escape(marker) + r'=([A-Za-z0-9+/=]+)', text)):
        try:
            decoder = zlib.decompressobj()
            raw = decoder.decompress(base64.b64decode(encoded, validate=True), 2_000_001)
            if len(raw) > 2_000_000 or not decoder.eof:
                continue
            result = json.loads(raw)
            if isinstance(result, dict) and result.get(identity) == probe_id and result.get('event') == event:
                return result
        except (ValueError, zlib.error):
            continue
    return None
