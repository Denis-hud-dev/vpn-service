"""Offline credential handling; every input is a fixture."""
import getpass
import json
from pathlib import Path
import warnings
import pytest
from scripts import save_website_oidc as saver

SECRET='offline_oidc_fixture_secret'


def test_save_atomic_private_and_no_overwrite(tmp_path):
    folder=tmp_path/'private'
    saver.save(folder,'123456',SECRET)
    target=folder/'oidc.json'
    assert target.stat().st_mode & 0o777==0o600
    assert folder.stat().st_mode & 0o777==0o700
    assert json.loads(target.read_text())['client_secret']==SECRET
    with pytest.raises(ValueError):saver.save(folder,'123456','other_fixture_secret')
    assert json.loads(target.read_text())['client_secret']==SECRET
    assert not list(folder.glob('.oidc-*'))


@pytest.mark.parametrize('fault',['git','symlink','permissions'])
def test_unsafe_destination_rejected(tmp_path,fault):
    folder=tmp_path/'private';folder.mkdir(mode=0o700)
    if fault=='git':(tmp_path/'.git').mkdir()
    if fault=='symlink':
        source=tmp_path/'actual';source.mkdir(mode=0o700);folder.rmdir();folder.symlink_to(source)
    if fault=='permissions':folder.chmod(0o755)
    with pytest.raises(ValueError):saver.save(folder,'123456',SECRET)
    assert not (folder/'oidc.json').exists()


def test_main_hidden_and_never_prints_secret(tmp_path,monkeypatch,capsys):
    monkeypatch.setattr(saver,'TARGET',tmp_path/'private')
    monkeypatch.setattr(saver.sys.stdin,'isatty',lambda:True)
    monkeypatch.setattr(saver.sys.stderr,'isatty',lambda:True)
    values=iter(['123456',SECRET])
    monkeypatch.setattr(saver.getpass,'getpass',lambda _:next(values))
    saver.main()
    output=capsys.readouterr()
    assert SECRET not in output.out+output.err


@pytest.mark.parametrize('kind',['non_tty','echo_warning','write_error'])
def test_failures_do_not_echo_or_leak(tmp_path,monkeypatch,kind,capsys):
    monkeypatch.setattr(saver,'TARGET',tmp_path/'private')
    monkeypatch.setattr(saver.sys.stdin,'isatty',lambda:kind!='non_tty')
    monkeypatch.setattr(saver.sys.stderr,'isatty',lambda:True)
    values=iter(['123456',SECRET])
    def hidden(_):
        if kind=='echo_warning':warnings.warn('echo',getpass.GetPassWarning)
        return next(values)
    monkeypatch.setattr(saver.getpass,'getpass',hidden)
    if kind=='write_error':
        def fail(*args):raise OSError(SECRET)
        monkeypatch.setattr(saver.os,'link',fail)
    with pytest.raises(SystemExit) as error:saver.main()
    output=capsys.readouterr()
    assert SECRET not in str(error.value)+output.out+output.err
    assert not (tmp_path/'private'/'oidc.json').exists()
