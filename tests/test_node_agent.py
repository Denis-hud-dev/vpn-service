import asyncio
import time
from uuid import uuid4
import pytest
from bot.node_agent.store import LeaseStore, LeaseConflict
from bot.node_agent.service import NodeAgent


def test_persistent_revisions_and_expiry(tmp_path):
    path=tmp_path/'private'/'leases.db'
    credential=uuid4();now=int(time.time())
    store=LeaseStore(str(path))
    assert path.stat().st_mode & 0o077 == 0
    store.apply(credential,1,True,now+2,now)
    store.apply(credential,1,True,now+2,now)
    with pytest.raises(LeaseConflict):store.apply(credential,1,False,None,now)
    with pytest.raises(LeaseConflict):store.apply(credential,0,False,None,now)
    store.close()
    store=LeaseStore(str(path))
    assert credential in store.desired(now)
    assert credential not in store.desired(now+3)
    store.apply(credential,2,False,None,now)
    with pytest.raises(LeaseConflict):store.apply(credential,1,True,now+10,now)
    assert credential not in store.desired(now)
    store.close()


def test_node_reconciles_orphans_after_restart(tmp_path):
    class Xray:
        def __init__(self):self.users=set()
        @staticmethod
        def email(value):return 'kuda-'+str(value)
        async def managed_users(self):return self.users.copy()
        async def add(self,value):self.users.add(self.email(value))
        async def remove(self,value):self.users.discard(self.email(value))
    async def run():
        store=LeaseStore(str(tmp_path/'leases.db'))
        xray=Xray();old=uuid4();new=uuid4();now=int(time.time())
        xray.users.add(xray.email(old))
        store.apply(new,1,True,now+30,now)
        agent=NodeAgent(store,xray)
        await agent.reconcile()
        assert xray.users=={xray.email(new)}
        listener=await asyncio.start_server(lambda reader,writer: writer.close(),
                                             '127.0.0.1',0)
        xray.port=listener.sockets[0].getsockname()[1]
        assert await agent.health() is True
        listener.close();await listener.wait_closed()
        with pytest.raises(OSError):await agent.health()
        await agent.apply(new,2,False,None)
        assert not xray.users
        store.close()
    asyncio.run(run())
