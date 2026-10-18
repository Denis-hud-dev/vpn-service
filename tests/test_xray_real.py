"""Offline end-to-end test: temporary REALITY Xray + mTLS node agent.

Run with TEST_XRAY_BINARY set to a trusted local Xray 26.3.27 binary.
No production config, token, server or route is used.
"""
import asyncio
import json
import os
import shutil
import socket
import ssl
import subprocess
import time
from pathlib import Path
from uuid import uuid4
import pytest
import aiohttp
from aiohttp import web
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy import select
from bot.db.models import Base, User, Subscription, VPNNode, NodeAccess
from bot.services.users import UserService
from bot.services.payments import PaymentRepository
from bot.services.payment_events import ConfirmedPayment
from bot.services.subscriptions import SubscriptionService, utcnow, as_utc
from bot.services.happ import HappService, NodeReconciler, AccessDenied
from bot.services.node_health import NodeHealthMonitor
from bot.services.node_gateway import HTTPSNodeGateway, NodeGatewayError
from bot.services import TARIFFS
from bot.node_agent.store import LeaseStore
from bot.node_agent.xray_cli import XrayCLI
from bot.node_agent.service import NodeAgent, create_app
from tests.test_subscription_lifecycle import TestVerifier


def free_port():
    with socket.socket() as s:
        s.bind(('127.0.0.1',0))
        return s.getsockname()[1]


def run(*args):
    p=subprocess.run(args,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,timeout=12)
    if p.returncode:raise AssertionError('Local test command failed')
    return p.stdout


def make_certs(root):
    config=root/'openssl.cnf'
    config.write_text('''[req]\ndistinguished_name=dn\nprompt=no\nx509_extensions=ca_ext\n[dn]\nCN=KudaVPN Test CA\n[ca_ext]\nbasicConstraints=critical,CA:TRUE\nkeyUsage=critical,keyCertSign,cRLSign\n[server_ext]\nbasicConstraints=CA:FALSE\nkeyUsage=digitalSignature,keyEncipherment\nextendedKeyUsage=serverAuth\nsubjectAltName=DNS:localhost,IP:127.0.0.1\n[client_ext]\nbasicConstraints=CA:FALSE\nkeyUsage=digitalSignature\nextendedKeyUsage=clientAuth\n''')
    ca=root/'ca.crt';cak=root/'ca.key'
    run('openssl','req','-x509','-newkey','rsa:2048','-nodes','-days','1',
        '-keyout',str(cak),'-out',str(ca),'-config',str(config))
    for name,ext in [('server','server_ext'),('client','client_ext')]:
        key=root/(name+'.key');csr=root/(name+'.csr');crt=root/(name+'.crt')
        run('openssl','req','-new','-newkey','rsa:2048','-nodes','-keyout',str(key),
            '-out',str(csr),'-subj','/CN='+name)
        run('openssl','x509','-req','-in',str(csr),'-CA',str(ca),'-CAkey',str(cak),
            '-CAcreateserial','-out',str(crt),'-days','1','-extfile',str(config),
            '-extensions',ext)
        key.chmod(0o600)
    cak.chmod(0o600)
    return ca


