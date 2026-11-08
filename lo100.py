"""Lights-Out 100 tools; independent of the iLO4/5 panel and its saved inventory."""
import argparse
import getpass
import json
import os
import re
import secrets
import subprocess
from datetime import datetime
from pathlib import Path
import yaml
from src.lo100 import Lo100, console_command


from src.lo100_ubuntu import prepare_ubuntu


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['inspect', 'console', 'prepare-ubuntu'])
    parser.add_argument('--address')
    parser.add_argument('--username', default='ILO')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--java', default='java')
    parser.add_argument('--jar', type=Path, help='Use an existing vendor M2.JAR; enter management credentials inside Java')
    parser.add_argument('--launch', action='store_true')
    parser.add_argument('--source', type=Path, default=Path('ISO/ubuntu-24.04.3-live-server-amd64.iso'))
    parser.add_argument('--hostname', default='ubuntu-g6')
    args = parser.parse_args(argv)
    output = args.output or Path('generated/lo100') / datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
    if args.command == 'prepare-ubuntu':
        password = os.environ.get('LO100_OS_PASSWORD') or secrets.token_urlsafe(20)
        iso = prepare_ubuntu(args.source, output, args.hostname, password)
        print('Prepared ISO:', iso.resolve())
        print('Local login credentials:', (output / 'login-credentials.json').resolve())
        print('Storage and network selection remain interactive. No server boot or installation was requested.')
        return 0
    if args.command == 'console' and args.jar:
        command = console_command(args.jar, args.java)
        if args.launch:
            return subprocess.run(command).returncode
        print('Vendor standalone Java console validated. Add --launch to open it.')
        return 0
    address = args.address or input('LO100 address: ').strip()
    password = os.environ.get('LO100_PASSWORD') or getpass.getpass('LO100 management password: ')
    controller = Lo100(address, args.username, password)
    if args.command == 'inspect':
        print(json.dumps(controller.inspect(), indent=2))
        return 0
    jar = controller.prepare_console(output)
    command = console_command(jar, args.java)
    print('Vendor console prepared:', jar)
    print('Enter the controller address and management credentials in the Java console.')
    if args.launch:
        return subprocess.run(command).returncode
    else:
        print('Add --launch to open the Java console. No power or keyboard commands are sent by this tool.')
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as exc:
        print('[FAIL]', type(exc).__name__, str(exc))
        raise SystemExit(1)
