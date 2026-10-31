"""No real Telegram, credentials or owner data: only mock bootstrap fixtures."""
import asyncio
from dataclasses import replace
import json
from pathlib import Path
from unittest.mock import AsyncMock
import urllib.error

import pytest
from support_bot import bootstrap as b
from support_bot.config import Config
from tests.test_support_bot import Harness, store

TOKEN = '123456:'+'x'*30
USERNAME = 'KudaVPN_Support_Test_Bot'


class API:
    def __init__(self):
        self.calls=[]
        self.responses={
            'getMe':{'id':123456,'username':USERNAME,'is_bot':True},
            'getWebhookInfo':{'url':''},
            'getChat':{'id':-999,'type':'supergroup'},
            'getUpdates':[],
        }
        self.members={123456:{'status':'administrator','user':{'id':123456}},
                      123:{'status':'creator','user':{'id':123}}}

    def call(self, method, payload):
        self.calls.append((method,payload))
        assert method in b.METHODS
        return self.members[payload['user_id']] if method=='getChatMember' else self.responses[method]


@pytest.fixture
def setup(tmp_path):
    folder=tmp_path/'private';owner=tmp_path/'owner.json'
    owner.write_text(json.dumps({'owner_id':123,'owner_start_verified':True}));owner.chmod(0o600)
    api=API()
    challenge=b.prepare(folder,owner,USERNAME,TOKEN,api_factory=lambda _:api,now=lambda:1000)
    return folder,owner,api,challenge


def proof(chat,challenge,mid=1,actor=123,**extra):
    return {'message':{'message_id':mid,'date':1001,'from':{'id':actor,'is_bot':False},
                       'chat':{'id':chat,'type':'private' if chat>0 else 'supergroup'},'text':challenge,**extra}}


def test_prepare_verifies_identity_only_and_keeps_everything_private(setup):
    folder,owner,api,challenge=setup
    assert [call[0] for call in api.calls]==['getMe','getWebhookInfo']
    assert challenge.startswith('/support_setup_') and TOKEN not in challenge
    assert (folder/'bot-token').stat().st_mode & 0o777 == 0o600
    assert (folder/'bootstrap.json').stat().st_mode & 0o777 == 0o600
    assert folder.stat().st_mode & 0o777 == 0o700
    assert not (folder/'support.env').exists()
    assert TOKEN not in repr(b.ReadOnlyTelegram(TOKEN))


def test_collect_verifies_both_owner_messages_and_memberships_without_ack(setup):
    folder,owner,api,challenge=setup
    api.responses['getUpdates']=[proof(123,challenge),proof(-999,challenge,2)]
    result=b.collect(folder,owner,api_factory=lambda _:api,now=lambda:1010)
    assert all(result.values())
    config=dict(line.split('=',1) for line in (folder/'support.env').read_text().splitlines())
    parsed=Config.from_env(config)
    assert parsed.admin_chat_id==-999 and parsed.operator_ids==frozenset({123})
    assert parsed.test_user_ids==frozenset({123})
    assert (folder/'support.env').stat().st_mode & 0o777==0o600
    assert (folder/'identity-verified.json').stat().st_mode & 0o777==0o600
    updates=[payload for name,payload in api.calls if name=='getUpdates']
    assert updates==[{'timeout':0,'limit':100}]
    assert not any('offset' in payload or 'allowed_updates' in payload for payload in updates)
    with pytest.raises(b.Refused,match='not_overwritten'):
        b.collect(folder,owner,api_factory=lambda _:api,now=lambda:1011)


@pytest.mark.parametrize('extra',[
    {'sender_chat':{'id':-999}}, {'forward_origin':{'type':'hidden_user'}},
    {'via_bot':{'id':555}}, {'external_reply':{'origin':{}}}, {'is_automatic_forward':True},
])
def test_forwarded_or_anonymous_proof_never_binds_group(setup,extra):
    folder,owner,api,challenge=setup
    api.responses['getUpdates']=[proof(123,challenge),proof(-999,challenge,2,**extra)]
    with pytest.raises(b.Refused,match='one_owner_operator_group'):
        b.collect(folder,owner,api_factory=lambda _:api,now=lambda:1010)
    assert not (folder/'support.env').exists()


def test_stranger_cannot_claim_owner_or_group(setup):
    folder,owner,api,challenge=setup
    api.responses['getUpdates']=[proof(888,challenge,actor=888),proof(-999,challenge,actor=888)]
    with pytest.raises(b.Refused,match='owner_private_challenge_missing'):
        b.collect(folder,owner,api_factory=lambda _:api,now=lambda:1010)


def test_ambiguous_groups_are_not_guessed(setup):
    folder,owner,api,challenge=setup
    api.responses['getUpdates']=[proof(123,challenge),proof(-999,challenge),proof(-555,challenge)]
    with pytest.raises(b.Refused,match='one_owner_operator_group'):
        b.collect(folder,owner,api_factory=lambda _:api,now=lambda:1010)
    assert not any(name=='getChat' for name,_ in api.calls)


