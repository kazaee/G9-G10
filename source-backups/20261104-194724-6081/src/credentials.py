"""OS credentials: hash via OpenSSL stdin, never command-line arguments."""
import json
import secrets
import shutil
import subprocess


def password_hash(password):
    if len(password) < 12 or '\n' in password or '\r' in password:
        raise ValueError('Use an administrator password of at least 12 characters without newlines')
    executable = shutil.which('openssl')
    if not executable:
        raise ValueError('OpenSSL is required to hash the administrator password')
    result = subprocess.run([executable, 'passwd', '-6', '-stdin'], input=password + '\n',
                            capture_output=True, text=True, encoding='utf-8', check=True)
    hashed = result.stdout.strip()
    if not hashed.startswith('$6$'):
        raise ValueError('OpenSSL did not produce a SHA-512 crypt password hash')
    return hashed


def ensure_login(config, generated):
    identity = config['identity']
    if not identity.get('generate_password') or identity.get('password_hash'):
        return
    path = generated / 'login-credentials.json'
    target = {'ilo_address': config['ilo']['address'], 'username': identity['username']}
    saved = json.loads(path.read_text()) if path.exists() else {}
    if any(saved.get(k) != v for k, v in target.items()):
        password = secrets.token_urlsafe(18)
        saved = dict(target, password=password, password_hash=password_hash(password))
        path.write_text(json.dumps(saved, indent=2))
        path.chmod(0o600)
    identity['password_hash'] = saved['password_hash']
    print('[LOGIN] Administrator credentials saved locally:', path, '(not printed in logs)')
