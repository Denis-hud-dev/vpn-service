import importlib.util
import json
from pathlib import Path
import pytest
ROOT=Path(__file__).resolve().parents[1]


def module():
 s=importlib.util.spec_from_file_location('profile_update',ROOT/'deploy/telegram-pilot/update_profile.py')
 m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m


class API:
 def __init__(self,username='KudaVPN_Test_Bot'):
  self.username=username;self.calls=[];self.desc={'':'old','ru':'old_ru'};self.short={'':'old short','ru':'old short ru'};self.photo=None;self.fail=False
 def __call__(self,method,params,avatar=None):
  self.calls.append(method)
  if method=='getMe':return {'username':self.username,'id':123}
  if method=='getUserProfilePhotos':return {'photos':[[{'file_unique_id':self.photo,'file_id':'file'}]] if self.photo else []}
  if method=='getMyDescription':return {'description':self.desc[params['language_code']]}
  if method=='getMyShortDescription':return {'short_description':self.short[params['language_code']]}
  if method=='setMyDescription':self.desc[params['language_code']]=params['description'];return True
  if method=='setMyShortDescription':self.short[params['language_code']]=params['short_description'];return True
  if method=='getFile':return {'file_path':'photos/old.jpg'}
  if method=='setMyProfilePhoto':
   self.photo='previous' if avatar==b'OLD' else 'new'
   if self.fail:self.fail=False;raise TimeoutError('private URL must not be logged')
   return True
  if method=='removeMyProfilePhoto':self.photo=None;return True
  raise AssertionError(method)


def test_description_avatar_and_empty_chat_copy_are_valid():
 c=json.loads((ROOT/'deploy/telegram-pilot/profile.json').read_text())
 assert 0<len(c['description'])<=512 and 0<len(c['short_description'])<=120
 assert '199' in c['description'] and 'Happ' in c['description']
 assert '<' not in c['description'] and 'https://' not in c['description']
 assert (ROOT/c['avatar']).read_bytes()==(ROOT/'bot/subscription_api/static/kudavpn-original-logo.jpeg').read_bytes()


def test_profile_update_changes_only_test_bot_metadata_and_has_backup(tmp_path):
 m=module();api=API();c=json.loads((ROOT/'deploy/telegram-pilot/profile.json').read_text());backup=tmp_path/'private'
 result=m.apply(api,lambda p:b'old',backup,c,b'\xff\xd8\xffJPEG')
 assert result['avatar_verified'] and api.photo=='new'
 assert api.desc=={'':c['description'],'ru':c['description']}
 assert backup.stat().st_mode&0o777==0o700
 assert (backup/'before.json').stat().st_mode&0o777==0o600
 assert not any('send' in method.lower() or 'payment' in method.lower() for method in api.calls)
 m.restore(api,backup)
 assert api.desc=={'':'old','ru':'old_ru'} and api.photo is None


def test_production_identity_is_rejected_before_any_write(tmp_path):
 m=module();api=API('KudaVpnBot');c=json.loads((ROOT/'deploy/telegram-pilot/profile.json').read_text())
 with pytest.raises(m.SafeFailure,match='wrong_bot'):m.apply(api,lambda p:b'',tmp_path/'nope',c,b'\xff\xd8\xff')
 assert api.calls==['getMe'] and not (tmp_path/'nope').exists()


def test_ambiguous_avatar_timeout_restores_metadata_and_original_empty_avatar(tmp_path):
 m=module();api=API();api.fail=True;c=json.loads((ROOT/'deploy/telegram-pilot/profile.json').read_text())
 with pytest.raises(m.SafeFailure,match='rolled_back'):m.apply(api,lambda p:b'',tmp_path/'backup',c,b'\xff\xd8\xff')
 assert api.desc=={'':'old','ru':'old_ru'} and api.short['']=='old short' and api.photo is None


def test_existing_profile_photo_is_backed_up_and_restored(tmp_path):
 m=module();api=API();api.photo='previous';c=json.loads((ROOT/'deploy/telegram-pilot/profile.json').read_text());backup=tmp_path/'saved'
 m.apply(api,lambda path:b'OLD',backup,c,b'\xff\xd8\xffNEW')
 assert api.photo=='new' and (backup/'before-avatar.jpg').read_bytes()==b'OLD'
 assert (backup/'before-avatar.jpg').stat().st_mode&0o777==0o600
 m.restore(api,backup)
 assert api.photo=='previous' and api.desc['']=='old'
