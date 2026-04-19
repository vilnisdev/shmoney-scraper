from typing import Optional

from .audit import AuditReport
from .classify import Classification


def score(c: Classification, report: Optional[AuditReport]) -> tuple[int, str]:
    if c.kind == "none":
        return 1, "no website"
    if c.kind == "social":
        return 2, f"social-only ({c.platform})"
    # kind == "real"
    if report is None:
        return 3, ""
    f = report.flags
    if not f.reachable:
        return 1, "site broken"
    s = 3
    if f.https or f.redirects_to_https:
        s += 1
    if f.has_viewport and f.body_substantial:
        s += 1
    if not f.response_time_ok:
        s -= 1
    if not f.last_modified_fresh:
        s -= 1
    s = max(1, min(5, s))
    tag = "" if s >= 4 else "weak site"
    return s, tag
