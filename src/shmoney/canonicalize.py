import hashlib
import re

_SUFFIX_RE = re.compile(
    r"\b(llc|l\.l\.c\.|inc|incorporated|co|corp|corporation|ltd|limited)\.?",
    re.IGNORECASE,
)
_NON_ALNUM = re.compile(r"[^a-z0-9 ]+")


def _normalize(s: str) -> str:
    s = (s or "").lower()
    s = _SUFFIX_RE.sub("", s)
    s = _NON_ALNUM.sub(" ", s)
    return " ".join(s.split())


def canonical_key(name: str, address: str) -> str:
    basis = f"{_normalize(name)}|{_normalize(address)}"
    return hashlib.sha1(basis.encode("utf-8")).hexdigest()
