"""Bounded periodic expiry; exceptions do not silently kill the worker."""
import asyncio
import logging
logger = logging.getLogger(__name__)


async def expiry_worker(service, interval=60):
    while True:
        try:
            await service.expire_due()
        except Exception as exc:
            logger.warning("Subscription expiry failed: %s", type(exc).__name__)
        await asyncio.sleep(interval)
