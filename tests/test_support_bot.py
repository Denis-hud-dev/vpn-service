"""Isolated support tests: temporary SQLite + fake Telegram, no network/secrets."""
import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
import sqlite3

import pytest
from aiogram import Bot, Dispatcher
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramNetworkError, TelegramRetryAfter
from aiogram.methods import SendMessage
from aiogram.types import CallbackQuery, Message, Update

from support_bot.application import Application, dispatch_one
from support_bot.config import Config
from support_bot.diagnostics import Diagnostics, VerifiedAccount, SubscriptionView, ServerView
from support_bot.knowledge import CATEGORIES, QUESTIONS
from support_bot.storage import Store, Rejected
from support_bot import ui


@pytest.fixture
def store(tmp_path):
    result = Store(tmp_path/'support.sqlite3',-999,clock=lambda:1000)
    result.migrate()
    return result


def config(path, custom=True):
    return Config('123456:offline_support_token',-999,frozenset({777}),path,'KudaVPN_Support_Test_Bot',custom)


class Harness:
    def __init__(self,store):
        self.store = store
        self.config = config(store.path)
        self.app = Application(store,self.config)
        self.bot = Bot(self.config.token)
        self.bot.session = AsyncMock(side_effect=self.request)
        self.dp = Dispatcher()
        self.dp.include_router(self.app.router)
        self.calls=[]; self.seq=100; self.update=1; self.messages={}
        self.fail_send=None; self.fail_delete=False; self.fail_emoji=False

    async def request(self,bot,method,**kwargs):
        self.calls.append(method)
        api=method.__api_method__
        if api in ('sendMessage','sendPhoto','sendDocument'):
            if self.fail_send:
                raise self.fail_send
            if self.fail_emoji and method.reply_markup and any(b.icon_custom_emoji_id for row in method.reply_markup.inline_keyboard for b in row):
                raise TelegramBadRequest(method=method,message='custom emoji unavailable')
            self.seq+=1
            msg=self.message_data(method.chat_id,self.seq,method.text if api=='sendMessage' else method.caption,
                                  bot.id,bot=True)
            self.messages[(method.chat_id,self.seq)]=msg
            return Message.model_validate(msg).as_(bot)
        if api=='deleteMessage':
            if self.fail_delete:
                raise TelegramBadRequest(method=method,message='cannot delete')
            self.messages.pop((method.chat_id,method.message_id),None)
        return True

    @staticmethod
    def message_data(chat,mid,text,actor,bot=False,**extra):
        return {'message_id':mid,'date':1000,'chat':{'id':chat,'type':'private' if chat>0 else 'supergroup'},
                'from':{'id':actor,'is_bot':bot,'first_name':'Support' if bot else 'Person'},'text':text,**extra}

    async def message(self,text='',owner=123,chat=None,mid=None,**extra):
        self.update+=1
        raw=self.message_data(chat if chat is not None else owner,mid or self.update,text,owner,**extra)
        await self.dp.feed_update(self.bot,Update.model_validate({'update_id':self.update,'message':raw}))

    async def callback(self,key,owner=123,chat=None,mid=None,query_id=None,**extra):
        self.update+=1
        chat=owner if chat is None else chat
        mid=mid or self.store.session(owner)['nav_id'] or 100
        raw=self.message_data(chat,mid,'menu',self.bot.id,bot=True,**extra)
        query={'id':query_id or str(self.update),'from':{'id':owner,'is_bot':False,'first_name':'Person'},
               'chat_instance':'offline','data':key,'message':raw}
        await self.dp.feed_update(self.bot,Update.model_validate({'update_id':self.update,'callback_query':query}))

    async def drain(self):
        for _ in range(50):
            if not await dispatch_one(self.store,self.config,self.bot):
                return
        raise AssertionError('queue did not drain')

    async def close(self):
        await self.dp.storage.close()


