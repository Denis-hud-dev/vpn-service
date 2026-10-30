"""Public destinations for the opt-in TEST Premium menu; no checkout links."""
from dataclasses import dataclass
from urllib.parse import urlsplit

REVIEW_WEBSITE = "https://r1276248.s2578.vm.mydomain.zone:20443"


@dataclass(frozen=True)
class MenuLinks:
    website: str = REVIEW_WEBSITE
    channel: str = ""
    community: str = ""

    def __post_init__(self):
        for name in ("website", "channel", "community"):
            value = getattr(self, name)
            if not value and name != "website":
                continue
            try:
                url = urlsplit(value)
                valid = (url.scheme == "https" and bool(url.hostname)
                         and url.username is None and url.password is None
                         and not url.query and not url.fragment
                         and not any(c.isspace() or ord(c) < 32 for c in value)
                         and "\\" not in value)
                port = url.port
                if name == "website":
                    valid = valid and url.path in ("", "/")
                else:
                    valid = (valid and url.hostname == "t.me" and port is None
                             and url.path not in ("", "/"))
            except ValueError:
                valid = False
            if not valid:
                # Never echo a malformed configuration URL into logs.
                raise ValueError("Invalid public menu " + name + " URL")
