"""Fail-closed WireGuard boundary. No subprocess, SSH or real provisioning."""
from typing import Protocol
from uuid import UUID
from sqlalchemy import select
from bot.db.models import Subscription, User, VPNServer
from bot.services.subscriptions import as_utc, utcnow


class WireGuardGateway(Protocol):
    async def add_peer(self, server: VPNServer, public_key: str, assigned_ip: str) -> None: ...
    async def remove_peer(self, server: VPNServer, public_key: str) -> None: ...


class DisabledWireGuardGateway:
    async def add_peer(self, server: VPNServer, public_key: str, assigned_ip: str) -> None:
        raise RuntimeError("WireGuard-сервер пока не настроен")

    async def remove_peer(self, server: VPNServer, public_key: str) -> None:
        raise RuntimeError("WireGuard-сервер пока не настроен")


class VPNService:
    def __init__(self, sessions, gateway: WireGuardGateway | None = None):
        self.sessions = sessions
        self.gateway = gateway or DisabledWireGuardGateway()

    async def connection_message(self, telegram_id: int) -> str:
        async with self.sessions() as session:
            sub = await session.scalar(select(Subscription).join(User).where(User.telegram_id == telegram_id))
            if sub is None or sub.status != "active" or not sub.expires_at or as_utc(sub.expires_at) <= utcnow():
                return "Для подключения устройства нужна активная подписка. VPN-сервер скоро будет подключён."
            server = await session.scalar(select(VPNServer).where(VPNServer.enabled.is_(True)))
            if server is None:
                return "Подписка активна. VPN-сервер пока не настроен; доступ ещё не выдаётся."
            return "Выдача WireGuard-конфигураций пока не подключена. Обратитесь в поддержку."

    async def provision_device(self, telegram_id: int, platform: str) -> UUID:
        # Even an enabled DB row cannot accidentally create real access in stage 2.
        raise RuntimeError("Выдача VPN-доступа ещё не реализована")
