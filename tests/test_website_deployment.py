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
