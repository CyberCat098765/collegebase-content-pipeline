from __future__ import annotations

import hashlib
import re

from src.models import SourceItem


def dedupe_items(items: list[SourceItem]) -> list[SourceItem]:
    seen: set[str] = set()
    unique: list[SourceItem] = []

    for item in items:
        key = item.url.strip().lower() or _fingerprint(item.raw_text)
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)

    return unique


def _fingerprint(text: str) -> str:
    normalized = re.sub(r"\s+", " ", text.lower()).strip()
    return hashlib.sha1(normalized.encode("utf-8")).hexdigest()