def probe_vless(xray, root, credential, public_key, server_port, origin_port,
                *, expect_working, reality):
    """Send HTTPS to a local origin through SOCKS -> VLESS REALITY -> Xray."""
    socks_port = free_port()
    config={'log':{'loglevel':'warning','access':'none'},
        'inbounds':[{'tag':'local-socks','listen':'127.0.0.1','port':socks_port,
                     'protocol':'socks','settings':{'auth':'noauth','udp':False}}],
        'outbounds':[{'tag':'remote','protocol':'vless','settings':{'vnext':[
            {'address':'127.0.0.1','port':server_port,'users':[
                {'id':str(credential),'encryption':'none',
                 'flow':'xtls-rprx-vision' if reality else ''}]}]},
            'streamSettings':{'network':'tcp','security':'reality' if reality else 'none','realitySettings':{
                'serverName':'www.cloudflare.com','fingerprint':'chrome',
                'password':public_key,'shortId':'abcd'}}}]}
    file=root/'client.json';file.write_text(json.dumps(config));file.chmod(0o600)
    run(xray,'run','-test','-c',str(file))
    client_log=open(root/'client.log','wb')
    client=subprocess.Popen([xray,'run','-c',str(file)],
        stdout=client_log,stderr=client_log)
    try:
        for _ in range(30):
            with socket.socket() as sock:
                if sock.connect_ex(('127.0.0.1',socks_port)) == 0:break
            time.sleep(.1)
        # Explicit proxy, cleared no-proxy list. No freedom/direct outbound exists
        # on the test client; a successful response must cross VLESS.
        result=subprocess.run(['curl','--silent','--show-error','--insecure','--max-time','18',
            '--connect-timeout','12','--noproxy','','--socks5-hostname',
            f'127.0.0.1:{socks_port}','--output','/dev/null',
            '--write-out','%{http_code} %{time_connect} %{time_appconnect} %{time_total}',
            f'https://127.0.0.1:{origin_port}/'],
            stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,timeout=20)
        fields=result.stdout.split()
        if os.getenv('TEST_XRAY_TIMINGS'):
            # Only numeric timing metadata; never print the URL or credentials.
            print('REALITY' if reality else 'plain VLESS',
                  'curl status', result.returncode, 'HTTP', fields[0] if fields else 'none',
                  'connect/appconnect/total', ' '.join(fields[1:4]))
        if expect_working:
            assert result.returncode==0 and fields and fields[0]=='200', (
                'Local VLESS REALITY data path did not deliver HTTPS')
        else:
            assert result.returncode!=0 and fields and fields[0]=='000', (
                'Revoked credential unexpectedly received an HTTP response')
    finally:
        for process in (client,):
            process.terminate()
            try:process.wait(timeout=3)
            except subprocess.TimeoutExpired:process.kill();process.wait()
        file.unlink(missing_ok=True)
        client_log.close()


async def wait_tcp(port):
    for _ in range(40):
        try:
            reader,writer=await asyncio.open_connection('127.0.0.1',port)
            writer.close();await writer.wait_closed();return
        except OSError:await asyncio.sleep(.1)
    raise AssertionError('Local test Xray did not listen')