def test_migrations_are_explicit_idempotent_and_private(store):
    store.migrate()
    assert store.path.stat().st_mode & 0o777 == 0o600
    with store.connect() as db:
        assert db.execute('PRAGMA foreign_key_check').fetchall()==[]
        assert db.execute('PRAGMA user_version').fetchone()[0]==1
        assert db.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
        assert not db.execute("SELECT name FROM sqlite_master WHERE name IN ('users','subscriptions','payments')").fetchall()


def test_refuse_foreign_database_and_wrong_path(tmp_path):
    path=tmp_path/'support.sqlite3'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE users(id INTEGER)')
    path.chmod(0o600)
    with pytest.raises(Rejected,match='not_support_database'):
        Store(path,-999).migrate()
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT name FROM sqlite_master WHERE name='users'").fetchone()
    with pytest.raises(Rejected):
        Store(tmp_path/'working.sqlite3',-999).migrate()
    link=tmp_path/'link';link.symlink_to(path)
    with pytest.raises(Rejected):
        Store(link,-999).migrate()


def test_navigation_faq_back_repeat_start_clean_only_navigation(store):
    async def run():
        h=Harness(store)
        await h.message('/start')
        important=await h.bot.send_message(123,'Ticket/payment history must remain')
        initial=store.session(123)['nav_id']
        for cat in CATEGORIES:
            await h.callback('cat:'+cat)
            for q,(parent,_,_) in QUESTIONS.items():
                if parent==cat:
                    await h.callback('q:'+q)
                    assert 'Проблема решена' in str(h.calls[-2:])
            await h.callback('home')
        await h.message('/start')
        assert (123,initial) not in h.messages
        assert (123,important.message_id) in h.messages
        assert len([k for k in h.messages if k[0]==123])==2
        assert len(ui.home(True)[1].inline_keyboard)==7
        assert all(b.icon_custom_emoji_id for row in ui.home(True)[1].inline_keyboard for b in row)
        await h.close()
    asyncio.run(run())


def test_create_screenshot_status_add_close_and_restart(store):
    async def run():
        h=Harness(store)
        await h.message('/start');await h.callback('help:android')
        await h.message('Не подключается',photo=[{'file_id':'screenshot1','file_unique_id':'unique','width':400,'height':300}])
        target=store.list_tickets(123)[0]
        assert target['category']=='connect' and target['question']=='android'
        assert target['status']=='new'
        await h.drain()
        assert any(getattr(c,'chat_id',None)==-999 for c in h.calls)
        assert json.loads(store.transcript(target['id'],123)[0]['attachment'])['file_id']=='screenshot1'
        await h.close()
        restarted=Store(store.path,-999,clock=lambda:1001)
        h=Harness(restarted)
        assert restarted.session(123)['target']==target['id']
        await h.message('Дополнение после перезапуска')
        assert len(restarted.transcript(target['id'],123))==2
        await h.callback('tickets');await h.callback('ticket:'+str(target['id']))
        await h.callback('close:'+str(target['id']))
        assert restarted.get_ticket(target['id'],123)['status']=='closed'
        await h.drain()
        # Ticket notifications persist; only navigation is removed.
        assert sum(k[0]==123 for k in h.messages)>=2
        await h.close()
    asyncio.run(run())


def test_duplicates_concurrency_and_rate_limits(store):
    tid,created=store.user_message(123,'connect','android','Error',None,'msg1')
    assert created
    assert store.user_message(123,'connect','android','Error',None,'msg1')==(tid,False)
    assert store.user_message(123,'connect','android','Error',None,'msg2')==(tid,False)
    for index in range(8):
        store.user_message(123,'billing',None,'More '+str(index),None,'other'+str(index))
    with pytest.raises(Rejected,match='rate_limited'):
        store.user_message(123,'billing',None,'Spam',None,'spam')
    assert len(store.list_tickets(123))==1


