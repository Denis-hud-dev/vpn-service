"""Multi-account presentation over the existing payment and Happ lifecycle."""
from bot.services.subscriptions import SubscriptionService
from bot.services.happ import AccessDenied
from bot.services.web_checkout import WebCheckout
from bot.services.npd_receipts import NPDLedger  # Backwards-compatible import.


class WebsiteCheckout(WebCheckout):
    def _owner(self, user_id):
        # Website HTTP supplies this exclusively from a verified server session.
        # Existing order queries still join Payment -> User and match this owner.
        if type(user_id) is not int or user_id <= 0:
            raise PermissionError('Authentication required')

    async def account(self, owner):
        snapshot = await SubscriptionService(self.sessions).snapshot(owner)
        result = {'status': snapshot.status, 'tariff': snapshot.tariff_label,
            'expires_at': snapshot.expires_at.isoformat() if snapshot.expires_at else None,
            'nodes': [], 'access_pending': False}
        if snapshot.status == 'active':
            try:
                result['nodes'] = list(await self.happ.available_node_names_for_user(owner))
                link = await self.happ.issue_for_user(owner)
                result['connect_url'] = self.happ.connection_page_url(link)
            except (AccessDenied, PermissionError, OSError, RuntimeError, TimeoutError):
                result['access_pending'] = True
        return result

    async def connection(self, owner):
        """The HTTP layer supplies the owner exclusively from its session."""
        self._owner(owner)
        snapshot = await SubscriptionService(self.sessions).snapshot(owner)
        if snapshot.status != 'active' or snapshot.expires_at is None:
            raise PermissionError('Active subscription required')
        # Existing idempotent Happ service; never create another subscription.
        return await self.happ.issue_for_user(owner), int(snapshot.expires_at.timestamp())
