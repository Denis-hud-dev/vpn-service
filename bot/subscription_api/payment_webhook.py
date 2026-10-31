"""Optional test-shop webhook: a hint to re-read YooKassa, never proof of payment."""
from aiohttp import web

from bot.services.yookassa_api import PaymentProviderUnavailable, PROVIDER_ID


def install_test_payment_webhook(app: web.Application, processor, *, refund_processor=None, inbox=None) -> None:
    async def notification(request: web.Request) -> web.Response:
        try:
            body = await request.json()
        except (ValueError, web.HTTPException):
            return web.Response(status=400)
        if not isinstance(body, dict) or body.get('type') != 'notification':
            return web.Response(status=400)
        event = body.get('event')
        refund = event == 'refund.succeeded' and refund_processor is not None
        if not refund and event not in {'payment.succeeded', 'payment.canceled', 'payment.waiting_for_capture'}:
            return web.Response(status=400)
        obj = body.get('object')
        payment_id = obj.get('id') if isinstance(obj, dict) else None
        if not isinstance(payment_id, str) or not PROVIDER_ID.fullmatch(payment_id):
            return web.Response(status=400)
        parent = obj.get('payment_id') if refund else None
        if refund and (not isinstance(parent, str) or not PROVIDER_ID.fullmatch(parent)):
            return web.Response(status=400)
        try:
            if inbox is not None:
                # Opt-in durable acknowledgment. Persist only IDs, never trust
                # the claimed status/amount. The worker independently GETs it.
                await inbox.accept_hint('refunds' if refund else 'payments', payment_id, parent)
                return web.Response(status=200)
            # The event body is not passed to the processor. It must first find
            # a locally bound order, then independently GET its provider state.
            if refund:
                await refund_processor.process(payment_id, parent)
            else:
                await processor.process(payment_id)
        except PaymentProviderUnavailable:
            return web.Response(status=503)
        except ValueError:
            # Unknown or mismatched IDs cannot grant access. A later poll still
            # catches a legitimate event that raced with local ID binding.
            return web.Response(status=200)
        except Exception:
            return web.Response(status=503)
        return web.Response(status=200)

    app.router.add_post('/yookassa/test-webhook', notification)
