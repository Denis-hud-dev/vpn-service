"""Fail-closed control-plane health for the Happ server list.

This proves that the node agent can reach its Xray API and listener. It cannot
prove that a client's route from Russia or the node's Internet egress works.
"""
import asyncio
import hashlib
import json
import logging
from sqlalchemy import select
from bot.db.models import NodeHealth, VPNNode
from bot.services.subscriptions import utcnow

HEALTH_TTL_SECONDS = 90
MAX_CONCURRENT_PROBES = 8
logger = logging.getLogger(__name__)


def node_config_digest(node: VPNNode) -> str:
    fields = [node.name, node.hostname, node.port, node.public_key,
              node.short_id, node.server_name, bool(node.enabled)]
    return hashlib.sha256(json.dumps(fields, ensure_ascii=True,
                                      separators=(',', ':')).encode()).hexdigest()


class NodeHealthMonitor:
    def __init__(self, sessions, gateway, *, max_concurrent=MAX_CONCURRENT_PROBES):
        if max_concurrent < 1:
            raise ValueError('Probe concurrency must be positive')
        self.sessions, self.gateway = sessions, gateway
        self.max_concurrent = max_concurrent

    async def tick(self) -> None:
        async with self.sessions() as session:
            nodes = (await session.scalars(select(VPNNode).where(
                VPNNode.enabled.is_(True)).order_by(VPNNode.id))).all()
        semaphore = asyncio.Semaphore(self.max_concurrent)

        async def check(node: VPNNode) -> None:
            async with semaphore:
                signature = node_config_digest(node)
                try:
                    available = bool(await asyncio.wait_for(self.gateway.probe(node), timeout=5))
                except Exception:
                    # The endpoint, certificates and keys are never logged.
                    available = False
                    logger.warning('VPN node health check failed')
                checked_at = utcnow()
                async with self.sessions.begin() as session:
                    current = await session.get(VPNNode, node.id)
                    if current is None or node_config_digest(current) != signature:
                        return  # Configuration changed during the network probe.
                    record = await session.get(NodeHealth, node.id)
                    if record is None:
                        session.add(NodeHealth(node_id=node.id, available=available,
                            checked_at=checked_at, config_digest=signature))
                    else:
                        record.available, record.checked_at = available, checked_at
                        record.config_digest = signature

        await asyncio.gather(*(check(node) for node in nodes))
