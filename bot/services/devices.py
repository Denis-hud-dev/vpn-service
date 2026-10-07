from sqlalchemy import select
from bot.db.models import Device, User


EMPTY_DEVICES = "📱 Ваши устройства\n\nУ вас пока нет подключённых устройств.\n\nПосле покупки подписки здесь можно будет подключить:\n• iPhone / iPad\n• Android\n• Windows\n• macOS"


class DemoDeviceService:
    async def describe(self, telegram_id: int) -> str:
        return EMPTY_DEVICES

    async def connection_message(self, telegram_id: int) -> str:
        return "Для подключения устройства нужна активная подписка. VPN-сервер скоро будет подключён."


class DeviceService:
    def __init__(self, sessions, vpn):
        self.sessions, self.vpn = sessions, vpn

    async def describe(self, telegram_id: int) -> str:
        async with self.sessions() as session:
            devices = (await session.scalars(select(Device).join(User).where(
                User.telegram_id == telegram_id, Device.status != "revoked").order_by(Device.created_at))).all()
            if not devices:
                return EMPTY_DEVICES
            labels = {"pending": "ожидает подключения", "active": "активно"}
            return "📱 Ваши устройства\n\n" + "\n".join(f"• {d.name} ({d.platform}) — {labels[d.status]}" for d in devices)

    async def connection_message(self, telegram_id: int) -> str:
        return await self.vpn.connection_message(telegram_id)
