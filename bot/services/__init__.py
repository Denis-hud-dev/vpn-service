"""Service boundaries: replace demo implementations with backend adapters."""
from dataclasses import dataclass
from typing import Protocol
from bot.services.account import AccountSnapshot


@dataclass(frozen=True)
class Tariff:
    id: str
    months: int
    price_rub: int
    label: str


TARIFFS = {
    "month": Tariff("month", 1, 299, "1 месяц — 299 ₽"),
    "quarter": Tariff("quarter", 3, 799, "3 месяца — 799 ₽"),
    "year": Tariff("year", 12, 2490, "12 месяцев — 2490 ₽"),
}


class PaymentService(Protocol):
    async def create_payment(self, user_id: int, tariff: Tariff) -> str: ...


class DemoPaymentService:
    async def create_payment(self, user_id: int, tariff: Tariff) -> str:
        return f"Платёжная система скоро будет подключена.\nВы выбрали: {tariff.label}"


class DemoSubscriptionService:
    async def snapshot(self, user_id: int) -> AccountSnapshot:
        return AccountSnapshot()

    async def describe(self, user_id: int) -> str:
        return "👤 Ваша подписка\n\nСтатус: не активна\nТариф: —\nДействует до: —\nУстройства: 0"


class MemoryReferralService:
    """Temporary first-touch attribution; replace with a PostgreSQL repository.

    Restart clears records. Self-referrals and repeated attribution are ignored.
    """
    def __init__(self):
        self.referrers: dict[int, int | None] = {}

    async def register(self, user_id: int, referrer_id: int | None) -> None:
        if referrer_id == user_id:
            referrer_id = None
        self.referrers.setdefault(user_id, referrer_id)


def parse_referrer(payload: str | None) -> int | None:
    if not payload or not payload.startswith("ref_"):
        return None
    value = payload[4:]
    if not value.isascii() or not value.isdigit() or len(value) > 20:
        return None
    result = int(value)
    return result if result > 0 else None


class SubscriptionReader(Protocol):
    async def snapshot(self, user_id: int) -> AccountSnapshot: ...
    async def describe(self, user_id: int) -> str: ...


class UserRegistry(Protocol):
    async def register(self, telegram_id: int, first_name: str, username: str | None = None,
                       last_name: str | None = None, referrer_id: int | None = None): ...


class DeviceReader(Protocol):
    async def describe(self, telegram_id: int) -> str: ...
    async def connection_message(self, telegram_id: int) -> str: ...


class HappLinkIssuer(Protocol):
    async def issue_for_user(self, telegram_id: int) -> str: ...


@dataclass
class Services:
    payments: PaymentService
    subscriptions: SubscriptionReader
    referrals: MemoryReferralService

    users: UserRegistry | None = None
    devices: DeviceReader | None = None
    happ: HappLinkIssuer | None = None

    @classmethod
    def demo(cls) -> "Services":
        from bot.services.devices import DemoDeviceService
        return cls(DemoPaymentService(), DemoSubscriptionService(), MemoryReferralService(), devices=DemoDeviceService())


    @classmethod
    def persistent(cls, sessions, *, subscription_public_url: str = "", happ_link_secret: str = "",
                   trial_owner_id: int | None = None) -> "Services":
        from bot.services.users import UserService
        from bot.services.subscriptions import SubscriptionService
        from bot.services.devices import DeviceService
        from bot.services.vpn import VPNService
        # Payment selection remains a demo; no automatic charge or activation.
        from bot.services.happ import HappService
        happ = (HappService(sessions, subscription_public_url, link_key=happ_link_secret,
                            trial_owner_id=trial_owner_id,
                            trial_node_port=10443 if trial_owner_id else None)
                if subscription_public_url and happ_link_secret else None)
        return cls(DemoPaymentService(), SubscriptionService(sessions), MemoryReferralService(),
                   UserService(sessions), DeviceService(sessions, VPNService(sessions)), happ)
