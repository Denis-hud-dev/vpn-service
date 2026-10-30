"""Offline overlay/rollback guards. Never execute SSH, systemd or working databases."""
import copy
import importlib.util
import json
from pathlib import Path
import hashlib
import subprocess

import pytest

ROOT=Path(__file__).resolve().parents[1]

def load(name):
 spec=importlib.util.spec_from_file_location(name,ROOT/'deploy/branding'/f'{name}.py')
 m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m


def test_overlay_changes_only_visual_handlers_and_no_business_routes(tmp_path):
 # A shallow CI checkout intentionally has no historical deployed runtimes.
 if subprocess.run(['git','cat-file','-e','0d45896:bot/subscription_api/website.py'],cwd=ROOT,capture_output=True).returncode:
  pytest.skip('Pinned historical TEST source requires the existing full local Git history')
 builder=load('build_overlay');manifest=builder.build(tmp_path/'payload')
 website=(tmp_path/'payload/website/bot/subscription_api/website.py').read_text()
 assert 'BRAND_IMAGES' in website
 assert "'/auth/assets/favicon.ico'" not in website
 assert manifest['stages']['bot']['restart'] is False
 assert set(manifest['stages']['bot']['files'])=={'bot/assets/welcome-premium.png'}
 assert not any('website.js' in p or 'payments' in p for s in manifest['stages'].values() for p in s['files'])
 old=builder.blob('0d45896','bot/subscription_api/static/website.html').decode()
 new=(tmp_path/'payload/website/bot/subscription_api/static/website.html').read_text()
 for important in ('id="plan-cards"','id="order-panel"','id="provider-link"','id="happ-link"'):
  assert new.count(important)==old.count(important)
 for name in ('login','callback','identity','create','refresh','connection'):
  assert 'async def '+name+'(' in website


def test_build_never_overwrites_existing_output(tmp_path):
 builder=load('build_overlay');folder=tmp_path/'existing';folder.mkdir();(folder/'keep').write_text('keep')
 with pytest.raises(FileExistsError):builder.build(folder)
 assert (folder/'keep').read_text()=='keep'


def state():
 return {'units':{'site':{'MainPID':'1','FragmentPath':'/site','DropInPaths':'','User':'same'},'xray':{'MainPID':'2','FragmentPath':'/vpn','DropInPaths':''}},'files':{'/site':'a','/vpn':'b','/secret-config':'c'},'listeners':[[':20443','1'],[':443','2']]}


def test_compare_allows_only_target_process_change():
 m=load('rollout');old=state();new=copy.deepcopy(old)
 new['units']['site']['MainPID']='3';new['listeners'][0][1]='3';new['units']['site']['DropInPaths']='/new';new['files']['/new']='new'
 m.compare(old,new,'site')
 new['units']['xray']['MainPID']='4'
 with pytest.raises(ValueError,match='protected_state_changed'):m.compare(old,new,'site')


def test_compare_keeps_configuration_secrets_and_ports_protected():
 m=load('rollout');old=state();new=copy.deepcopy(old);new['files']['/secret-config']='changed'
 with pytest.raises(ValueError):m.compare(old,new,'site')
 new=copy.deepcopy(old);new['listeners'][1][0]=':8443'
 with pytest.raises(ValueError):m.compare(old,new,'site')


def test_validate_refuses_runtime_drift_and_path_escape(tmp_path,monkeypatch):
 m=load('rollout');old=tmp_path/'old';payload=tmp_path/'payload';old.mkdir();payload.mkdir()
 (old/'image.png').write_bytes(b'old');(payload/'image.png').write_bytes(b'new')
 stage={'unit':'site','old':str(old),'files':{'image.png':{'before':m.sha(old/'image.png'),'after':m.sha(payload/'image.png')}}}
 monkeypatch.setattr(m,'unit',lambda _:dict(WorkingDirectory=str(old)))
 m.validate_files(stage,payload)
 (old/'image.png').write_bytes(b'unreviewed')
 with pytest.raises(ValueError,match='runtime_drift'):m.validate_files(stage,payload)
 stage['files']={'../escape':{}}
 with pytest.raises(ValueError,match='unsafe_path'):m.validate_files(stage,payload)


def test_rollback_restarts_only_target_and_preserves_old_override(tmp_path,monkeypatch):
 m=load('rollout');m.BASE=tmp_path/'backup';folder=m.BASE/'website';folder.mkdir(parents=True)
 (folder/'before.json').write_text('{}');old=tmp_path/'old';old.mkdir();(old/'logo').write_bytes(b'old')
 stage={'unit':'site','old':str(old),'restart':True,'files':{'logo':{'before':m.sha(old/'logo')}}}
 dropin=tmp_path/'99-approved-branding.conf';dropin.write_text('[Service]\nWorkingDirectory='+str(old.with_name('brand-ca0d10ece2f3-website'))+'\n')
 previous=tmp_path/'90-review-release.conf';previous.write_text('keep')
 calls=[];monkeypatch.setattr(m,'dropin_for',lambda _:dropin);monkeypatch.setattr(m,'run',lambda args,**kw:calls.append(args));monkeypatch.setattr(m,'wait_ready',lambda _:None);monkeypatch.setattr(m,'unit',lambda _:dict(WorkingDirectory=str(old)))
 m.rollback('website',{'stages':{'website':stage}})
 assert previous.read_text()=='keep' and not dropin.exists()
 assert [a for a in calls if a[:2]==['systemctl','restart']]==[['systemctl','restart','site']]


def test_failed_inplace_banner_update_restores_original(tmp_path,monkeypatch):
 m=load('rollout');m.BASE=tmp_path/'backups';old=tmp_path/'old';old.mkdir();(old/'banner').write_bytes(b'old')
 payload=tmp_path/'payload';(payload/'bot').mkdir(parents=True);(payload/'bot/banner').write_bytes(b'new')
 stage={'unit':'testbot','old':str(old),'restart':False,'files':{'banner':{'before':m.sha(old/'banner'),'after':m.sha(payload/'bot/banner')}}}
 monkeypatch.setattr(m,'unit',lambda _:dict(WorkingDirectory=str(old),User='same'))
 monkeypatch.setattr(m,'snapshot',lambda:{'units':{'testbot':{'FragmentPath':'/fixture','DropInPaths':'','User':'same'}}})
 monkeypatch.setattr(m,'data_digest',lambda:'unchanged');monkeypatch.setattr(m,'encrypted_backup',lambda *a:None);monkeypatch.setattr(m,'run',lambda *a,**kw:'')
 calls=[0]
 def ready(_):
  calls[0]+=1
  if calls[0]>1:raise ValueError('simulated_failure')
 monkeypatch.setattr(m,'wait_ready',ready)
 with pytest.raises(ValueError,match='simulated_failure'):m.apply('bot',{'stages':{'bot':stage}},payload)
 assert (old/'banner').read_bytes()==b'old'
