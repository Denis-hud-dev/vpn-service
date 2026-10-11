"""Future Xray reconciler boundary. No network calls or provisioning today."""
from typing import Protocol
from uuid import UUID


class AccessGateway(Protocol):
    async def reconcile(self, subscription_id: UUID) -> None:
        """Read current entitlement, enforce expiry/blocking, and converge node state."""
        ...


class DisabledAccessGateway:
    async def reconcile(self, subscription_id: UUID) -> None:
        raise RuntimeError("Управление Xray-доступом не подключено")
