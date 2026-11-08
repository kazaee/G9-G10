"""Summarize installer failures without treating a timeout as the root cause."""
import re
from pathlib import Path


def kernel_crashes(serial_path):
    path = Path(serial_path)
    if not path.exists():
        return []
    lines = path.read_text(errors='replace').splitlines()
    crashes = []
    for index, line in enumerate(lines):
        if not any(marker in line for marker in (
                'BUG: unable to handle page fault', 'Kernel panic - not syncing',
                'general protection fault:', 'Oops: Oops:')):
            continue
        if 'Oops: Oops:' in line and any('BUG: unable to handle page fault' in old
                for old in lines[max(0, index - 5):index]):
            continue
        detail = [line.strip()]
        detail.extend(candidate.strip() for candidate in lines[index:index + 16]
            if any(marker in candidate for marker in ('Comm:', 'Hardware name:', 'RIP:')))
        crashes.append({'line': index + 1, 'detail': detail[:5]})
    return crashes


def media_stats(http_log):
    path = Path(http_log)
    if not path.exists():
        return {'requests': 0, 'requested_bytes': 0, 'http_errors': 0}
    content = path.read_text(errors='replace')
    ranges = [(int(a), int(b)) for a, b in
        re.findall(r'Range=bytes=([0-9]+)-([0-9]+)', content)]
    return {'requests': content.count('GET /'),
        'requested_bytes': sum(b - a + 1 for a, b in ranges if b >= a),
        'http_errors': len(re.findall(r'HTTP/1\.1" [45][0-9][0-9] ', content))}


def analyze(serial_path, http_log):
    return {'kernel_crashes': kernel_crashes(serial_path), 'media': media_stats(http_log)}
