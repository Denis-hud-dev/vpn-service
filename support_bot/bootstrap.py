"""Owner-run credential/ID preparation, NOT a support bot process.

Read-only Telegram methods, one short getUpdates request without offset or
allowed_updates. Never dispatches, sends, acknowledges, deletes or clears data.
"""
import argparse
from dataclasses import dataclass, field
from getpass import getpass, GetPassWarning
import json
import os
from pathlib import Path
import re
import secrets
import ssl
import sys
import time
import urllib.request
import warnings

from support_bot.config import Config

DEFAULT_FOLDER = Path.home()/'Documents/KudaVPN-private/support'
DEFAULT_OWNER = Path.home()/'Documents/KudaVPN-private/monitor/owner-config.json'
METHODS = frozenset({'getMe','getWebhookInfo','getUpdates','getChat','getChatMember'})


class Refused(ValueError):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


@dataclass
class ReadOnlyTelegram:
    token: str = field(repr=False)

    def call(self, method, payload):
        if method not in METHODS:
            raise Refused('method_not_allowed')
        if method == 'getUpdates' and payload != {'timeout':0,'limit':100}:
            raise Refused('update_acknowledgement_forbidden')
        try:
            request = urllib.request.Request('https://api.telegram.org/bot'+self.token+'/'+method,
                data=json.dumps(payload).encode(),headers={'Content-Type':'application/json'})
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect(),
                urllib.request.HTTPSHandler(context=ssl.create_default_context()))
            with opener.open(request,timeout=15) as response:
                body = response.read(2*1024*1024+1)
            if len(body)>2*1024*1024:
                raise Refused('response_too_large')
            data = json.loads(body)
            if data.get('ok') is not True:
                raise Refused('telegram_request_failed')
            return data['result']
        except Refused:
            raise
        except Exception:
            # Never propagate URL-bearing HTTP/connection/JSON errors.
            raise Refused('telegram_request_failed') from None


def private_folder(path):
    if not path.is_absolute() or path.is_symlink():
        raise Refused('private_folder_required')
    resolved = path.resolve()
    if any((p/'.git').exists() for p in [resolved,*resolved.parents]):
        raise Refused('git_directory_forbidden')
    path.mkdir(parents=True,exist_ok=True,mode=0o700)
    if path.stat().st_mode & 0o077 or path.stat().st_uid != os.getuid():
        raise Refused('private_folder_permissions')
    return path


def read_private(path):
    if path.is_symlink() or not path.is_file() or path.stat().st_mode & 0o077 or path.stat().st_uid != os.getuid():
        raise Refused('private_file_permissions')
    return path.read_text()


def save_private(path, text):
    fd = os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'w') as file:
        file.write(text)
        file.flush()
        os.fsync(file.fileno())


def trusted_owner(path):
    value = json.loads(read_private(path))
    owner = value.get('owner_id')
    if (type(owner) is not int or owner<=0
            or not (value.get('owner_start_verified') is True or value.get('owner_contact_verified') is True)):
        raise Refused('verified_owner_required')
    return owner


def values(token, username, owner, group):
    result = {'SUPPORT_BOT_TOKEN':token,'SUPPORT_BOT_USERNAME':username,
              'SUPPORT_ADMIN_CHAT_ID':str(group),'SUPPORT_OPERATOR_IDS':str(owner),
              'SUPPORT_DB_PATH':'/var/lib/kudavpn-support/support.sqlite3',
              'SUPPORT_CUSTOM_EMOJI':'0','SUPPORT_TEST_USER_IDS':str(owner)}
    Config.from_env(result)
    return result


def identity(api, expected):
    me = api.call('getMe',{})
    if ((me.get('username') or '').casefold()!=expected.casefold()
            or type(me.get('id')) is not int or me['id']<=0 or me.get('is_bot') is not True):
        raise Refused('unexpected_support_bot')
    if api.call('getWebhookInfo',{}).get('url'):
        raise Refused('existing_webhook_requires_review')
    return me


def prepare(folder, owner_file, username, token, *, api_factory=ReadOnlyTelegram, now=time.time):
    folder = private_folder(folder)
    if any((folder/name).exists() for name in ('bot-token','bootstrap.json','support.env')):
        raise Refused('existing_support_configuration_not_overwritten')
    owner = trusted_owner(owner_file)
    username = username.strip().lstrip('@')
    values(token,username,owner,-1)  # Validate without any real group guess.
    api = api_factory(token)
    me = identity(api,username)
    state = {'bot_id':me['id'],'username':username,'owner_id':owner,
             'challenge':'/support_setup_'+secrets.token_hex(6), 'created_at':int(now()),
             'owner_source':'verified_private_monitor_record'}
    save_private(folder/'bot-token',token+'\n')
    save_private(folder/'bootstrap.json',json.dumps(state)+'\n')
    return state['challenge']


