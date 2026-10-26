"""Explicit, reversible provisioning of Monitor only. Root operator entrypoint."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import shutil
import subprocess

from monitor.common import atomic_json

CONFIG = Path('/etc/kudavpn-monitor')
BASE = Path('/opt/kudavpn-monitor')
ACCOUNT = 'kudavpn_monitor'
EXPORT = '/var/lib/kudavpn-monitor-collector/export.json'
UNITS = ('kudavpn-monitor.service', 'kudavpn-monitor-collect.service', 'kudavpn-monitor-collect.timer')
PEERS = {'justhost': ('digitalocean', '209.38.196.186', 'primary'),
         'digitalocean': ('justhost', '194.58.39.247', 'watchdog')}


def run(args, **kwargs):
    return subprocess.run(args, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, **kwargs)


def protected(host):
    if host == 'digitalocean':
        result = run(['docker', 'inspect', '--format', '{{.Id}} {{.State.StartedAt}}',
                      'kudavpn-premium-bot-1', 'kudavpn-premium-postgres-1', 'kudavpn-xray-xray-1'])
        return result.stdout.decode().splitlines()
    lines = run(['ss', '-ltnpH']).stdout.decode().splitlines()
    return sorted(' '.join(line.split()[3:]) for line in lines
                  if re.search(r':(?:443|8443|9443|10443|18443|18543)$', line.split()[3]))


def setup(host, revision, source):
    release = BASE/'releases'/revision[:7]
    backup = BASE/'backups'/revision[:7]
    if CONFIG.exists() or release.exists() or backup.exists():
        raise ValueError('Refusing to overwrite an existing monitor installation')
    for unit in UNITS:
        if Path('/etc/systemd/system', unit).exists():
            raise ValueError('Monitor unit already exists')
    backup.mkdir(parents=True, mode=0o700)
    backup.parent.chmod(0o700)
    atomic_json(backup/'protected.json', protected(host))
    authorized = Path('/root/.ssh/authorized_keys')
    if authorized.is_symlink():
        raise ValueError('Unexpected authorized_keys symlink')
    if authorized.exists():
        shutil.copy2(authorized, backup/'authorized_keys.before')
        (backup/'authorized_keys.before').chmod(0o600)
    try:
        pwd.getpwnam(ACCOUNT)
        raise ValueError('Unexpected pre-existing Monitor account')
    except KeyError:
        run(['useradd', '--system', '--user-group', '--no-create-home', '--home-dir', '/nonexistent',
             '--shell', '/usr/sbin/nologin', ACCOUNT])
    gid = pwd.getpwnam(ACCOUNT).pw_gid
    release.mkdir(parents=True, mode=0o755)
    for name in ('monitor', 'deploy'):
        shutil.copytree(source/name, release/name, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    CONFIG.mkdir(mode=0o750)
    os.chown(CONFIG, 0, gid)
    peer_host, peer_ip, role = PEERS[host]
    atomic_json(CONFIG/'config.json', dict(host=host, peer_host=peer_host, peer_ip=peer_ip, role=role,
                                          revision=revision), 0o640)
    os.chown(CONFIG/'config.json', 0, gid)
    run(['ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-C', 'kudavpn-monitor-'+host,
         '-f', str(CONFIG/'peer-key')])
    (CONFIG/'peer-key').chmod(0o600)
    probe = '--probe-file /etc/kudavpn-monitor/feed-probe-url' if host == 'justhost' else ''
    for unit in UNITS:
        path = release/'deploy/monitor'/unit
        if not path.exists():
            path = Path(str(path)+'.template')
        body = path.read_text().replace('@RELEASE@', str(release)).replace('@HOST@', host).replace('@PROBE@', probe)
        Path('/etc/systemd/system', unit).write_text(body)
    atomic_json(CONFIG/'installation.json', dict(host=host, revision=revision, backup=str(backup), release=str(release)))
    rollback = backup/'rollback.sh'
    rollback.write_text('#!/bin/sh\nset -eu\ncd '+str(release)+'\nexec /usr/bin/python3 -B -m monitor.deployment rollback\n')
    rollback.chmod(0o700)
    print('monitor_prepare=passed')


def authorize(public_key):
    installed = json.loads((CONFIG/'installation.json').read_text())
    peer_host, peer_ip, _ = PEERS[installed['host']]
    parts = Path(public_key).read_text().strip().split()
    if len(parts) < 2 or parts[0] != 'ssh-ed25519' or not re.fullmatch(r'[A-Za-z0-9+/=]{50,120}', parts[1]):
        raise ValueError('Invalid dedicated monitor public key')
    authorized = Path('/root/.ssh/authorized_keys')
    if authorized.is_symlink():
        raise ValueError('Unexpected authorized_keys symlink')
    before = authorized.read_text() if authorized.exists() else ''
    if parts[1] in before:
        raise ValueError('New monitor key is already present')
    line = (f'from="{peer_ip}",restrict,command="/usr/bin/cat {EXPORT}" '
            f'ssh-ed25519 {parts[1]} kudavpn-monitor-readonly-{peer_host}')
    fd = os.open(authorized, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as file:
        os.fchmod(file.fileno(), 0o600)
        file.write(('\n' if before and not before.endswith('\n') else '')+line+'\n')
    atomic_json(Path(installed['backup'])/'added-key.json', {'line': line})
    print('monitor_restricted_key=installed')


def verify():
    config = json.loads((CONFIG/'installation.json').read_text())
    expected = json.loads((Path(config['backup'])/'protected.json').read_text())
    if protected(config['host']) != expected:
        raise ValueError('A protected process changed')
    run(['systemd-analyze', 'verify', *[str(Path('/etc/systemd/system', unit)) for unit in UNITS]])
    for name in ('bot-token', 'owner.json', 'peer-key'):
        path = CONFIG/name
        if path.is_symlink() or path.stat().st_mode & 0o077:
            raise ValueError('Monitor credentials must be private')
    identity = json.loads((CONFIG/'owner.json').read_text())
    if not (identity.get('owner_start_verified') or identity.get('owner_contact_verified')):
        raise ValueError('Owner private chat is not verified')
    run(['systemctl', 'daemon-reload'])
    print('monitor_preflight=passed protected_processes=unchanged')


def rollback():
    config = json.loads((CONFIG/'installation.json').read_text())
    for unit in ('kudavpn-monitor.service', 'kudavpn-monitor-collect.timer'):
        subprocess.run(['systemctl', 'disable', '--now', unit], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.run(['systemctl', 'stop', 'kudavpn-monitor-collect.service'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    added = Path(config['backup'])/'added-key.json'
    if added.exists():
        line = json.loads(added.read_text())['line']
        authorized = Path('/root/.ssh/authorized_keys')
        if authorized.is_symlink():
            raise ValueError('Refusing unexpected symlink')
        contents = authorized.read_text().splitlines(keepends=True)
        contents = [entry for entry in contents if entry.rstrip('\r\n') != line]
        temporary = authorized.with_name('.monitor-key-rollback')
        fd = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'w') as file:
            file.write(''.join(contents))
        os.replace(temporary, authorized)
    # Retain private backups, receipts and code for investigation. No app/DB
    # rollback and no deletion of any unrelated authorized key or service.
    print('monitor_rollback=disabled_only_monitor')


def main():
    if os.geteuid() != 0:
        raise ValueError('Root operator only')
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest='action', required=True)
    setup_args = sub.add_parser('prepare')
    setup_args.add_argument('--host', choices=tuple(PEERS), required=True)
    setup_args.add_argument('--revision', required=True)
    setup_args.add_argument('--source', type=Path, required=True)
    auth = sub.add_parser('authorize')
    auth.add_argument('--public-key', required=True)
    sub.add_parser('verify')
    sub.add_parser('rollback')
    args = parser.parse_args()
    if args.action == 'prepare':
        if not re.fullmatch(r'[0-9a-f]{40}', args.revision):
            raise ValueError('Full reviewed commit required')
        setup(args.host, args.revision, args.source)
    elif args.action == 'authorize':
        authorize(args.public_key)
    elif args.action == 'verify':
        verify()
    else:
        rollback()


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print('monitor_provision=failed category='+type(error).__name__)
        raise SystemExit(1) from None