def test_simultaneous_requests_still_create_one_ticket(store):
    from concurrent.futures import ThreadPoolExecutor
    def create(index):
        return store.user_message(123,'contact',None,'Issue '+str(index),None,'parallel'+str(index))[0]
    with ThreadPoolExecutor(max_workers=4) as pool:
        ids=list(pool.map(create,range(4)))
    assert len(set(ids))==1


def test_user_cannot_access_other_ticket(store):
    tid,_=store.user_message(123,'contact',None,'Private issue',None,'a')
    for action in (store.get_ticket,store.transcript):
        with pytest.raises(Rejected,match='ticket_not_found'):
            action(tid,456)
    with pytest.raises(Rejected):
        store.close_user(tid,456,'unauthorized')
    assert store.get_ticket(tid,123)['status']=='new'


def test_operator_group_and_user_reply_round_trip(store):
    async def run():
        h=Harness(store)
        await h.message('/start');await h.callback('contact');await h.message('Ошибка')
        await h.drain()
        group=[mid for chat,mid in h.messages if chat==-999][0]
        tid=store.list_tickets(123)[0]['id']
        await h.callback(f'op:take:{tid}',owner=777,chat=-999,mid=group)
        assert store.get_ticket(tid,123)['status']=='working'
        reply=h.messages[(-999,group)]
        await h.message('Проверьте другую сеть',owner=777,chat=-999,reply_to_message=reply)
        assert store.get_ticket(tid,123)['status']=='waiting'
        await h.drain()
        user_texts=[m['text'] for (chat,_),m in h.messages.items() if chat==123]
        assert any('Проверьте другую сеть' in text for text in user_texts)
        assert all('777' not in text and 'Person' not in text for text in user_texts)
        await h.callback(f'op:resolve:{tid}',owner=777,chat=-999,mid=group)
        assert store.get_ticket(tid,123)['status']=='resolved'
        await h.close()
    asyncio.run(run())


@pytest.mark.parametrize('actor,chat,extra',[
    (888,-999,{}),(777,-555,{}),(777,777,{}),
    (777,-999,{'forward_origin':{'type':'hidden_user','date':1000,'sender_user_name':'Copied'}}),
    (777,-999,{'sender_chat':{'id':-999,'type':'supergroup','title':'Anonymous'}}),
])
def test_operator_commands_denied_for_strangers_other_chats_and_forwards(store,actor,chat,extra):
    async def run():
        h=Harness(store)
        tid,_=store.user_message(123,'contact',None,'Issue',None,'m')
        await h.drain()
        mid=[mid for c,mid in h.messages if c==-999][0]
        await h.callback(f'op:close:{tid}',owner=actor,chat=chat,mid=mid,**extra)
        assert store.get_ticket(tid,123)['status']=='new'
        reply=h.messages[(-999,mid)]
        await h.message('Hacked response',owner=actor,chat=chat,reply_to_message=reply,**extra)
        assert not any(r['text']=='Hacked response' for r in store.transcript(tid,123))
        await h.close()
    asyncio.run(run())


def test_spoofed_ticket_callback_and_reply_binding_denied(store):
    async def run():
        h=Harness(store)
        tid,_=store.user_message(123,'contact',None,'Issue',None,'m')
        other,_=store.user_message(456,'contact',None,'Private',None,'n')
        await h.drain()
        mid=[mid for c,mid in h.messages if c==-999][0]
        await h.callback(f'op:close:{other}',owner=777,chat=-999,mid=mid)
        await h.callback(f'op:close:{tid}',owner=777,chat=-999,mid=999999)
        await h.message('Forged',owner=777,chat=-999,
                        reply_to_message=h.message_data(-999,999999,'Fake',h.bot.id,bot=True))
        assert store.get_ticket(tid,123)['status']=='new'
        assert store.get_ticket(other,456)['status']=='new'
        await h.close()
    asyncio.run(run())


