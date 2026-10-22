"""Isolated Happ header prototype. The live feed does not call this module.

Happ documents lowest-delay selection at app launch only with a Provider ID.
This helper is intentionally opt-in and must not be wired into subscriptions
before a two-node device test and explicit approval of Provider ID data sharing.
"""


class SmartPrototypeUnavailable(ValueError):
    pass


def happ_launch_selection_headers(*, provider_id: str | None,
                                  healthy_nodes: int) -> dict[str, str]:
    """Build documented opt-in headers for an isolated, two-node fixture."""
    if not provider_id or not provider_id.isascii() or not provider_id.isalnum():
        raise SmartPrototypeUnavailable("A valid Happ Provider ID is required")
    if healthy_nodes < 2:
        raise SmartPrototypeUnavailable("Two independent healthy nodes are required")
    return {
        "providerid": provider_id,
        "subscription-ping-onopen-enabled": "1",
        "subscription-autoconnect": "1",
        "subscription-autoconnect-type": "lowestdelay",
    }
