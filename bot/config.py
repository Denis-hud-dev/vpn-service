"""Configuration comes exclusively from environment variables."""
import os
import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    bot_token: str
    support_username: str = ""
    health_port: int = 8080
    database_url: str = ""
    subscription_public_url: str = ""
    happ_link_secret: str = ""
    pilot_trial_owner_id: int | None = None
    pilot_connect_page_enabled: bool = False

    @classmethod
    def from_env(cls) -> "Config":
        token = os.getenv("BOT_TOKEN", "").strip()
        if not token:
            raise ValueError("Укажите BOT_TOKEN в переменных окружения.")
        support = os.getenv("SUPPORT_USERNAME", "").strip().lstrip("@")
        if support and not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{4,31}", support):
            raise ValueError("SUPPORT_USERNAME должен содержать Telegram username без ссылки.")
        port = int(os.getenv("HEALTH_PORT", "8080"))
        if not 1 <= port <= 65535:
            raise ValueError("HEALTH_PORT должен быть от 1 до 65535.")
        public_url = os.getenv("SUBSCRIPTION_PUBLIC_URL", "").strip()
        link_secret = os.getenv("HAPP_LINK_SECRET", "").strip()
        if link_secret and not public_url:
            raise ValueError("Для HAPP_LINK_SECRET нужен SUBSCRIPTION_PUBLIC_URL.")
        trial_owner = (int(os.getenv('PILOT_TRIAL_OWNER_ID'))
                       if os.getenv('KUDAVPN_PILOT_MARKER') == 'kudavpn-telegram-pilot-v1'
                       and os.getenv('PILOT_TRIAL_OWNER_ID', '').isascii()
                       and os.getenv('PILOT_TRIAL_OWNER_ID', '').isdigit() else None)
        connect_page = bool(trial_owner and os.getenv('PILOT_CONNECT_PAGE_ENABLED') == '1')
        return cls(token, support, port, os.getenv("DATABASE_URL", "").strip(), public_url,
                   link_secret, trial_owner, connect_page)
