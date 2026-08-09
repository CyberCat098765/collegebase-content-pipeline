from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from typing import Any, Mapping
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from src.processing.cleaner import clean_text, clean_title
from src.reddit_discovery.constants import SUPPORTED_SUBREDDIT
from src.reddit_discovery.discovery import absolute_reddit_url, canonicalize_url
from src.reddit_discovery.models import RedditCandidate


ATOM_NAMESPACE = "http://www.w3.org/2005/Atom"
ATOM = {"atom": ATOM_NAMESPACE}
POST_PATH_PATTERN = re.compile(
    r"^/r/(?P<subreddit>[^/]+)/comments/(?P<post_id>[a-z0-9]+)(?:/|$)",
    re.IGNORECASE,
)


def candidate_from_public_json(
    data: Mapping[str, Any],
    *,
    discovered_by: str,
    retrieved_at: str,
    response_url: str,
) -> RedditCandidate:
    post_id = _text(data.get("id"))
    if not post_id:
        raise ValueError("Reddit JSON post id is missing.")
    subreddit = _text(data.get("subreddit"))
    if subreddit.casefold() != SUPPORTED_SUBREDDIT.casefold():
        raise ValueError("Reddit JSON post is outside r/ApplyingToCollege.")

    permalink = absolute_reddit_url(_text(data.get("permalink")))
    if not permalink:
        permalink = canonicalize_url(
            f"https://www.reddit.com/r/{SUPPORTED_SUBREDDIT}/comments/{post_id}"
        )
    destination = canonicalize_url(_text(data.get("url")))
    external_url = destination if destination and not is_reddit_url(destination) else None
    missing = [
        field
        for field in ("author", "created_utc", "score", "upvote_ratio", "num_comments")
        if data.get(field) is None
    ]

    return RedditCandidate(
        reddit_post_id=post_id,
        fullname=_text(data.get("name")) or f"t3_{post_id}",
        subreddit=SUPPORTED_SUBREDDIT,
        title=clean_title(_text(data.get("title"))),
        selftext=clean_text(_text(data.get("selftext"))),
        canonical_url=permalink,
        permalink=permalink,
        author_name=_author(_text(data.get("author"))),
        created_utc=_optional_float(data.get("created_utc")),
        retrieved_at=retrieved_at,
        score=_optional_integer(data.get("score")),
        upvote_ratio=_optional_float(data.get("upvote_ratio")),
        num_comments=_optional_integer(data.get("num_comments")),
        link_flair_text=_optional_text(data.get("link_flair_text")),
        is_self=bool(data.get("is_self", False)),
        is_original_content=bool(data.get("is_original_content", False)),
        over_18=bool(data.get("over_18", False)),
        spoiler=bool(data.get("spoiler", False)),
        stickied=bool(data.get("stickied", False)),
        distinguished=_optional_text(data.get("distinguished")),
        locked=bool(data.get("locked", False)),
        archived=bool(data.get("archived", False)),
        removed_by_category=_optional_text(data.get("removed_by_category")),
        acquisition_method="public_json",
        provenance={
            "provider": "public_json",
            "response_url": response_url,
            "missing_optional_fields": missing,
        },
        discovered_by=[discovered_by],
        external_url=external_url,
    )