def test_operator_ownership_and_idempotency(store):
    tid,_=store.user_message(123,'contact',None,'Issue',None,'m')
    assert store.operator_action(tid,777,'take','take1')
    assert not store.operator_action(tid,777,'take','take1')
    with pytest.raises(Rejected,match='ticket_unavailable'):
        store.operator_action(tid,888,'close','other')
    store.operator_action(tid,777,'ask','ask1')
    assert store.get_ticket(tid,123)['status']=='waiting'
    store.operator_action(tid,777,'close','close1')
    assert store.get_ticket(tid,123)['status']=='closed'


def test_outbox_timeout_restart_retry_and_crash_lease(store):
    async def run():
        h=Harness(store)
        tid,_=store.user_message(123,'contact',None,'Issue',None,'m')
        h.fail_send=TelegramNetworkError(method=SendMessage(chat_id=-999,text='safe'),message='offline')
        await dispatch_one(store,h.config,h.bot)
        with store.connect() as db:
            row=db.execute("SELECT * FROM deliveries WHERE audience='operator'").fetchone()
            assert row['state']=='pending' and row['error_code']=='transport_or_ack_failed'
        store.clock=lambda:1100
        h.fail_send=None
        await h.drain()
        assert store.health()=={'pending':0,'blocked':0}
        # Crash after a claim, before Telegram delivery; a new Store reclaims the lease.
        store.user_message(123,'contact',None,'Another detail',None,'new')
        claimed=store.claim();assert claimed
        restarted=Store(store.path,-999,clock=lambda:1200)
        assert restarted.claim()['id']==claimed['id']
        await h.close()
    asyncio.run(run())


@pytest.mark.parametrize('error,blocked', [('forbidden',1),('bad_request',1),('retry_after',0)])
def test_delivery_errors_are_persistent_without_secret_logs(store,caplog,error,blocked):
    async def run():
        h=Harness(store)
        store.user_message(123,'contact',None,'Issue',None,'m')
        method=SendMessage(chat_id=-999,text='safe')
        h.fail_send={'forbidden':TelegramForbiddenError(method=method,message='secret content'),
                     'bad_request':TelegramBadRequest(method=method,message='secret content'),
                     'retry_after':TelegramRetryAfter(method=method,message='secret content',retry_after=90)}[error]
        await dispatch_one(store,h.config,h.bot)
        assert store.health()['blocked']==blocked
        with store.connect() as db:
            row=db.execute('SELECT * FROM deliveries ORDER BY id LIMIT 1').fetchone()
            if error=='retry_after':
                assert row['due']>=1090
        assert 'secret content' not in caplog.text
        await h.close()
    asyncio.run(run())


def test_navigation_delete_and_custom_emoji_failures_do_not_break_flow(store):
    async def run():
        h=Harness(store);h.fail_delete=True;h.fail_emoji=True
        await h.message('/start');await h.callback('cat:connect');await h.callback('q:android')
        assert store.session(123)['nav_key']=='q:android'
        assert any(c.__api_method__=='editMessageReplyMarkup' for c in h.calls)
        assert any(isinstance(c,SendMessage) and c.reply_markup and not any(
            b.icon_custom_emoji_id for row in c.reply_markup.inline_keyboard for b in row) for c in h.calls)
        await h.close()
    asyncio.run(run())


def test_private_callback_cannot_close_other_users_ticket(store):
    async def run():
        h=Harness(store)
        tid,_=store.user_message(456,'contact',None,'Private',None,'p')
        await h.message('/start')
        await h.callback(f'close:{tid}')
        assert store.get_ticket(tid,456)['status']=='new'
        await h.close()
    asyncio.run(run())


@pytest.mark.parametrize('payload',[
    {'text':'vless://private-data'},
    {'text':'https://example.org/sub/personal'},
    {'text':'x'*3001},
    {'text':'','document':{'file_id':'f','file_unique_id':'u','mime_type':'application/pdf'}},
])
def test_sensitive_or_unsupported_content_not_rebroadcast(store,payload):
    async def run():
        h=Harness(store)
        await h.message('/start');await h.callback('contact')
        await h.message(**payload)
        assert store.list_tickets(123)==[]
        await h.close()
    asyncio.run(run())


