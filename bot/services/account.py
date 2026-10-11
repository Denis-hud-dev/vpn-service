"""Structured read model: None means unavailable, never fabricated zero."""
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class AccountSnapshot:
    status: str = "inactive"
    tariff_label: str | None = None
    expires_at: datetime | None = None
    device_count: int | None = None
    traffic_bytes: int | None = None
