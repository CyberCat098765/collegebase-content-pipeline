from __future__ import annotations

import logging
import json
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

from src.config import ArticleConfig
from src.models import PipelineError, SourceItem
from src.processing.cleaner import clean_text, clean_title

LOGGER = logging.getLogger(__name__)


class ArticleCollector:
    def __init__(self, config: ArticleConfig) -> None:
        self.config = config
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "collegebase-content-pipeline/0.1.0"})

    def collect(self, collected_at: str) -> tuple[list[SourceItem], list[PipelineError]]:
        items: list[SourceItem] = []
        errors: list[PipelineError] = []

        for url in self.config.urls:
            item, error = self._collect_url(url, collected_at)
            if item:
                items.append(item)
            if error:
                errors.append(error)

        return items, errors

    def _collect_url(
        self, url: str, collected_at: str
    ) -> tuple[SourceItem | None, PipelineError | None]:
        if not _is_http_url(url):
            return None, PipelineError("blog", url, "Invalid article URL.")

        try:
            response = self.session.get(url, timeout=self.config.request_timeout_seconds)
        except requests.RequestException as exc:
            return None, PipelineError("blog", url, str(exc))

        if response.status_code >= 400:
            return None, PipelineError("blog", url, f"HTTP {response.status_code}")

        content_type = response.headers.get("Content-Type", "")
        if "html" not in content_type.lower():
            return None, PipelineError("blog", url, f"Unsupported content type: {content_type}")

        html = response.text
        title, author, publish_date, text, extractor = _extract_article_fields(html, url)
        if _is_poor_extraction(text):
            return None, PipelineError("blog", url, "Article extraction produced too little text.")

        return (
            SourceItem(
                source_type="blog",
                source_name=urlparse(url).netloc,
                title=title or url,
                url=url,
                author_or_channel=author,
                published_date=publish_date,
                collected_at=collected_at,
                raw_text=text,
                metadata={
                    "content_type": content_type,
                    "status_code": response.status_code,
                    "extractor": extractor,
                },
            ),
            None,
        )


def _extract_article_fields(html: str, url: str) -> tuple[str, str, str, str, str]:
    soup = BeautifulSoup(html, "html.parser")
    fallback_title = _meta_content(soup, ["og:title", "twitter:title"]) or (
        soup.title.get_text(" ", strip=True) if soup.title else ""
    )
    fallback_author = _meta_content(soup, ["author", "article:author", "parsely-author"])
    fallback_date = _meta_content(
        soup,
        ["article:published_time", "date", "datePublished", "pubdate", "parsely-pub-date"],
    )

    title, author, publish_date, text = _trafilatura_article(html, url)
    if not _is_poor_extraction(text):
        return (
            clean_title(title or fallback_title),
            clean_title(author or fallback_author),
            clean_title(publish_date or fallback_date),
            clean_text(text),
            "trafilatura",
        )

    text = _readability_text(html)
    if not _is_poor_extraction(text):
        return (
            clean_title(fallback_title),
            clean_title(fallback_author),
            clean_title(fallback_date),
            clean_text(text),
            "readability-lxml",
        )

    text = _fallback_article_text(soup)
    return (
        clean_title(fallback_title),
        clean_title(fallback_author),
        clean_title(fallback_date),
        clean_text(text),
        "beautifulsoup",
    )


def _trafilatura_article(html: str, url: str) -> tuple[str, str, str, str]:
    try:
        import trafilatura
    except ImportError:
        return "", "", "", ""

    try:
        extracted = trafilatura.extract(
            html,
            url=url,
            output_format="json",
            with_metadata=True,
            include_comments=False,
            include_tables=False,
            favor_precision=True,
        )
    except Exception as exc:
        LOGGER.warning("Trafilatura extraction failed for %s: %s", url, exc)
        return "", "", "", ""

    if not extracted:
        return "", "", "", ""

    try:
        data = json.loads(extracted)
    except ValueError:
        return "", "", "", clean_text(extracted)

    return (
        str(data.get("title") or ""),
        str(data.get("author") or ""),
        str(data.get("date") or ""),
        str(data.get("text") or ""),
    )


def _readability_text(html: str) -> str:
    try:
        from readability import Document
    except ImportError:
        return ""

    try:
        document = Document(html)
        summary = document.summary(html_partial=True)
    except Exception as exc:
        LOGGER.warning("Readability extraction failed: %s", exc)
        return ""

    soup = BeautifulSoup(summary, "html.parser")
    return soup.get_text("\n", strip=True)


def _fallback_article_text(soup: BeautifulSoup) -> str:
    for tag in soup(["script", "style", "noscript", "nav", "footer", "aside", "form", "header"]):
        tag.decompose()

    container = soup.find("article") or soup.find("main") or soup.body
    if not container:
        return ""

    return container.get_text("\n", strip=True)


def _meta_content(soup: BeautifulSoup, keys: list[str]) -> str:
    lowered = {key.lower() for key in keys}
    for tag in soup.find_all("meta"):
        name = (tag.get("name") or tag.get("property") or tag.get("itemprop") or "").lower()
        if name in lowered:
            return str(tag.get("content") or "").strip()
    return ""


def _is_http_url(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _is_poor_extraction(text: str) -> bool:
    return len(clean_text(text).split()) < 80