def test_diagnostics_disabled_and_account_binding_is_enforced():
    async def run():
        assert 'Нет данных' in await Diagnostics().report(123)
        reader=SimpleNamespace(verified_account=AsyncMock(return_value=VerifiedAccount(456,'other')),
                               subscription=AsyncMock(),servers=AsyncMock())
        assert 'привязка' in await Diagnostics(reader).report(123)
        reader.subscription.assert_not_awaited()
        reader.verified_account.return_value=VerifiedAccount(123,'own')
        reader.subscription.return_value=SubscriptionView('other','active',None)
        assert 'принадлежности' in await Diagnostics(reader).report(123)
        reader.servers.assert_not_awaited()
    asyncio.run(run())


@pytest.mark.parametrize('status,delta,expected',[('active',1,'Активна'),('active',-1,'Истекла'),('expired',-1,'Истекла')])
def test_diagnostics_reports_actual_expiry_and_stale_monitoring(status,delta,expected):
    async def run():
        now=datetime.now(timezone.utc)
        reader=SimpleNamespace(verified_account=AsyncMock(return_value=VerifiedAccount(123,'own')),
            subscription=AsyncMock(return_value=SubscriptionView('own',status,now+timedelta(days=delta),'Premium')),
            servers=AsyncMock(return_value=[ServerView('Node','ok',now-timedelta(hours=1))]))
        report=await Diagnostics(reader).report(123)
        assert expected in report and 'Нет данных · измерение устарело' in report
        assert 'Node: Работает' not in report
    asyncio.run(run())


def test_config_has_no_main_bot_fallback_and_blocks_wrong_identity(tmp_path,monkeypatch):
    for key in ('SUPPORT_BOT_TOKEN','SUPPORT_ADMIN_CHAT_ID','SUPPORT_OPERATOR_IDS','SUPPORT_DB_PATH','SUPPORT_BOT_USERNAME'):
        monkeypatch.delenv(key,raising=False)
    monkeypatch.setenv('BOT_TOKEN','must-not-be-used')
    with pytest.raises(ValueError):
        Config.from_env()
    monkeypatch.setenv('SUPPORT_BOT_TOKEN','123456:'+'x'*30)
    monkeypatch.setenv('SUPPORT_ADMIN_CHAT_ID','-999')
    monkeypatch.setenv('SUPPORT_OPERATOR_IDS','777,888')
    monkeypatch.setenv('SUPPORT_DB_PATH',str(tmp_path/'support.sqlite3'))
    monkeypatch.setenv('SUPPORT_BOT_USERNAME','KudaVPN_Support_Test_Bot')
    cfg=Config.from_env()
    assert cfg.operator_ids==frozenset({777,888}) and cfg.token not in repr(cfg)
    async def run():
        bot=SimpleNamespace(get_me=AsyncMock(return_value=SimpleNamespace(username='KudaVpnBot')))
        with pytest.raises(ValueError,match='identity_mismatch'):
            await cfg.validate_identity(bot)
        bot.get_me.return_value=SimpleNamespace(username=cfg.expected_username)
        bot.get_chat=AsyncMock(return_value=SimpleNamespace(type='supergroup',username='public_group'))
        with pytest.raises(ValueError,match='private_operator_group'):
            await cfg.validate_identity(bot)
    asyncio.run(run())


