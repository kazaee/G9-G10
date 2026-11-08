"""OpenSSH VSP capture with generation-aware terminal setup and diagnostics."""
import os
import re
import subprocess
import tempfile
import time
from pathlib import Path
from urllib.parse import urlsplit


class SerialCapture:
    def __init__(self, config, password, log):
        self.config, self.password, self.log = config, password, Path(log)
        self.process = self.output = self.directory = None

    def _diagnostic(self, text):
        lower = text.lower()
        if 'currently in use' in lower or 'already in use' in lower:
            return 'iLO VSP is occupied by another session. Close the other text console and retry.'
        if 'permission denied' in lower or 'authentication failed' in lower:
            return 'iLO SSH login was rejected; verify credentials and SSH access for this account.'
        if 'no matching' in lower or 'bad configuration option' in lower or 'bad key types' in lower:
            return 'SSH algorithm negotiation failed; inspect the offered algorithms in the serial log.'
        if 'host key verification failed' in lower or 'remote host identification has changed' in lower:
            return 'SSH host key verification failed; verify the target identity before updating generated/ilo-known-hosts.'
        if any(s in lower for s in ('connection refused', 'connection timed out', 'no route to host', 'could not resolve hostname')):
            return 'iLO SSH is unreachable; check SSH enablement, SSH port, jump-host routing and firewall.'
        if any(s in lower for s in ('not authorized', 'insufficient privilege', 'access denied', 'permission required')):
            return 'iLO denied VSP access; check the account Remote Console privilege.'
        return None

    def _failure(self, reason):
        text = self.log.read_text(errors='replace') if self.log.exists() else ''
        # Never include a credential, even if a device unexpectedly echoes it.
        safe = text.replace(self.password, '[REDACTED]') if self.password else text
        safe = re.sub(r'\x1b\[[0-?]*[ -/]*[@-~]', '', safe)
        tail = '\n'.join(safe.splitlines()[-12:])[-2400:]
        return RuntimeError(f'{reason} No ISO boot or disk wipe has started. '
                            f'SSH/VSP log: {self.log.resolve()}\n{tail}')

    def start(self):
        self.log.parent.mkdir(parents=True, exist_ok=True)
        self.output = self.log.open('wb')
        try:
            self.directory = tempfile.TemporaryDirectory(prefix='ilo-askpass-')
            askpass = Path(self.directory.name) / ('askpass.cmd' if os.name == 'nt' else 'askpass.sh')
            askpass.write_text('@echo off\npowershell.exe -NoProfile -Command "[Console]::Write($env:ILO_PASSWORD)"\n'
                              if os.name == 'nt' else '#!/bin/sh\nprintf "%s" "$ILO_PASSWORD"\n')
            askpass.chmod(0o700)
            env = dict(os.environ, ILO_PASSWORD=self.password, SSH_ASKPASS=str(askpass),
                       SSH_ASKPASS_REQUIRE='force', DISPLAY=os.environ.get('DISPLAY') or ':0', TERM='vt100')
            address = self.config['address']
            host = urlsplit(address if '://' in address else 'https://' + address).hostname
            if not host or host.startswith('-'):
                raise ValueError('Invalid iLO SSH host')
            known = Path('generated/ilo-known-hosts').resolve()
            known.parent.mkdir(parents=True, exist_ok=True)
            generation = str(self.config.get('generation') or '')
            # Preserve the proven iLO 4 path; request a PTY for newer/unknown firmware.
            terminal = '-T' if generation == '4' else '-tt'
            args = ['ssh', terminal, '-o', 'StrictHostKeyChecking=accept-new',
                    '-o', 'UserKnownHostsFile=' + str(known), '-o', 'PubkeyAuthentication=no',
                    '-o', 'ConnectTimeout=10', '-o', 'ConnectionAttempts=1',
                    '-o', 'NumberOfPasswordPrompts=1', '-o', 'ServerAliveInterval=15',
                    '-o', 'ServerAliveCountMax=3']
            if generation == '4':
                args += ['-o', 'HostKeyAlgorithms=+ssh-rsa',
                         '-o', 'KexAlgorithms=+diffie-hellman-group14-sha1']
            args += ['-p', str(int(self.config.get('ssh_port', 22))),
                     '-l', self.config['username'], host]
            self.output.write(f'[VSP] generation={generation or "unknown"}; terminal={terminal}; '
                              f'ssh_port={self.config.get("ssh_port", 22)}\n'.encode())
            self.output.flush()
            self.process = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=self.output,
                                            stderr=subprocess.STDOUT, env=env)
            sent = False
            deadline = time.monotonic() + 45
            while time.monotonic() < deadline:
                output = self.log.read_text(errors='replace')
                error = self._diagnostic(output)
                if error:
                    raise self._failure(error)
                if re.search(r'virtual serial port\s+(?:is\s+)?active\b', output, re.I):
                    if self.process.poll() is not None:
                        raise self._failure('iLO closed SSH immediately after VSP activation.')
                    return self
                if self.process.poll() is not None:
                    raise self._failure(f'iLO SSH exited (code {self.process.returncode}) before VSP activation.')
                # iLO 5 receives the command only after the CLP prompt is ready.
                if not sent and (generation == '4' or re.search(r'hpiLO[^\r\n]*->\s*$', output, re.I)):
                    self.process.stdin.write(b'vsp\r\n')
                    self.process.stdin.flush()
                    sent = True
                time.sleep(.5)
            raise self._failure('Timed out waiting for ' + ('VSP activation.' if sent else 'the iLO SSH CLI prompt.'))
        except Exception as exc:
            if not isinstance(exc, RuntimeError):
                exc = self._failure(f'Could not start iLO SSH/VSP: {exc}')
            self.close()
            raise exc

    def close(self):
        try:
            if self.process and self.process.poll() is None:
                self.process.terminate()
                try:
                    self.process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(timeout=5)
        finally:
            if self.process and self.process.stdin:
                self.process.stdin.close()
            if self.output:
                self.output.close()
            if self.directory:
                self.directory.cleanup()
