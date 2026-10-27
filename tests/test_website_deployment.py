"""Deployment regression: restrictive umask must not block unprivileged runtime."""
import importlib.util
import os
from pathlib import Path
import stat

import pytest


def module():
    spec=importlib.util.spec_from_file_location('review_install',Path('deploy/website-review/install.py'))
    result=importlib.util.module_from_spec(spec);spec.loader.exec_module(result)
    return result


def test_runtime_permission_repair_preserves_private_bundle_and_external_targets(tmp_path,monkeypatch):
    install=module();monkeypatch.setattr(install,'BASE',tmp_path)
    old=os.umask(0o077)
    try:
        release=tmp_path/'release-fixture';release.mkdir(mode=0o755)
        sub=release/'bot';sub.mkdir();(sub/'main.py').write_text('pass')
        private=tmp_path/'oidc-upload.json';private.write_text('private fixture');private.chmod(0o600)
        outside=tmp_path/'not-runtime';outside.mkdir(mode=0o700)
        (release/'external').symlink_to(outside,target_is_directory=True)
    finally:os.umask(old)
    assert stat.S_IMODE(release.stat().st_mode)==0o700
    install.public_runtime(release)
    assert stat.S_IMODE(release.stat().st_mode)==0o755
    assert stat.S_IMODE(sub.stat().st_mode)==0o755
    assert stat.S_IMODE((sub/'main.py').stat().st_mode)==0o644
    assert stat.S_IMODE(private.stat().st_mode)==0o600
    assert stat.S_IMODE(outside.stat().st_mode)==0o700
    with pytest.raises(ValueError):install.public_runtime(outside)
    with pytest.raises(ValueError):install.public_runtime(private)


def test_runtime_credential_requires_exact_root_owned_readonly_systemd_mount(monkeypatch):
    from types import SimpleNamespace
    import bot.subscription_api.website_main as app
    directory='/run/credentials/kudavpn-website-review.service'
    path=Path(directory)/'oidc.json'
    monkeypatch.setenv('CREDENTIALS_DIRECTORY',directory)
    monkeypatch.setattr(app.os,'getegid',lambda:123)
    monkeypatch.setattr(app.os,'statvfs',lambda _:SimpleNamespace(f_flag=os.ST_RDONLY))
    info=SimpleNamespace(st_mode=0o440,st_uid=0,st_gid=123)
    parent=SimpleNamespace(st_mode=0o550,st_uid=0,st_gid=123)
    assert app.sealed_systemd_credential(path,info,parent)
    assert not app.sealed_systemd_credential(Path('/tmp/oidc.json'),info,parent)
    assert not app.sealed_systemd_credential(Path(directory)/'unregistered.json',info,parent)
    info.st_gid=124
    assert not app.sealed_systemd_credential(path,info,parent)
    info.st_gid=123;info.st_mode=0o444
    assert not app.sealed_systemd_credential(path,info,parent)
    info.st_mode=0o440;info.st_uid=1000
    assert not app.sealed_systemd_credential(path,info,parent)
    info.st_uid=0
    monkeypatch.setattr(app.os,'statvfs',lambda _:SimpleNamespace(f_flag=0))
    assert not app.sealed_systemd_credential(path,info,parent)


def test_private_input_outside_systemd_still_requires_private_file_and_directory(tmp_path,monkeypatch):
    from bot.subscription_api.website_main import private_file
    folder=tmp_path/'credentials';folder.mkdir(mode=0o700)
    path=folder/'oidc.json';path.write_text('offline fixture');path.chmod(0o600)
    monkeypatch.setenv('SITE_OIDC_BUNDLE_FILE',str(path))
    assert private_file('SITE_OIDC_BUNDLE_FILE')=='offline fixture'
    path.chmod(0o440)
    with pytest.raises(ValueError):private_file('SITE_OIDC_BUNDLE_FILE')


def test_sealed_root_group_credentials_require_exclusive_service_uid_acl(monkeypatch):
    import struct
    from types import SimpleNamespace
    import bot.subscription_api.website_main as app
    directory=Path('/run/credentials/kudavpn-website-review.service')
    path=directory/'oidc.json'
    monkeypatch.setenv('CREDENTIALS_DIRECTORY',str(directory))
    monkeypatch.setattr(app.os,'geteuid',lambda:501)
    monkeypatch.setattr(app.os,'getegid',lambda:123)
    monkeypatch.setattr(app.os,'statvfs',lambda _:SimpleNamespace(f_flag=os.ST_RDONLY))
    foreign=False
    def acl(p,name):
        permission=5 if p==directory else 4
        entries=[(1,permission,0xffffffff),(2,permission,502 if foreign else 501),
                 (4,0,0xffffffff),(16,permission,0xffffffff),(32,0,0xffffffff)]
        return struct.pack('<I',2)+b''.join(struct.pack('<HHI',*row) for row in entries)
    monkeypatch.setattr(app.os,'getxattr',acl,raising=False)
    info=SimpleNamespace(st_mode=0o440,st_uid=0,st_gid=0)
    parent=SimpleNamespace(st_mode=0o550,st_uid=0,st_gid=0)
    assert app.sealed_systemd_credential(path,info,parent)
    foreign=True
    assert not app.sealed_systemd_credential(path,info,parent)
    foreign=False
    monkeypatch.setattr(app.os,'getxattr',lambda *args:struct.pack('<I',2))
    assert not app.sealed_systemd_credential(path,info,parent)