def matching_messages(updates, state):
    private = []
    groups = {}
    for update in updates:
        message = update.get('message') or {}
        author = message.get('from') or {}
        chat = message.get('chat') or {}
        if (author.get('id') != state['owner_id'] or author.get('is_bot') is not False
                or message.get('sender_chat') or message.get('forward_origin') or message.get('via_bot')
                or message.get('external_reply') or message.get('is_automatic_forward')
                or message.get('text') != state['challenge']
                or message.get('date',0) < state['created_at']-5):
            continue
        if chat.get('type') == 'private' and chat.get('id') == state['owner_id']:
            private.append(message['message_id'])
        elif chat.get('type') in {'group','supergroup'} and type(chat.get('id')) is int and chat['id']<0:
            groups[chat['id']] = message['message_id']
    if not private:
        raise Refused('owner_private_challenge_missing')
    if len(groups)!=1:
        raise Refused('one_owner_operator_group_required')
    return next(iter(groups.items())),private[-1]


def collect(folder, owner_file, *, api_factory=ReadOnlyTelegram, now=time.time):
    folder = private_folder(folder)
    if (folder/'support.env').exists():
        raise Refused('existing_support_configuration_not_overwritten')
    state = json.loads(read_private(folder/'bootstrap.json'))
    if state['owner_id'] != trusted_owner(owner_file) or not 0 <= now()-state['created_at'] <= 86400:
        raise Refused('owner_or_challenge_expired')
    token = read_private(folder/'bot-token').strip()
    values(token,state['username'],state['owner_id'],-1)
    api = api_factory(token)
    me = identity(api,state['username'])
    if me['id'] != state['bot_id']:
        raise Refused('unexpected_support_bot')
    (group,group_message),private_message = matching_messages(api.call('getUpdates',{'timeout':0,'limit':100}),state)
    chat = api.call('getChat',{'chat_id':group})
    if (chat.get('id') != group or chat.get('type') not in {'group','supergroup'}
            or chat.get('username') or chat.get('active_usernames')):
        raise Refused('private_operator_group_required')
    for user,allowed in ((me['id'],{'administrator'}),(state['owner_id'],{'creator','administrator'})):
        membership = api.call('getChatMember',{'chat_id':group,'user_id':user})
        if (membership.get('status') not in allowed or (membership.get('user') or {}).get('id') != user):
            raise Refused('group_membership_not_verified')
    config = values(token,state['username'],state['owner_id'],group)
    save_private(folder/'support.env',''.join(k+'='+v+'\n' for k,v in config.items()))
    # IDs stay only in this private metadata, not in code/reports/raw updates.
    receipt = {'bot_id':me['id'],'owner_id':state['owner_id'],'admin_chat_id':group,
               'verified_at':int(now()),'private_proof_message':private_message,
               'group_proof_message':group_message,'test_owner_only':True,
               'no_acknowledgement':True,'no_service_started':True}
    save_private(folder/'identity-verified.json',json.dumps(receipt)+'\n')
    return {'identity_verified':True,'group_verified':True,'owner_verified':True,'owner_only_test':True}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('mode',choices=('prepare','collect'))
    parser.add_argument('--folder',type=Path,default=DEFAULT_FOLDER)
    parser.add_argument('--owner-file',type=Path,default=DEFAULT_OWNER)
    args = parser.parse_args()
    try:
        if args.mode=='prepare':
            if not sys.stdin.isatty():
                raise Refused('hidden_terminal_input_required')
            username = input('Username of the NEW Support bot: ').strip()
            with warnings.catch_warnings():
                warnings.simplefilter('error',GetPassWarning)
                token = getpass('Support bot token (hidden): ').strip()
            challenge = prepare(args.folder,args.owner_file,username,token)
            print('Support token verified and saved privately (0600). No bot process started.')
            print('Send this exact command FROM YOUR OWN ACCOUNT to both the new bot and the private operator group:')
            print(challenge)
            print('Then run the same command with collect. The bot will not reply yet.')
        else:
            collect(args.folder,args.owner_file)
            print('Support identity and private group verified. Owner-only support.env saved (0600).')
            print('No updates acknowledged; no messages sent; no bot or service started. Await deployment approval.')
    except BaseException as error:
        code = str(error) if isinstance(error,Refused) and re.fullmatch('[a-z_]{1,80}',str(error)) else 'preparation_failed'
        raise SystemExit('Support preparation stopped: '+code+'. No secrets were printed.') from None


if __name__=='__main__':
    main()
