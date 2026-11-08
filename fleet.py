"""Read-only fleet survey: which iLO generation, model and driver each server needs.

    python fleet.py                      # anonymous detection only
    python fleet.py --login              # also log in and read power state
"""
import argparse
import getpass
import json
import os
from pathlib import Path

import yaml

from src.drivers.detect import detect, connect


def servers(path):
    data = yaml.safe_load(Path(path).read_text()) or {}
    defaults = data.get('defaults', {})
    for entry in data.get('servers', []):
        yield {**defaults, **entry, 'address': entry['ilo']}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--inventory', default='config/servers.yaml')
    parser.add_argument('--login', action='store_true')
    parser.add_argument('--output', default='generated/fleet.json')
    args = parser.parse_args(argv)
    password = (os.environ.get('ILO_PASSWORD') or getpass.getpass('iLO password: ')) if args.login else None
    results = []
    for server in servers(args.inventory):
        row = {'name': server['name'], 'ilo': server['address']}
        try:
            row.update(detect(server['address']))
            if args.login:
                driver = connect(server, password)
                row.update(driver.describe(), power=driver.power_state())
        except Exception as exc:
            row['error'] = f'{type(exc).__name__}: {exc}'
        results.append(row)
        print(f"{row['name']:<16} {row['ilo']:<16} iLO {row.get('generation') or '?'} "
              f"{row.get('protocol') or '-':<8} {row.get('model', '')} {row.get('error', '')}", flush=True)
    Path(args.output).parent.mkdir(exist_ok=True)
    Path(args.output).write_text(json.dumps(results, indent=2))
    return 1 if any('error' in row for row in results) else 0


if __name__ == '__main__':
    raise SystemExit(main())
