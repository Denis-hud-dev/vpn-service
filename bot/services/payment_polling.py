"""Poll pending test-shop orders; provider GET, never a browser redirect, grants access."""
import asyncio
import logging
import random
from time import monotonic

from bot.services.payments import PaymentRepository
from bot.services.payment_notifications import PaymentNotificationProcessor

logger = logging.getLogger(__name__)


class TestPaymentPoller:
    def __init__(self, sessions, lookup, *, clock=monotonic, jitter=lambda: random.uniform(0, 3)):
        self.repository = PaymentRepository(sessions)
        self.processor = PaymentNotificationProcessor(sessions, lookup)
        self.clock, self.jitter = clock, jitter
        self.retry = {}

    async def tick(self) -> None:
        pending = await self.repository.pending_provider_ids()
        active = set(pending)
        self.retry = {key: value for key, value in self.retry.items() if key in active}
        for provider_id in pending:
            attempt, deadline = self.retry.get(provider_id, (0, 0))
            if deadline > self.clock():
                continue
            try:
                await self.processor.process(provider_id)
                self.retry.pop(provider_id, None)
            except Exception as exc:
                delay = min(300, 30 * 2 ** min(attempt, 4)) + max(0, min(3, self.jitter()))
                self.retry[provider_id] = (attempt + 1, self.clock() + delay)
                # No payment IDs, provider payloads, URLs or credentials in logs.
                logger.warning('test_payment_poll outcome=retry error=%s', type(exc).__name__)


async def run_test_payment_poller(poller: TestPaymentPoller, *, reconciler=None) -> None:
    while True:
        try:
            await poller.tick()
        except Exception as exc:
            logger.warning('test_payment_poll outcome=retry error=%s', type(exc).__name__)
        if reconciler is not None:
            try:
                outcome = await reconciler.tick()
                if outcome.get('discovery_errors'):
                    logger.warning('test_payment_reconcile outcome=discovery_retry')
                if outcome.get('review'):
                    logger.warning('test_payment_reconcile outcome=review_required')
            except Exception:
                logger.warning('test_payment_reconcile outcome=retry')
        await asyncio.sleep(30)
