"""Channel gates, independent of provider adapters and subscription writes.

Deployment switches are not permission to bypass platform policy or pretend a
provider capability exists. This module never establishes a caller's identity.
"""
from dataclasses import dataclass
from enum import Enum


class Channel(str,Enum):
    STARS='stars'
    CARD='card'
    SBP='sbp'


class Surface(str,Enum):
    TELEGRAM='telegram'
    WEBSITE='website'


@dataclass(frozen=True)
class ChannelFlags:
    stars: bool=False
    card: bool=False
    sbp: bool=False


@dataclass(frozen=True)
class ChannelDecision:
    allowed: bool
    reason: str


@dataclass(frozen=True)
class ChannelPolicy:
    flags: ChannelFlags=ChannelFlags()
    # These are trusted backend configuration, never callback/query values.
    telegram_test_transport_ready: bool=False
    canonical_xtr_ledger_ready: bool=False
    yookassa_test_shop: bool=True
    yookassa_sbp_confirmed: bool=False

    def decision(self,channel: Channel,surface: Surface):
        channel,surface=Channel(channel),Surface(surface)
        if surface==Surface.TELEGRAM and channel!=Channel.STARS:
            return ChannelDecision(False,'telegram_requires_stars')
        if channel==Channel.STARS and surface!=Surface.TELEGRAM:
            return ChannelDecision(False,'native_telegram_only')
        if getattr(self.flags,channel.value) is not True:
            return ChannelDecision(False,'channel_disabled')
        if channel==Channel.STARS:
            if not self.telegram_test_transport_ready:
                return ChannelDecision(False,'telegram_test_bot_required')
            if not self.canonical_xtr_ledger_ready:
                return ChannelDecision(False,'canonical_xtr_ledger_required')
        if channel==Channel.SBP:
            if self.yookassa_test_shop:
                return ChannelDecision(False,'sbp_unavailable_in_test_shop')
            if not self.yookassa_sbp_confirmed:
                return ChannelDecision(False,'sbp_not_confirmed')
        return ChannelDecision(True,'eligible')

    def require(self,channel,surface):
        result=self.decision(channel,surface)
        if not result.allowed:
            raise PermissionError(result.reason)


def website_methods(policy: ChannelPolicy, *, read_only=False):
    """Display only card/SBP; final method is selected on YooKassa's hosted page.

    This is availability, not a request to force a method or change the stable
    order payload/idempotency key. It never enables a live gateway by itself.
    """
    result=[]
    for channel, name, label in ((Channel.CARD,'bank_card','Банковская карта'),
                                 (Channel.SBP,'sbp','СБП')):
        decision=policy.decision(channel,Surface.WEBSITE)
        result.append({'id':name,'label':label,'available':decision.allowed and not read_only,
                       'reason':'read_only' if read_only else decision.reason})
    return result
