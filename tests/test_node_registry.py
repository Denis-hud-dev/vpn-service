"""A new VPN node is staged off, with no credentials or live-side effects."""
import asyncio
import base64
import json
import os
import subprocess
import sys

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bot.db.models import Base, VPNNode, NodeAccess
from bot.services.node_registry import NodeRegistry, NodeSpec


def manifest(**changes):
    data = dict(name='JustHost test', hostname='203.0.113.20', port=8443,
                public_key=base64.urlsafe_b64encode(bytes(range(32))).rstrip(b'=').decode(),
                short_id='a1b2c3d4', server_name='www.cloudflare.com')
    data.update(changes)
    return data


@pytest.mark.parametrize('changes', [
    {'privateKey': 'secret'}, {'id': 'shared-user-uuid'}, {'enabled': True},
    {'hostname': 'https://203.0.113.20'}, {'port': True}, {'port': 0},
    {'public_key': 'not-a-key'}, {'short_id': 'xyz'},
    {'server_name': 'https://www.cloudflare.com'},
])
def test_rejects_unsafe_node_manifest(changes):
    with pytest.raises(ValueError):
        NodeSpec.from_mapping(manifest(**changes))


def test_register_only_disabled_and_idempotent(tmp_path):
    async def run():
        engine = create_async_engine('sqlite+aiosqlite:///' + str(tmp_path / 'registry.db'))
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            sessions = async_sessionmaker(engine, expire_on_commit=False)
            registry = NodeRegistry(sessions)
            spec = NodeSpec.from_mapping(manifest())
            first = await registry.register_disabled(spec)
            assert await registry.register_disabled(spec) == first
            async with sessions() as session:
                nodes = (await session.scalars(select(VPNNode))).all()
                assert len(nodes) == 1 and nodes[0].id == first
                assert nodes[0].port == 8443 and nodes[0].enabled is False
                assert (await session.scalars(select(NodeAccess))).all() == []
            with pytest.raises(ValueError):
                await registry.register_disabled(NodeSpec.from_mapping(manifest(port=9443)))
            with pytest.raises(ValueError):
                await registry.register_disabled(NodeSpec.from_mapping(manifest(name='Other')))
            async with sessions.begin() as session:
                node = await session.get(VPNNode, first)
                node.enabled = True
            with pytest.raises(ValueError):
                await registry.register_disabled(spec)
        finally:
            await engine.dispose()
    asyncio.run(run())


def test_manifest_validation_does_not_access_database(tmp_path):
    path = tmp_path / 'node.json'
    path.write_text(json.dumps(manifest()))
    path.chmod(0o600)
    env = dict(os.environ, DATABASE_URL='invalid-production-like-value')
    result = subprocess.run([sys.executable, '-m', 'scripts.register_vpn_node', str(path)],
                            text=True, capture_output=True, env=env, timeout=10)
    assert result.returncode == 0
    assert 'no database or server change' in result.stdout
    assert manifest()['public_key'] not in result.stdout + result.stderr
    path.chmod(0o644)
    result = subprocess.run([sys.executable, '-m', 'scripts.register_vpn_node', str(path)],
                            text=True, capture_output=True, env=env, timeout=10)
    assert result.returncode != 0
    assert '0600' in result.stderr
