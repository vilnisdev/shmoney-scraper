from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlparse


@dataclass(frozen=True)
class Classification:
    kind: str  # "none" | "social" | "real"
    platform: Optional[str]  # facebook | instagram | linktree | beacons | None
    url: str


_SOCIAL_HOSTS: dict[str, str] = {
    "facebook.com": "facebook",
    "m.facebook.com": "facebook",
    "fb.com": "facebook",
    "fb.me": "facebook",
    "instagram.com": "instagram",
    "m.instagram.com": "instagram",
    "linktr.ee": "linktree",
    "beacons.ai": "beacons",
}


def _normalize_host(host: str) -> str:
    host = host.lower().strip()
    if host.startswith("www."):
        host = host[4:]
    return host


def classify(url: Optional[str]) -> Classification:
    if not url or not url.strip():
        return Classification(kind="none", platform=None, url="")
    raw = url.strip()
    if "://" not in raw:
        raw = "https://" + raw
    parsed = urlparse(raw)
    host = _normalize_host(parsed.netloc)
    if not host:
        return Classification(kind="none", platform=None, url="")
    platform = _SOCIAL_HOSTS.get(host)
    if platform is not None:
        return Classification(kind="social", platform=platform, url=url.strip())
    return Classification(kind="real", platform=None, url=url.strip())


def consulting_opportunity_tag(c: Classification) -> str:
    if c.kind == "none":
        return "no website"
    if c.kind == "social":
        return f"social-only ({c.platform})"
    return ""
