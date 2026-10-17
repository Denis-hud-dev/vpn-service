"""Operator-only registration of disabled VLESS/REALITY nodes.

The private server key and test user's UUID never belong in this manifest or
the central database. Registration cannot advertise or provision a node.
"""
import base64
import ipaddress
import re
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import and_, or_, select

from bot.db.models import VPNNode


def _dns_name(value: str) -> bool:
    if not isinstance(value, str) or not 1 <= len(value) <= 253:
        return False
    return all(re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?', label)
               for label in value.split('.'))


@dataclass(frozen=True)
class NodeSpec:
    name: str
    hostname: str
    port: int
    public_key: str
    short_id: str
    server_name: str

    @classmethod
    def from_mapping(cls, data: dict) -> 'NodeSpec':
        if not isinstance(data, dict) or set(data) != set(cls.__dataclass_fields__):
            # In particular reject privateKey, UUID, enabled and admin secrets.
            raise ValueError('Invalid public node manifest')
        name, host, port = data['name'], data['hostname'], data['port']
        key, sid, sni = data['public_key'], data['short_id'], data['server_name']
        if (not isinstance(name, str) or not 1 <= len(name) <= 128
                or name != name.strip() or any(ord(c) < 32 for c in name)):
            raise ValueError('Invalid node name')
        if not isinstance(host, str) or host != host.strip():
            raise ValueError('Invalid node address')
        try:
            ipaddress.ip_address(host)
        except ValueError:
            if not _dns_name(host):
                raise ValueError('Invalid node address') from None
        if type(port) is not int or not 1 <= port <= 65535:
            raise ValueError('Invalid node port')
        if not isinstance(key, str) or not re.fullmatch(r'[A-Za-z0-9_-]{43}', key):
            raise ValueError('Invalid REALITY public key')
        try:
            decoded = base64.urlsafe_b64decode(key + '=')
        except (ValueError, base64.binascii.Error):
            decoded = b''
        if len(decoded) != 32 or base64.urlsafe_b64encode(decoded).rstrip(b'=').decode() != key:
            raise ValueError('Invalid REALITY public key')
        if not isinstance(sid, str) or not re.fullmatch(r'(?:[0-9a-fA-F]{2}){1,8}', sid):
            raise ValueError('Invalid REALITY short ID')
        if not _dns_name(sni):
            raise ValueError('Invalid REALITY server name')
        return cls(name, host.lower(), port, key, sid.lower(), sni.lower())


class NodeRegistry:
    def __init__(self, sessions):
        self.sessions = sessions

    async def register_disabled(self, spec: NodeSpec) -> UUID:
        """Add only a disabled row; identical retries are safe.

        An endpoint/name collision with different REALITY metadata is refused
        rather than silently changing a running node or reusing a test account.
        """
        async with self.sessions.begin() as session:
            rows = (await session.scalars(select(VPNNode).where(or_(
                VPNNode.name == spec.name,
                and_(VPNNode.hostname == spec.hostname, VPNNode.port == spec.port)
            )).with_for_update())).all()
            if rows:
                if (len(rows) != 1 or rows[0].enabled or
                        any(getattr(rows[0], field) != getattr(spec, field)
                            for field in NodeSpec.__dataclass_fields__)):
                    raise ValueError('Node registration conflicts with an existing node')
                return rows[0].id
            row = VPNNode(**{field: getattr(spec, field) for field in NodeSpec.__dataclass_fields__},
                          enabled=False)
            session.add(row)
            await session.flush()
            return row.id
