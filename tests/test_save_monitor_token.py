"""Credential entry regressions. All tokens and Telegram calls are fake."""
import getpass
import json
import os
from pathlib import Path
import urllib.error
import warnings

import pytest

from scripts import save_monitor_token as saver

BOT_ID=55555555
TOKEN=str(BOT_ID)+':'+('x'*30)
OWNER=501


@pytest.fixture
def prepared(tmp_path,monkeypatch):
    folder=tmp_path/'private'
    folder.mkdir(mode=0o700)
    identity=folder/'owner-config.json'
    identity.write_text(json.dumps({'owner_id':OWNER,'application_bot_ids':[7001]}))
    identity.chmod(0o600)
    monkeypatch.setattr(saver.sys,'argv',['save-monitor-token.py','--private-dir',str(folder)])
    monkeypatch.setattr(saver.sys.stdin,'isatty',lambda:True)
    monkeypatch.setattr(saver.getpass,'getpass',lambda prompt:TOKEN)
    me={'id':BOT_ID,'username':'KudaVPN_Monitor_Bot','first_name':'KudaVPN Monitor','is_bot':True}
    updates=[{'update_id':10,'message':{'chat':{'id':OWNER,'type':'private'},'from':{'id':OWNER},'text':'/start'}}]
    calls=[]
    def fake_api(token,method,payload):
        assert token==TOKEN
        calls.append((method,payload))
        return me if method=='getMe' else updates
    monkeypatch.setattr(saver,'telegram',fake_api)
    return folder,me,updates,calls


def test_hidden_input_saves_private_files_and_confirms_only_owner_start(prepared,capsys):
    folder,_,_,calls=prepared
    saver.main()
    assert (folder/'bot-token').read_text().strip()==TOKEN
    config=json.loads((folder/'owner-config.json').read_text())
    assert config['owner_start_verified'] and config['initial_update_offset']==11
    assert config['bot_id']==BOT_ID
    assert all((folder/name).stat().st_mode & 0o777==0o600 for name in ['bot-token','owner-config.json'])
    assert folder.stat().st_mode & 0o777==0o700
    output=capsys.readouterr()
    assert TOKEN not in output.out+output.err and 'api.telegram.org/bot' not in output.out+output.err
    assert [method for method,_ in calls]==['getMe','getUpdates']


@pytest.mark.parametrize('change', ['foreign_sender','foreign_chat','group','no_start','empty'])
def test_other_updates_do_not_confirm_owner_start(prepared,change):
    folder,_,updates,_=prepared
    msg=updates[0]['message']
    if change=='foreign_sender': msg['from']['id']=777
    if change=='foreign_chat': msg['chat']['id']=777
    if change=='group': msg['chat']['type']='group'
    if change=='no_start': msg['text']='/status'
    if change=='empty': updates.clear()
    saver.main()
    assert not json.loads((folder/'owner-config.json').read_text())['owner_start_verified']


@pytest.mark.parametrize('kind',['wrong_bot','application_bot','mismatched_token'])
def test_wrong_bot_is_refused_without_overwriting_existing_token(prepared,kind):
    folder,me,_,_=prepared
    (folder/'bot-token').write_text('old-placeholder')
    (folder/'bot-token').chmod(0o600)
    if kind=='wrong_bot': me.update(username='KudaVpnBot',first_name='KudaVPN')
    if kind=='application_bot': me['id']=7001
    if kind=='mismatched_token': me['id']=BOT_ID+1
    with pytest.raises(SystemExit): saver.main()
    assert (folder/'bot-token').read_text()=='old-placeholder'


def test_non_tty_cannot_fall_back_to_visible_input(prepared,monkeypatch):
    folder,*_=prepared
    monkeypatch.setattr(saver.sys.stdin,'isatty',lambda:False)
    monkeypatch.setattr(saver.getpass,'getpass',lambda _:pytest.fail('must not read visibly'))
    with pytest.raises(SystemExit,match='Terminal'): saver.main()
    assert not (folder/'bot-token').exists()


def test_getpass_warning_aborts_before_plaintext_fallback(prepared,monkeypatch):
    folder,*_=prepared
    def unavailable(_):
        warnings.warn('echo fallback',getpass.GetPassWarning)
        pytest.fail('must not continue to visible fallback')
    monkeypatch.setattr(saver.getpass,'getpass',unavailable)
    with pytest.raises(SystemExit,match='Скрытый ввод'): saver.main()
    assert not (folder/'bot-token').exists()


def test_owner_config_permissions_are_required(prepared):
    folder,*_=prepared
    (folder/'owner-config.json').chmod(0o644)
    with pytest.raises(SystemExit,match='0600'): saver.main()
    assert not (folder/'bot-token').exists()


def test_git_directory_cannot_receive_secret(prepared):
    folder,*_=prepared
    (folder.parent/'.git').mkdir()
    with pytest.raises(SystemExit,match='вне Git'): saver.main()
    assert not (folder/'bot-token').exists()


def test_symlink_target_is_never_written(prepared,tmp_path):
    folder,*_=prepared
    target=tmp_path/'unrelated'
    target.write_text('preserve')
    (folder/'bot-token').symlink_to(target)
    with pytest.raises(SystemExit,match='Символические'): saver.main()
    assert target.read_text()=='preserve'


def test_atomic_write_failure_preserves_previous_secret_and_removes_temp(prepared,monkeypatch):
    folder,*_=prepared
    target=folder/'bot-token'
    target.write_text('previous-placeholder')
    target.chmod(0o600)
    def fail_replace(source,destination):
        assert Path(source).stat().st_mode & 0o777==0o600
        raise OSError('simulated disk error '+TOKEN)
    monkeypatch.setattr(saver.os,'replace',fail_replace)
    with pytest.raises(SystemExit) as error: saver.main()
    assert TOKEN not in str(error.value)
    assert target.read_text()=='previous-placeholder'
    assert not list(folder.glob('.monitor-save-*'))


def test_api_exception_never_exposes_token_or_url(monkeypatch):
    def unavailable(*args,**kwargs):
        raise urllib.error.URLError('https://api.telegram.org/bot'+TOKEN+'/getMe')
    monkeypatch.setattr(saver.urllib.request,'urlopen',unavailable)
    with pytest.raises(ValueError) as error: saver.telegram(TOKEN,'getMe',{})
    assert TOKEN not in str(error.value) and 'api.telegram.org/bot' not in str(error.value)
    assert error.value.__suppress_context__
