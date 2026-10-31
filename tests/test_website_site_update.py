"""Website-only rollout/rollback contracts; never call systemctl or a database."""
import importlib.util
import json
from pathlib import Path

import pytest


def module():
    spec=importlib.util.spec_from_file_location('site_update',Path('deploy/website-review/update_site.py'))
    result=importlib.util.module_from_spec(spec);spec.loader.exec_module(result)
    return result


@pytest.mark.parametrize('revision',['HEAD','../outside','a'*39,'G'*40])
def test_revision_must_be_exact_immutable_sha(revision):
    with pytest.raises(ValueError):module().paths(revision)


def test_readiness_retries_503_and_exceptions_until_really_ready():
    app=module();now=[0];results=[False,OSError('warming'),False,True]
    def probe():
        value=results.pop(0)
        if isinstance(value,Exception):raise value
        return value
    app.wait_ready(clock=lambda:now[0],sleep=lambda n:now.__setitem__(0,now[0]+n),probe=probe)
    assert now[0]==6 and results==[]


def test_readiness_deadline_does_not_accept_false():
    app=module();now=[0]
    with pytest.raises(RuntimeError,match='deadline'):
        app.wait_ready(clock=lambda:now[0],sleep=lambda n:now.__setitem__(0,now[0]+n),probe=lambda:False)
    assert now[0]==90


@pytest.mark.parametrize('kind,active,pid,accepted',[
    ('timer',True,None,True),
    ('timer',False,None,False),
    ('service',True,'123',True),
    ('service',True,None,False),
    ('service',True,'0',False),
])
def test_baseline_distinguishes_timer_from_service_process(tmp_path,monkeypatch,kind,active,pid,accepted):
    app=module();unit='isolated.'+kind
    fragment=tmp_path/unit;fragment.write_text('[Unit]\nDescription=fixture\n')
    monkeypatch.setattr(app,'EXTRA',(unit,))
    raw='ActiveState='+('active' if active else 'inactive')+'\nFragmentPath='+str(fragment)+'\nDropInPaths=\n'
    if pid is not None:raw+='MainPID='+pid+'\n'
    monkeypatch.setattr(app,'run',lambda *args,**kwargs:raw)
    if accepted:
        result=app.extras()[unit]
        assert result['pid']==(pid or '0') and result['state']=='active'
        assert str(fragment) in result['files']
    else:
        with pytest.raises(ValueError):app.extras()


@pytest.mark.parametrize('existed',[False,True])
def test_rollback_restores_exact_override_only(tmp_path,monkeypatch,existed):
    app=module();backup=tmp_path/'backup';backup.mkdir()
    dropin=tmp_path/'dropins/release.conf';dropin.parent.mkdir();dropin.write_text('new')
    other=dropin.parent/'security.conf';other.write_text('keep')
    (backup/'before.json').write_text(json.dumps({'dropin_existed':existed}))
    if existed:(backup/'previous-dropin').write_text('[Service]\nWorkingDirectory=/old\n')
    monkeypatch.setattr(app,'DROPIN',dropin)
    app.restore_dropin(backup)
    assert (dropin.read_text()=='[Service]\nWorkingDirectory=/old\n') if existed else not dropin.exists()
    assert other.read_text()=='keep'


@pytest.mark.parametrize('failure',[False,True])
def test_activate_restarts_only_website_and_preserves_shared_pointer(tmp_path,monkeypatch,failure):
    app=module();revision='a'*40
    monkeypatch.setattr(app,'BASE',tmp_path/'runtime');app.BASE.mkdir()
    monkeypatch.setattr(app,'BACKUPS',tmp_path/'backups');app.BACKUPS.mkdir()
    monkeypatch.setattr(app,'DROPIN',tmp_path/'units/site.d/90-review-release.conf')
    app.DROPIN.parent.parent.mkdir()
    old=app.BASE/'release-old';(old/'bot').mkdir(parents=True);(old/'bot/requirements.txt').write_text('same')
    (app.BASE/'current').symlink_to(old,target_is_directory=True)
    release,backup=app.paths(revision);(release/'bot').mkdir(parents=True);backup.mkdir()
    (release/'bot/requirements.txt').write_text('same');(release/'.kuda-revision').write_text(revision)
    (backup/'prepared').write_text('ready');(backup/'before.json').write_text(json.dumps({'shared_release':str(old)}))
    seen=[];rollbacks=[]
    monkeypatch.setattr(app,'verify',lambda *args,**kwargs:None)
    monkeypatch.setattr(app,'wait_ready',lambda:None)
    monkeypatch.setattr(app,'rollback',lambda value:rollbacks.append(value))
    def run(args,**kwargs):
        seen.append(args)
        if args[:2]==['systemctl','restart'] and failure:raise RuntimeError('failed')
        return str(release) if args[:2]==['systemctl','show'] else ''
    monkeypatch.setattr(app,'run',run)
    if failure:
        with pytest.raises(RuntimeError):app.activate(revision)
        assert rollbacks==[revision]
    else:
        app.activate(revision);assert rollbacks==[]
        assert (backup/'activated').read_text()=='ready'
    assert (app.BASE/'current').resolve()==old
    assert [args for args in seen if args[:2]==['systemctl','restart']]==[['systemctl','restart',app.SITE]]
    assert app.DROPIN.read_text()=='[Service]\nWorkingDirectory='+str(release)+'\n'
