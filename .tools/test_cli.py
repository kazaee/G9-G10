import subprocess
args = ['ssh', '-T', '-o', 'StrictHostKeyChecking=yes', '-o', 'UserKnownHostsFile=.tools/ilo_known_hosts', '-o', 'PubkeyAuthentication=no', '-o', 'ConnectTimeout=8', '-o', 'HostKeyAlgorithms=+ssh-rsa', '-o', 'KexAlgorithms=+diffie-hellman-group14-sha1', 'ILO@193.141.127.2']
p = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
try:
    out = p.communicate(b'show /system1\r\nexit\r\n', timeout=15)[0]
except subprocess.TimeoutExpired:
    p.kill()
    out = p.communicate()[0]
print(out.decode(errors='replace'))
