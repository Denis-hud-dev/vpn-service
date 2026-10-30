"""TEST-bot public profile only. No messages, users, payments or server settings."""
import json
import hashlib
import os
from pathlib import Path
import secrets
import subprocess
import sys
from urllib.parse import urlsplit
import urllib.request

EXPECTED='KudaVPN_Test_Bot'


class SafeFailure(Exception):pass


def write_private(path,data):
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'wb') as stream:stream.write(data)


def assert_test(api):
    me=api('getMe',{})
    if me.get('username')!=EXPECTED:raise SafeFailure('wrong_bot')
    return me


def restore(api,backup):
    assert_test(api)
    old=json.loads((backup/'before.json').read_text())
    for entry in old['settings']:
        api('setMyDescription',{'language_code':entry['language_code'],'description':entry['description']})
        api('setMyShortDescription',{'language_code':entry['language_code'],'short_description':entry['short_description']})
    current=api('getUserProfilePhotos',{'user_id':assert_test(api)['id'],'limit':1})['photos']
    current_id=current[0][-1]['file_unique_id'] if current else None
    if current_id!=old.get('photo_unique_id'):
        if old['has_photo']:api('setMyProfilePhoto',{},(backup/'before-avatar.jpg').read_bytes())
        else:api('removeMyProfilePhoto',{})


def apply(api,download,backup,config,avatar):
    me=assert_test(api)
    if config['username']!=EXPECTED or config['languages']!=['','ru']:
        raise SafeFailure('invalid_target')
    if not 0<len(config['description'])<=512 or not 0<len(config['short_description'])<=120:
        raise SafeFailure('invalid_lengths')
    if not avatar.startswith(b'\xff\xd8\xff'):raise SafeFailure('jpeg_required')
    old={'settings':[]}
    for language in config['languages']:
        old['settings'].append({'language_code':language,
            'description':api('getMyDescription',{'language_code':language})['description'],
            'short_description':api('getMyShortDescription',{'language_code':language})['short_description']})
    photos=api('getUserProfilePhotos',{'user_id':me['id'],'limit':1})
    old['has_photo']=bool(photos['photos'])
    old['photo_unique_id']=photos['photos'][0][-1]['file_unique_id'] if old['has_photo'] else None
    original=None
    if old['has_photo']:
        file=api('getFile',{'file_id':photos['photos'][0][-1]['file_id']})
        original=download(file['file_path'])
    backup.mkdir(mode=0o700,parents=True,exist_ok=False)
    write_private(backup/'before.json',json.dumps(old).encode())
    if original is not None:write_private(backup/'before-avatar.jpg',original)
    try:
        for language in config['languages']:
            api('setMyDescription',{'language_code':language,'description':config['description']})
            api('setMyShortDescription',{'language_code':language,'short_description':config['short_description']})
        assert api('setMyProfilePhoto',{},avatar) is True
        for language in config['languages']:
            assert api('getMyDescription',{'language_code':language})['description']==config['description']
            assert api('getMyShortDescription',{'language_code':language})['short_description']==config['short_description']
        assert api('getUserProfilePhotos',{'user_id':me['id'],'limit':1})['photos']
        result={'bot':EXPECTED,'description_verified':True,'short_description_verified':True,'avatar_verified':True,'languages':['default','ru']}
        write_private(backup/'verified.json',json.dumps(result).encode())
        return result
    except Exception:
        try:restore(api,backup)
        except Exception:raise SafeFailure('profile_update_and_rollback_failed') from None
        raise SafeFailure('profile_update_failed_rolled_back') from None


class TelegramAPI:
    def __init__(self,token):self.token=token

    def __call__(self,method,params,avatar=None):
        headers={}
        if avatar is None:
            body=json.dumps(params).encode();headers['Content-Type']='application/json'
        else:
            boundary='kuda'+secrets.token_hex(16)
            field=json.dumps({'type':'static','photo':'attach://avatar'})
            body=('--'+boundary+'\r\nContent-Disposition: form-data; name="photo"\r\n\r\n'+field+'\r\n--'+boundary+'\r\nContent-Disposition: form-data; name="avatar"; filename="avatar.jpg"\r\nContent-Type: image/jpeg\r\n\r\n').encode()+avatar+('\r\n--'+boundary+'--\r\n').encode()
            headers['Content-Type']='multipart/form-data; boundary='+boundary
        req=urllib.request.Request('https://api.telegram.org/bot'+self.token+'/'+method,data=body,headers=headers)
        with urllib.request.urlopen(req,timeout=25) as response:r=json.load(response)
        if not r.get('ok'):raise SafeFailure('telegram_rejected')
        return r['result']

    def download(self,path):
        if not isinstance(path,str) or path.startswith('/') or '..' in Path(path).parts:raise SafeFailure('invalid_file_path')
        with urllib.request.urlopen('https://api.telegram.org/file/bot'+self.token+'/'+path,timeout=25) as response:
            raw=response.read(10*1024*1024+1)
        if len(raw)>10*1024*1024:raise SafeFailure('file_too_large')
        return raw


def main(mode,revision):
    if os.geteuid()!=0 or len(revision)!=40 or any(c not in '0123456789abcdef' for c in revision):
        raise SafeFailure('immutable_revision_required')
    source=Path('/opt/kudavpn-website-review/site-release-'+revision[:12])
    if (source/'.kuda-revision').read_text().strip()!=revision:raise SafeFailure('unknown_source')
    pid=subprocess.check_output(['systemctl','show','-P','MainPID','kudavpn-telegram-pilot-bot.service'],text=True).strip()
    env=dict(v.split('=',1) for v in Path('/proc/'+pid+'/environ').read_bytes().decode().split('\0') if '=' in v)
    database=urlsplit(env['DATABASE_URL'])
    if database.hostname!='127.0.0.1' or database.path!='/kudavpn_telegram_pilot' or env.get('PILOT_WELCOME_ENABLED')!='1':
        raise SafeFailure('isolated_pilot_required')
    api=TelegramAPI(env['BOT_TOKEN']);backup=Path('/var/backups/kudavpn-telegram-pilot/profile-'+revision[:12])
    if mode=='rollback':restore(api,backup);print('test_bot_profile=restored');return
    if mode!='apply':raise SafeFailure('invalid_mode')
    config=json.loads((source/'deploy/telegram-pilot/profile.json').read_text())
    avatar=(source/config['avatar']).read_bytes()
    manifest=json.loads((source/'bot/assets/brand/manifest.json').read_text())
    assert config['avatar']=='bot/assets/brand/avatar-1024.jpg'
    assert hashlib.sha256(avatar).hexdigest()==manifest['products']['avatar-1024.jpg']['sha256']
    print(json.dumps(apply(api,api.download,backup,config,avatar)))


if __name__=='__main__':
    try:
        os.umask(0o077)
        if len(sys.argv)!=3:raise SafeFailure('mode_and_revision_required')
        main(sys.argv[1],sys.argv[2])
    except Exception as exc:
        reason=str(exc) if isinstance(exc,SafeFailure) else type(exc).__name__
        raise SystemExit('TEST profile operation stopped: '+reason+'; credentials withheld') from None