def test_polling_does_not_acknowledge_unsaved_update_and_persists_success(store):
    from support_bot.polling import poll
    async def run():
        update=Update(update_id=42)
        bot=SimpleNamespace(get_updates=AsyncMock(return_value=[update]))
        dispatcher=SimpleNamespace(feed_update=AsyncMock(side_effect=RuntimeError('database unavailable')))
        with pytest.raises(RuntimeError):
            await poll(bot,dispatcher,store)
        assert store.offset()==0
        assert bot.get_updates.await_args.kwargs['offset']==0
        assert bot.get_updates.await_args.kwargs['request_timeout'] > bot.get_updates.await_args.kwargs['timeout']
        assert bot.get_updates.await_count==1
        bot.get_updates=AsyncMock(side_effect=[[update],RuntimeError('stop test')])
        dispatcher.feed_update=AsyncMock()
        with pytest.raises(RuntimeError,match='stop test'):
            await poll(bot,dispatcher,store)
        assert store.offset()==43
        restarted=Store(store.path,-999)
        assert restarted.offset()==43
        assert bot.get_updates.await_args.kwargs['offset']==43
    asyncio.run(run())


def test_blocked_delivery_requires_explicit_retry_without_new_event(store):
    store.user_message(123,'contact',None,'Issue',None,'m')
    delivery=store.claim()
    store.failed(delivery,'forbidden',permanent=True)
    assert store.delivery_health()['blocked']==1
    assert store.retry_blocked(delivery['id'])
    assert not store.retry_blocked(delivery['id'])
    assert store.delivery_health()['blocked']==0
    assert store.claim()['id']==delivery['id']


def test_close_cooldown_and_duplicate_operator_reply(store):
    tid,_=store.user_message(123,'contact',None,'Issue',None,'m')
    store.operator_action(tid,777,'reply','r','Answer')
    before=len(store.transcript(tid,123))
    assert not store.operator_action(tid,777,'reply','r','Answer')
    assert len(store.transcript(tid,123))==before
    store.close_user(tid,123,'c')
    with pytest.raises(Rejected,match='rate_limited'):
        store.user_message(123,'contact',None,'Spam new ticket',None,'m2')
    store.clock=lambda:1100
    assert store.user_message(123,'contact',None,'Follow-up',None,'m2')[0] != tid


def test_attachment_from_operator_reaches_user_without_forwarding_identity(store):
    async def run():
        h=Harness(store)
        tid,_=store.user_message(123,'contact',None,'Issue',None,'m')
        await h.drain()
        mid=[mid for c,mid in h.messages if c==-999][0]
        await h.message('',owner=777,chat=-999,reply_to_message=h.messages[(-999,mid)],
                        photo=[{'file_id':'operator-image','file_unique_id':'u','width':200,'height':200}])
        await h.drain()
        sent=[c for c in h.calls if c.__api_method__=='sendPhoto' and c.chat_id==123]
        assert len(sent)==1 and sent[0].photo=='operator-image'
        assert not any(c.__api_method__=='forwardMessage' for c in h.calls)
        assert store.get_ticket(tid,123)['status']=='waiting'
        await h.close()
    asyncio.run(run())


def test_kb_tariffs_match_existing_approved_catalog():
    from bot.services import PILOT_TARIFFS
    text=QUESTIONS['plans'][2]
    assert [p.price_rub for p in PILOT_TARIFFS.values()]==[199,499,1790]
    assert all(f'{p.price_rub} ₽' in text for p in PILOT_TARIFFS.values())
    for _,_,body in QUESTIONS.values():
        assert 'https://' not in body  # No external payment link in support bot.


def test_database_cannot_be_reused_by_another_bot_or_operator_chat(store):
    store.bind(123456,-999)
    store.bind(123456,-999)
    with pytest.raises(Rejected,match='identity_mismatch'):
        store.bind(654321,-999)
    with pytest.raises(Rejected,match='identity_mismatch'):
        store.bind(123456,-555)


