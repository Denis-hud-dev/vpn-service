"""Service boundaries: replace demo implementations with backend adapters."""
from dataclasses import dataclass
from typing import Protocol


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


@dataclass
class Services:
    payments: PaymentService
    subscriptions: DemoSubscriptionService
    referrals: MemoryReferralService

    @classmethod
    def demo(cls) -> "Services":
        return cls(DemoPaymentService(), DemoSubscriptionService(), MemoryReferralService())
