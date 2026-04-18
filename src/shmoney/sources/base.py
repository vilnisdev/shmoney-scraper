from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Iterator, Optional


@dataclass
class RawBusiness:
    source: str
    name: str
    address: str
    phone: Optional[str] = None
    website: Optional[str] = None
    neighborhood: Optional[str] = None
    business_type: Optional[str] = None
    yelp_or_google_listing: Optional[str] = None
    review_count: Optional[int] = None
    owner_name: Optional[str] = None
    registered_at: Optional[str] = None
    raw: dict[str, Any] = field(default_factory=dict)


class SourceAdapter(ABC):
    source_name: str = ""

    @abstractmethod
    def iter_businesses(self) -> Iterator[RawBusiness]:
        raise NotImplementedError