@pytest.mark.parametrize('chat_extra',[{'username':'public_group'},{'active_usernames':['public_alias']}])
def test_public_operator_group_is_refused(setup,chat_extra):
    folder,owner,api,challenge=setup
    api.responses['getUpdates']=[proof(123,challenge),proof(-999,challenge)]
    api.responses['getChat'].update(chat_extra)
    with pytest.raises(b.Refused,match='private_operator_group_required'):
        b.collect(folder,owner,api_factory=lambda _:api,now=lambda:1010)


@pytest.mark.parametrize('user',[123,123456])
def test_owner_and_bot_group_memberships_are_confirmed(setup,user):
    folder,owner,api,challenge=setup
    api.responses['getUpdates']=[proof(123,challenge),proof(-999,challenge)]
    api.members[user]['status']='member'
    with pytest.raises(b.Refused,match='membership_not_verified'):
        b.collect(folder,owner,api_factory=lambda _:api,now=lambda:1010)


def test_old_challenge_and_changed_owner_refused_before_telegram(setup):
    folder,owner,api,challenge=setup
    with pytest.raises(b.Refused,match='owner_or_challenge_expired'):
        b.collect(folder,owner,api_factory=lambda _:api,now=lambda:100000)
    owner.write_text(json.dumps({'owner_id':456,'owner_start_verified':True}))
    with pytest.raises(b.Refused,match='owner_or_challenge_expired'):
        b.collect(folder,owner,api_factory=lambda _:api,now=lambda:1010)
    assert not any(name=='getUpdates' for name,_ in api.calls)


def test_active_webhook_is_not_removed(tmp_path):
    api=API();api.responses['getWebhookInfo']={'url':'https://private.example/webhook'}
    owner=tmp_path/'owner.json';owner.write_text('{"owner_id":123,"owner_start_verified":true}');owner.chmod(0o600)
    with pytest.raises(b.Refused,match='existing_webhook'):
        b.prepare(tmp_path/'private',owner,USERNAME,TOKEN,api_factory=lambda _:api)
    assert not (tmp_path/'private'/'bot-token').exists()
    assert all(name in {'getMe','getWebhookInfo'} for name,_ in api.calls)


def test_reserved_bot_refused_before_network(tmp_path):
    owner=tmp_path/'owner.json';owner.write_text('{"owner_id":123,"owner_start_verified":true}');owner.chmod(0o600)
    factory=AsyncMock()
    with pytest.raises(ValueError):
        b.prepare(tmp_path/'private',owner,'KudaVpnBot',TOKEN,api_factory=factory)
    factory.assert_not_called()


def test_wrong_identity_and_permissions_refused(setup):
    folder,owner,api,challenge=setup
    api.responses['getMe']['username']='Other_Support_Bot'
    with pytest.raises(b.Refused,match='unexpected_support_bot'):
        b.collect(folder,owner,api_factory=lambda _:api,now=lambda:1010)
    (folder/'bot-token').chmod(0o644)
    with pytest.raises(b.Refused,match='private_file_permissions'):
        b.collect(folder,owner,api_factory=lambda _:api,now=lambda:1010)


def test_transport_whitelist_and_non_ack_request_are_enforced(monkeypatch):
    api=b.ReadOnlyTelegram(TOKEN)
    for method,payload in [('sendMessage',{}),('deleteWebhook',{}),('getUpdates',{'offset':123}),
                           ('getUpdates',{'timeout':0,'limit':100,'allowed_updates':[]})]:
        with pytest.raises(b.Refused):
            api.call(method,payload)
    monkeypatch.setattr(b.urllib.request,'build_opener',lambda *args: (_ for _ in ()).throw(
        urllib.error.URLError('https://api.telegram.org/bot'+TOKEN+'/getMe')))
    with pytest.raises(b.Refused) as error:
        api.call('getMe',{})
    assert TOKEN not in str(error.value)


def test_main_rejects_non_tty_before_token_input(monkeypatch,capsys):
    monkeypatch.setattr('sys.argv',['bootstrap','prepare'])
    monkeypatch.setattr(b.sys.stdin,'isatty',lambda:False)
    prompt=AsyncMock();monkeypatch.setattr(b,'getpass',prompt)
    with pytest.raises(SystemExit,match='hidden_terminal_input_required'):
        b.main()
    prompt.assert_not_called()
    assert TOKEN not in capsys.readouterr().out


def test_owner_only_test_gate_denies_other_private_users_but_keeps_operators(store):
    async def run():
        h=Harness(store)
        h.config=replace(h.config,test_user_ids=frozenset({123}));h.app.config=h.config
        await h.message('/start',owner=456)
        assert not h.calls
        await h.message('/start',owner=123)
        assert any(getattr(call,'chat_id',None)==123 for call in h.calls)
        await h.close()
    asyncio.run(run())
