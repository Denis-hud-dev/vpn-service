"""Owner-only local key ceremony. No network or server/database mutations.

Backup payloads use the standard age format. Only the native age identity is
password-wrapped here: Scrypt + authenticated Fernet from cryptography.
Never pass a password as an argument/environment variable or persist plaintext.
"""
from __future__ import annotations

import base64
import datetime
import fcntl
import getpass
import hashlib
import json
import os
from pathlib import Path
import re
import resource
import secrets
import stat
import subprocess
import sys
import warnings

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

FORMAT = 'kudavpn-age-identity-v1'
N, R, P = 131072, 8, 1
MAX_KEY_FILE = 16384
TOOLS = {
    'age': '4012dfc2725883beafb710894af4f599b7a94f8c8e0f51f02cc96ab8df33915e',
    'age-keygen': 'c16e229245123d0ad27442317461d63915416cad0294395cd19ca93feb3211ea',
}
RECIPIENT = re.compile(r'age1[023456789acdefghjklmnpqrstuvwxyz]{58}')


def private_directory(path):
    path = Path(path).absolute()
    if path.is_symlink() or path.resolve() != path:
        raise ValueError('Symlink paths are forbidden')
    if any((parent / '.git').exists() for parent in (path, *path.parents)):
        raise ValueError('Key storage must be outside Git')
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.stat()
    if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
        raise ValueError('Private directory must be owned by you with mode0700')
    return path


def read_private(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, 'rb') as file:
        info = os.fstat(file.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                or info.st_uid != os.getuid()
                or stat.S_IMODE(info.st_mode) != 0o600
                or info.st_size > MAX_KEY_FILE):
            raise ValueError('Unsafe key-file ownership, permissions or size')
        return file.read(MAX_KEY_FILE + 1)


