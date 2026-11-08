import pathlib
import subprocess
import time

queue = pathlib.Path('.tools/serial-input.bin')
queue.write_bytes(b'')
args = ['ssh', '-T', '-o', 'StrictHostKeyChecking=yes', '-o', 'UserKnownHostsFile=.tools/ilo_known_hosts',
        '-o', 'PubkeyAuthentication=no', '-o', 'ConnectTimeout=8', '-o', 'HostKeyAlgorithms=+ssh-rsa',
        '-o', 'KexAlgorithms=+diffie-hellman-group14-sha1', 'ILO@193.141.127.2']
with open('.tools/serial-debug.log', 'wb') as output:
    process = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=output, stderr=subprocess.STDOUT)
    process.stdin.write(b'vsp\r\n')
    process.stdin.flush()
    cursor = 0
    deadline = time.monotonic() + 10800
    try:
        while process.poll() is None and time.monotonic() < deadline:
            data = queue.read_bytes()
            if len(data) > cursor:
                process.stdin.write(data[cursor:])
                process.stdin.flush()
                cursor = len(data)
            time.sleep(.2)
    finally:
        if process.poll() is None:
            process.kill()
        process.wait()