def test_local_token_helper_saves_only_private_file_without_echo(tmp_path,monkeypatch,capsys):
    import support_bot.setup_config as setup
    destination=tmp_path/'private'/'support.env'
    monkeypatch.setattr('sys.argv',['setup','--output',str(destination)])
    token='123456:'+'x'*30
    monkeypatch.setattr(setup,'getpass',lambda prompt:token)
    monkeypatch.setattr(setup.sys.stdin,'isatty',lambda:True)
    answers=iter(['KudaVPN_Support_Test_Bot','-999','777,888'])
    monkeypatch.setattr('builtins.input',lambda prompt:next(answers))
    setup.main()
    assert token not in capsys.readouterr().out
    assert destination.stat().st_mode & 0o777 == 0o600
    assert destination.parent.stat().st_mode & 0o777 == 0o700
    assert 'SUPPORT_BOT_TOKEN='+token in destination.read_text()
    with pytest.raises(SystemExit):
        setup.main()  # Never overwrite existing credentials.


def test_real_monitor_snapshot_statuses_do_not_imply_phone_connectivity():
    async def run():
        now=datetime.now(timezone.utc)
        reader=SimpleNamespace(verified_account=AsyncMock(return_value=VerifiedAccount(123,'own')),
            subscription=AsyncMock(return_value=SubscriptionView('own','active',now+timedelta(days=1))),
            servers=AsyncMock(return_value=[ServerView('Node 1','ok',now-timedelta(seconds=10)),
                ServerView('Node 2','error',now-timedelta(seconds=10)),ServerView('Node 3','unknown',now)]))
        text=await Diagnostics(reader).report(123)
        assert 'Node 1: Работает' in text and 'Node 2: Ошибка' in text and 'Node 3: Нет данных' in text
        assert 'не подтверждает' in text
        reader.servers.side_effect=RuntimeError('private endpoint')
        assert await Diagnostics(reader).report(123)=='Нет данных · источник диагностики временно недоступен.'
    asyncio.run(run())


def test_migration_rejects_nonprivate_directory(tmp_path):
    directory=tmp_path/'public';directory.mkdir(mode=0o755)
    with pytest.raises(Rejected,match='directory_permissions'):
        Store(directory/'support.sqlite3',-999).migrate()
    assert not (directory/'support.sqlite3').exists()


def test_rejected_input_during_telegram_outage_does_not_drop_existing_ticket(store):
    async def run():
        h=Harness(store)
        await h.message('/start');await h.callback('contact')
        await h.message('Valid issue')
        tid=store.list_tickets(123)[0]['id']
        h.fail_send=TelegramNetworkError(method=SendMessage(chat_id=123,text='safe'),message='offline')
        await h.message('vless://private-data')
        assert store.get_ticket(tid,123)['status']=='new'
        assert len(store.transcript(tid,123))==1
        await h.close()
    asyncio.run(run())


def test_actual_dispatcher_storage_failure_is_not_acknowledged(store,monkeypatch):
    from support_bot.polling import poll
    async def run():
        h=Harness(store)
        await h.message('/start');await h.callback('contact')
        update=Update.model_validate({'update_id':50,
            'message':h.message_data(123,50,'Issue not yet saved',123)})
        h.bot.get_updates=AsyncMock(return_value=[update])
        def unavailable(*args,**kwargs):
            raise OSError('offline test storage failure')
        monkeypatch.setattr(store,'user_message',unavailable)
        with pytest.raises(OSError):
            await poll(h.bot,h.dp,store)
        assert store.offset()==0 and store.list_tickets(123)==[]
        assert h.bot.get_updates.await_count==1
        await h.close()
    asyncio.run(run())


def test_suppressed_duplicate_stays_suppressed_after_restart_and_window(store):
    tid,_=store.user_message(123,'contact',None,'Same issue',None,'first')
    assert store.user_message(123,'contact',None,'Same issue',None,'repeated')==(tid,False)
    restarted=Store(store.path,-999,clock=lambda:1200)
    assert restarted.user_message(123,'contact',None,'Same issue',None,'repeated')==(tid,False)
    with restarted.connect() as db:
        assert db.execute('SELECT count(*) FROM history').fetchone()[0]==1
        assert db.execute('SELECT count(*) FROM deliveries').fetchone()[0]==2