@pytest.mark.skipif(not os.environ.get('TEST_XRAY_BINARY'),reason='Trusted local Xray binary not configured')
@pytest.mark.parametrize('reality',[False,True],ids=['plain-vless-data-plane','reality-vision-management'])
def test_real_xray_node_lifecycle(tmp_path,reality):
    xray=os.environ['TEST_XRAY_BINARY']
    async def scenario():
        api_port,vless_port,agent_port,target_port=free_port(),free_port(),free_port(),free_port()
        ca=make_certs(tmp_path)
        tls_target = ('import http.server,ssl,sys;'
            's=http.server.HTTPServer(("127.0.0.1",int(sys.argv[1])),http.server.SimpleHTTPRequestHandler);'
            'c=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);'
            'c.load_cert_chain(sys.argv[2],sys.argv[3]);'
            's.socket=c.wrap_socket(s.socket,server_side=True);s.serve_forever()')
        target=subprocess.Popen(['python3','-c',tls_target,str(target_port),
            str(tmp_path/'server.crt'),str(tmp_path/'server.key')],
            stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        await wait_tcp(target_port)
        assert run('curl','-k','--silent','--max-time','3','--output','/dev/null',
                   '--write-out','%{http_code}',f'https://127.0.0.1:{target_port}/') == '200'
        fields=dict(line.split(':',1) for line in run(xray,'x25519').splitlines() if ':' in line)
        private=fields['PrivateKey'].strip()
        public=fields.get('Password (PublicKey)',fields.get('PublicKey','')).strip()
        assert len(public)==43
        config={'log':{'loglevel':'warning','access':'none'},
            'api':{'tag':'api','listen':f'127.0.0.1:{api_port}','services':['HandlerService']},
            'inbounds':[{'tag':'kuda-test','listen':'127.0.0.1','port':vless_port,
                'protocol':'vless','settings':{'decryption':'none','users':[]},
                'streamSettings':{'network':'tcp','security':'reality' if reality else 'none','realitySettings':{
                    'dest':'www.cloudflare.com:443','serverNames':['www.cloudflare.com'],
                    'privateKey':private,'shortIds':['abcd']}}}],
            'outbounds':[{'tag':'direct','protocol':'freedom'}]}
        config_file=tmp_path/'xray.json';config_file.write_text(json.dumps(config));config_file.chmod(0o600)
        run(xray,'run','-test','-c',str(config_file))
        server_log=open(tmp_path/'server.log','wb')
        proc=subprocess.Popen([xray,'run','-c',str(config_file)],stdout=server_log,stderr=server_log)
        runner=None;engine=None;store=None
        try:
            await wait_tcp(api_port)
            cli=XrayCLI(xray,f'127.0.0.1:{api_port}','kuda-test',vless_port,
                         flow='xtls-rprx-vision' if reality else '')
            store=LeaseStore(str(tmp_path/'state'/'leases.db'))
            agent=NodeAgent(store,cli)
            context=ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
            context.verify_mode=ssl.CERT_REQUIRED
            context.load_cert_chain(str(tmp_path/'server.crt'),str(tmp_path/'server.key'))
            context.load_verify_locations(cafile=str(ca))
            runner=web.AppRunner(create_app(agent),access_log=None)
            await runner.setup()
            await web.TCPSite(runner,'127.0.0.1',agent_port,ssl_context=context).start()
            engine=create_async_engine('sqlite+aiosqlite:///'+str(tmp_path/'business.db'))
            async with engine.begin() as conn:await conn.run_sync(Base.metadata.create_all)
            sessions=async_sessionmaker(engine,expire_on_commit=False)
            await UserService(sessions).register(101,'Test')
            async with sessions.begin() as session:
                node=VPNNode(name='Local REALITY',hostname='127.0.0.1',port=vless_port,
                    public_key=public,short_id='abcd',server_name='www.cloudflare.com',enabled=True)
                session.add(node);await session.flush();node_id=node.id
            entry={str(node_id):{'url':f'https://localhost:{agent_port}',
                'ca':str(ca),'cert':str(tmp_path/'client.crt'),'key':str(tmp_path/'client.key')}}
            path=tmp_path/'agents.json';path.write_text(json.dumps(entry));path.chmod(0o600)
            gateway=HTTPSNodeGateway(str(path));reconciler=NodeReconciler(sessions,gateway)
            # The agent must reject a client without a trusted certificate.
            untrusted=ssl.create_default_context(cafile=str(ca))
            with pytest.raises(aiohttp.ClientError):
                async with aiohttp.ClientSession(trust_env=False,
                    timeout=aiohttp.ClientTimeout(total=2)) as session:
                    await session.put(f'https://localhost:{agent_port}/v1/access',
                        json={'credential':str(uuid4()),'revision':1,'enabled':False,
                              'expires_at':None},ssl=untrusted)
            happ=HappService(sessions,'https://subscriptions.example')
            p=await PaymentRepository(sessions).create_pending(101,TARIFFS['month'],uuid4())
            lifecycle=SubscriptionService(sessions,TestVerifier())
            first_event=ConfirmedPayment(p.id,'yookassa',str(uuid4()),29900,'RUB',utcnow())
            sub=await lifecycle.apply_confirmed_payment(first_event)
            # Payment activates the node even when the Telegram/Happ link has
            # not yet been delivered.
            await reconciler.reconcile(sub.id)
            await NodeHealthMonitor(sessions,gateway).tick()
            async with sessions() as session:
                access=await session.scalar(select(NodeAccess));credential=access.credential
            assert cli.email(credential) in await cli.managed_users()
            link=await happ.issue(sub.id);token=link.rsplit('/',1)[1]
            assert str(credential) in (await happ.feed(token))[0]
            probe_vless(xray,tmp_path,credential,public,vless_port,target_port,
                        expect_working=True,reality=reality)
            # Duplicate event must not renew or generate another UUID.
            prior=sub.expires_at
            again=await lifecycle.apply_confirmed_payment(first_event)
            assert as_utc(again.expires_at)==as_utc(prior)
            p2=await PaymentRepository(sessions).create_pending(101,TARIFFS['quarter'],uuid4())
            await lifecycle.apply_confirmed_payment(ConfirmedPayment(
                p2.id,'yookassa',str(uuid4()),79900,'RUB',utcnow()))
            await reconciler.reconcile(sub.id)
            async with sessions() as session:
                access2=await session.scalar(select(NodeAccess))
                assert access2.credential==credential
            assert cli.email(credential) in await cli.managed_users()
            await lifecycle.set_status(sub.id,'blocked')
            with pytest.raises(AccessDenied):await happ.feed(token)
            with pytest.raises(AccessDenied):await happ.issue(sub.id)
            await reconciler.reconcile(sub.id)
            assert cli.email(credential) not in await cli.managed_users()
            probe_vless(xray,tmp_path,credential,public,vless_port,target_port,
                        expect_working=False,reality=reality)
            # Unavailable Xray: persist intent, recover with same UUID/revision.
            proc.terminate();proc.wait(timeout=4)
            async with sessions.begin() as session:
                row=await session.get(Subscription,sub.id);row.status='expired'
            p3=await PaymentRepository(sessions).create_pending(101,TARIFFS['month'],uuid4())
            await lifecycle.apply_confirmed_payment(ConfirmedPayment(
                p3.id,'yookassa',str(uuid4()),29900,'RUB',utcnow()))
            with pytest.raises((NodeGatewayError,RuntimeError)):
                await reconciler.reconcile(sub.id)
            proc=subprocess.Popen([xray,'run','-c',str(config_file)],stdout=server_log,stderr=server_log)
            await wait_tcp(api_port)
            # A failed enforcement marks the agent unhealthy until its process
            # restarts; model the restart instead of reusing the failed object.
            await runner.cleanup()
            agent=NodeAgent(store,cli)
            runner=web.AppRunner(create_app(agent),access_log=None)
            await runner.setup()
            await web.TCPSite(runner,'127.0.0.1',agent_port,ssl_context=context).start()
            await agent.reconcile()
            await reconciler.reconcile(sub.id)
            assert cli.email(credential) in await cli.managed_users()
            await happ.revoke(sub.id)
            await reconciler.reconcile(sub.id)
            assert cli.email(credential) not in await cli.managed_users()
            with pytest.raises(AccessDenied):await happ.feed(token)
            # The node enforces expiry independently of the central database.
            isolated=uuid4()
            now=int(time.time())
            await agent.apply(isolated,1,True,now+2)
            assert cli.email(isolated) in await cli.managed_users()
            await asyncio.sleep(2.1)
            await agent.reconcile()
            assert cli.email(isolated) not in await cli.managed_users()
        finally:
            if runner:await runner.cleanup()
            if engine:await engine.dispose()
            if store:store.close()
            if proc.poll() is None:
                proc.terminate()
                try:proc.wait(timeout=4)
                except subprocess.TimeoutExpired:proc.kill();proc.wait()
            server_log.close()
            target.terminate()
            try:target.wait(timeout=3)
            except subprocess.TimeoutExpired:target.kill();target.wait()
            shutil.rmtree(tmp_path, ignore_errors=True)
    asyncio.run(scenario())