def exclusive_write(path, data):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as file:
        os.fchmod(file.fileno(), 0o600)
        file.write(data)
        file.flush()
        os.fsync(file.fileno())
    directory = os.open(Path(path).parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def derive(password, salt):
    return base64.urlsafe_b64encode(Scrypt(salt=salt, length=32, n=N, r=R, p=P).derive(password.encode('utf-8')))


def wrap(identity, password):
    if not 16 <= len(password) <= 1024 or '\n' in password or '\r' in password:
        raise ValueError('Use a unique passphrase of at least16 characters')
    salt = secrets.token_bytes(16)
    token = Fernet(derive(password, salt)).encrypt(identity).decode('ascii')
    return json.dumps(dict(format=FORMAT, kdf='scrypt', n=N, r=R, p=P,
                           salt=base64.b64encode(salt).decode('ascii'), token=token),
                      sort_keys=True).encode('ascii') + b'\n'


def unwrap(encoded, password):
    try:
        if len(encoded) > MAX_KEY_FILE:
            raise ValueError
        item = json.loads(encoded)
        if (item['format'] != FORMAT or item['kdf'] != 'scrypt'
                or type(item['n']) is not int or type(item['r']) is not int
                or type(item['p']) is not int or (item['n'], item['r'], item['p']) != (N, R, P)):
            raise ValueError
        salt = base64.b64decode(item['salt'], validate=True)
        if len(salt) != 16 or not isinstance(item['token'], str):
            raise ValueError
        identity = Fernet(derive(password, salt)).decrypt(item['token'].encode('ascii'))
        if not re.search(rb'^AGE-SECRET-KEY-1[0-9A-Z]+$', identity, re.M):
            raise ValueError
        return identity
    except (ValueError, KeyError, TypeError, UnicodeError, InvalidToken):
        # Deliberately never include input data, passwords or child stderr.
        raise ValueError('Key unlock failed or encrypted file is invalid') from None


def tool(directory, name):
    path = Path(directory) / name
    if (path.is_symlink() or not path.is_file()
            or path.stat().st_uid != os.getuid()
            or path.stat().st_mode & 0o022
            or hashlib.sha256(path.read_bytes()).hexdigest() != TOOLS[name]):
        raise ValueError('Encryption tool did not match the verified release')
    return str(path)


def run_keygen(binary, *args, data=None):
    result = subprocess.run([binary, *args], input=data, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, timeout=30, check=False)
    if result.returncode or len(result.stdout) > MAX_KEY_FILE:
        raise ValueError('age-keygen failed; sensitive output suppressed')
    return result.stdout


def recipient(binary, identity):
    value = run_keygen(binary, '-y', data=identity).decode('ascii').strip()
    if not RECIPIENT.fullmatch(value):
        raise ValueError('Unexpected age recipient')
    return value


def prepare(directory, tools_directory, *, prompt=getpass.getpass):
    directory = private_directory(directory)
    binary = tool(tools_directory, 'age-keygen')
    tool(tools_directory, 'age')
    lock_fd = os.open(directory / '.setup.lock', os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(lock_fd)
        if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1:
            raise ValueError('Unsafe setup lock')
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        encrypted = directory / 'recovery-key.encrypted.json'
        public = directory / 'recipient.txt'
        ready = directory / 'key-ready.json'
        password = prompt('Recovery passphrase (hidden; at least16 characters): ')
        if encrypted.exists() or encrypted.is_symlink():
            identity = unwrap(read_private(encrypted), password)
            encoded = read_private(encrypted)
        else:
            if ready.exists() or public.exists():
                raise ValueError('Existing key metadata without identity; refusing replacement')
            if password != prompt('Repeat recovery passphrase (hidden): '):
                raise ValueError('Passphrases do not match')
            if len(password) < 16:
                raise ValueError('Use at least16 characters')
            identity = run_keygen(binary)
            encoded = wrap(identity, password)
            if unwrap(encoded, password) != identity:
                raise ValueError('Key round-trip verification failed')
            exclusive_write(encrypted, encoded)
        if unwrap(read_private(encrypted), password) != identity:
            raise ValueError('Persisted key verification failed')
        value = recipient(binary, identity)
        public_bytes = (value + '\n').encode('ascii')
        if public.exists() or public.is_symlink():
            if read_private(public) != public_bytes:
                raise ValueError('Existing recipient does not match; refusing replacement')
        else:
            exclusive_write(public, public_bytes)
        digest = hashlib.sha256(encoded).hexdigest()
        if ready.exists() or ready.is_symlink():
            previous = json.loads(read_private(ready))
            if previous.get('encrypted_key_sha256') != digest or previous.get('recipient') != value:
                raise ValueError('Existing ready record does not match')
        else:
            item = dict(format=FORMAT, recipient=value, encrypted_key_sha256=digest,
                        verified=True, created_utc=datetime.datetime.now(datetime.timezone.utc).isoformat())
            exclusive_write(ready, json.dumps(item, sort_keys=True).encode('ascii') + b'\n')
        del identity, password
        return encrypted
    finally:
        os.close(lock_fd)


def main():
    if len(sys.argv) != 1 or not sys.stdin.isatty():
        print('Run this script interactively in your own Mac Terminal; no secret arguments.')
        return 1
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    warnings.simplefilter('error', getpass.GetPassWarning)
    try:
        directory = Path.home() / 'Documents/KudaVPN-private/backups'
        result = prepare(directory, directory / 'tools')
        print('Recovery key encrypted and verified. No server was changed.')
        print('Encrypted key file:', result)
        print('Keep its passphrase in your password manager; copy this encrypted file to offline storage.')
        print('Never send the passphrase or the encrypted key in chat or Git.')
        return 0
    except (ValueError, OSError, subprocess.SubprocessError, getpass.GetPassWarning):
        print('Setup not completed. Existing keys were not replaced. No server was changed.')
        print('Check the passphrase, private-directory permissions and verified tools, then retry.')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
