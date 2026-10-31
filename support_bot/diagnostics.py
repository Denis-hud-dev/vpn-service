"""Read-only integration boundary. No credentials, SQL, SSH or HTTP clients."""
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol


@dataclass(frozen=True)
class VerifiedAccount:
    telegram_id: int
    account_ref: str


@dataclass(frozen=True)
class SubscriptionView:
    account_ref: str
    status: str
    expires_at: datetime | None
    tariff: str | None = None


@dataclass(frozen=True)
class ServerView:
    name: str
    status: str  # ok/error/unknown; not a guarantee of device connectivity
    checked_at: datetime


class AccountReader(Protocol):
    async def verified_account(self, telegram_id: int) -> VerifiedAccount | None: ...
    async def subscription(self, account: VerifiedAccount) -> SubscriptionView | None: ...
    async def servers(self, account: VerifiedAccount) -> list[ServerView]: ...


class Diagnostics:
    def __init__(self, reader: AccountReader | None = None):
        self.reader = reader

    async def report(self, authenticated_telegram_id: int) -> str:
        # ID is provided only by the private Telegram update, never URL/text/callback.
        if self.reader is None:
            return ('Диагностика · Нет данных\nПодключение к данным подписок и мониторинга '
                    'пока не настроено. Это не означает, что подписка или сервер неисправны.')
        try:
            account = await self.reader.verified_account(authenticated_telegram_id)
            if not account or account.telegram_id != authenticated_telegram_id:
                return 'Нет данных · подтверждённая привязка аккаунта отсутствует.'
            subscription = await self.reader.subscription(account)
            if subscription is None:
                return 'Подписка не найдена в подтверждённом аккаунте.'
            if subscription.account_ref != account.account_ref:
                return 'Нет данных · проверка принадлежности не пройдена.'
            statuses = {'active':'Активна', 'expired':'Истекла', 'pending':'Ожидает активации', 'blocked':'Доступ ограничен'}
            now = datetime.now(timezone.utc)
            expiry = subscription.expires_at
            if expiry and expiry.tzinfo is None:
                return 'Нет данных · срок действия требует проверки.'
            state = 'expired' if subscription.status == 'active' and expiry and expiry <= now else subscription.status
            lines = ['Диагностика KudaVPN', 'Подписка: '+statuses.get(state,'Статус требует подтверждения'),
                     'Действует до: '+(expiry.astimezone(timezone.utc).strftime('%d.%m.%Y %H:%M UTC') if expiry else 'Нет данных')]
            if subscription.tariff:
                lines.append('Тариф: '+subscription.tariff[:128])
            servers = await self.reader.servers(account)
            lines.append('Серверы по данным мониторинга:')
            if not servers:
                lines.append('Нет данных')
            for server in servers[:10]:
                age = (now-server.checked_at).total_seconds() if server.checked_at.tzinfo else -1
                label = {'ok':'Работает', 'error':'Ошибка'}.get(server.status,'Нет данных') if 0 <= age <= 300 else 'Нет данных · измерение устарело'
                lines.append(server.name[:80]+': '+label)
            lines.append('Проверка сервера не подтверждает работу VPN на вашем устройстве.')
            return '\n'.join(lines)
        except Exception:
            return 'Нет данных · источник диагностики временно недоступен.'
