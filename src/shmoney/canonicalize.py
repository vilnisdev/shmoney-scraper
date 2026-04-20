import hashlib
import re

_SUFFIX_RE = re.compile(
    r"\b(llc|l\.l\.c\.|inc|incorporated|co|corp|corporation|ltd|limited)\.?",
    re.IGNORECASE,
)
_NON_ALNUM = re.compile(r"[^a-z0-9 ]+")

_STREET_SUFFIX_MAP = {
    "avenue": "ave",
    "boulevard": "blvd",
    "court": "ct",
    "drive": "dr",
    "highway": "hwy",
    "lane": "ln",
    "parkway": "pkwy",
    "place": "pl",
    "road": "rd",
    "street": "st",
    "suite": "ste",
    "apartment": "apt",
    "number": "",
    "unit": "ste",
}
_ZIP_DASH_RE = re.compile(r"\b(\d{5})-(?=\s|$)")


def _normalize_name(s: str) -> str:
    s = (s or "").lower()
    s = _SUFFIX_RE.sub("", s)
    s = _NON_ALNUM.sub(" ", s)
    return " ".join(s.split())


def _normalize_address(s: str) -> str:
    s = (s or "").lower()
    s = _ZIP_DASH_RE.sub(r"\1", s)
    s = s.replace("#", " ste ")
    s = _NON_ALNUM.sub(" ", s)
    tokens = s.split()
    tokens = [_STREET_SUFFIX_MAP.get(t, t) for t in tokens]
    tokens = [t for t in tokens if t]
    return " ".join(tokens)


def canonical_key(name: str, address: str) -> str:
    basis = f"{_normalize_name(name)}|{_normalize_address(address)}"
    return hashlib.sha1(basis.encode("utf-8")).hexdigest()