def candidate_from_atom_entry(
    entry: ET.Element,
    *,
    discovered_by: str,
    retrieved_at: str,
    feed_url: str,
    acquisition_method: str,
    extra_provenance: Mapping[str, Any] | None = None,
) -> RedditCandidate:
    entry_id = _entry_text(entry, "id")
    if not entry_id.startswith("t3_"):
        raise ValueError("Atom entry is not a Reddit post.")
    post_id = entry_id.removeprefix("t3_").strip()
    if not post_id:
        raise ValueError("Atom post id is missing.")

    content_html = _entry_text(entry, "content")
    soup = BeautifulSoup(content_html, "html.parser")
    permalink = _post_permalink(soup, post_id)
    parsed = parse_reddit_post_url(permalink)
    if parsed is None or parsed[0].casefold() != SUPPORTED_SUBREDDIT.casefold():
        raise ValueError("Atom entry is outside r/ApplyingToCollege.")
    external_url = _external_link(soup)
    atom_author = _entry_text(entry, "author/name")
    updated = _entry_text(entry, "updated")
    provenance = {
        "provider": acquisition_method,
        "feed_url": feed_url,
        "atom_entry_id": entry_id,
        "atom_updated": updated,
        "engagement_fields_available": False,
        "missing_optional_fields": [
            "score",
            "upvote_ratio",
            "num_comments",
            "link_flair_text",
            "created_utc",
        ],
    }
    if extra_provenance:
        provenance.update(dict(extra_provenance))

    return RedditCandidate(
        reddit_post_id=post_id,
        fullname=entry_id,
        subreddit=SUPPORTED_SUBREDDIT,
        title=clean_title(_entry_text(entry, "title")),
        selftext=clean_text(_post_body(soup)),
        canonical_url=permalink,
        permalink=permalink,
        author_name=_author(atom_author),
        created_utc=None,
        retrieved_at=retrieved_at,
        score=None,
        upvote_ratio=None,
        num_comments=None,
        link_flair_text=None,
        is_self=external_url is None,
        is_original_content=False,
        over_18=False,
        spoiler=False,
        stickied=False,
        distinguished=None,
        locked=False,
        archived=False,
        removed_by_category=None,
        acquisition_method=acquisition_method,
        provenance=provenance,
        discovered_by=[discovered_by],
        external_url=external_url,
    )


def parse_reddit_post_url(value: str) -> tuple[str, str, str] | None:
    canonical = canonicalize_url(value)
    parsed = urlparse(canonical)
    if not is_reddit_url(canonical):
        return None
    match = POST_PATH_PATTERN.match(parsed.path)
    if match is None:
        return None
    return match.group("subreddit"), match.group("post_id"), canonical


def is_reddit_url(value: str) -> bool:
    host = (urlparse(value).hostname or "").casefold()
    return host == "reddit.com" or host.endswith(".reddit.com") or host == "redd.it"


def _entry_text(entry: ET.Element, path: str) -> str:
    element = entry.find("/".join(f"atom:{part}" for part in path.split("/")), ATOM)
    return "" if element is None or element.text is None else element.text.strip()


def _post_permalink(soup: BeautifulSoup, post_id: str) -> str:
    fallback = ""
    for anchor in soup.select("a[href]"):
        href = absolute_reddit_url(_text(anchor.get("href")))
        parsed = parse_reddit_post_url(href)
        if parsed is None or parsed[1].casefold() != post_id.casefold():
            continue
        if anchor.get_text(" ", strip=True).casefold() == "[comments]":
            return parsed[2]
        fallback = fallback or parsed[2]
    return fallback or canonicalize_url(
        f"https://www.reddit.com/r/{SUPPORTED_SUBREDDIT}/comments/{post_id}"
    )


def _external_link(soup: BeautifulSoup) -> str | None:
    for anchor in soup.select("a[href]"):
        if anchor.get_text(" ", strip=True).casefold() != "[link]":
            continue
        href = canonicalize_url(_text(anchor.get("href")))
        if href and not is_reddit_url(href):
            return href
    return None


def _post_body(soup: BeautifulSoup) -> str:
    text = soup.get_text(" ", strip=True)
    return re.sub(
        r"\s*submitted by\s+(?:/u/)?\S+\s+\[link\]\s+\[comments\]\s*$",
        "",
        text,
        flags=re.IGNORECASE,
    ).strip()


def _author(value: str) -> str | None:
    normalized = value.strip().removeprefix("/u/").removeprefix("u/")
    if normalized.casefold() in {"", "[deleted]", "deleted"}:
        return None
    return normalized


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _optional_text(value: Any) -> str | None:
    result = _text(value)
    return result or None


def _optional_integer(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _optional_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
