from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from src.reddit_discovery.models import DiscoveryResult


PROVIDER_CHOICES = (
    "auto",
    "public-json",
    "curated",
    "rss",
    "praw",
    "import",
    "manual",
)


@dataclass(slots=True)
class ProviderStatus:
    provider: str
    status: str
    candidate_count: int = 0
    request_count: int = 0
    cache_hit_count: int = 0
    cache_miss_count: int = 0
    status_code: int | None = None
    content_type: str = ""
    error: str = ""
    intended_use: str = "development/calibration"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class ProviderAcquisition:
    discovery: DiscoveryResult = field(default_factory=DiscoveryResult)
    statuses: list[ProviderStatus] = field(default_factory=list)
    request_count: int = 0
    cache_hit_count: int = 0
    cache_miss_count: int = 0
