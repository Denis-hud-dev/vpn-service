"""Poll pending test-shop orders; provider GET, never a browser redirect, grants access."""
import asyncio
import logging

from bot.services.payments import PaymentRepository
from bot.services.payment_notifications import PaymentNotificationProcessor

logger = logging.getLogger(__name__)


class TestPaymentPoller:
    def __init__(self, sessions, lookup):
        self.repository = PaymentRepository(sessions)
        self.processor = PaymentNotificationProcessor(sessions, lookup)

    async def tick(self) -> None:
        for provider_id in await self.repository.pending_provider_ids():
            try:
                await self.processor.process(provider_id)
            except Exception as exc:
                # No payment IDs, provider payloads, URLs or credentials in logs.
                logger.warning('test_payment_poll outcome=retry error=%s', type(exc).__name__)


async def run_test_payment_poller(poller: TestPaymentPoller) -> None:
    while True:
        try:
            await poller.tick()
        except Exception as exc:
            logger.warning('test_payment_poll outcome=retry error=%s', type(exc).__name__)
        await asyncio.sleep(30)
