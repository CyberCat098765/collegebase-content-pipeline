from __future__ import annotations

import json
from html import escape
from typing import Any

from src.reddit_discovery.capabilities import CapabilityResult, check_reddit_capabilities
from src.reddit_discovery.cli import (
    build_discovery_parser,
    capability_check_main,
)
from src.reddit_discovery.options import options_from_namespace
from src.reddit_discovery.provider_http import BoundedHttpClient
from src.reddit_discovery.providers import stop_reason


class FakeResponse:
    def __init__(
        self,
        status_code: int,
        body: str,
        content_type: str,
    ) -> None:
        self.status_code = status_code
        self.text = body
        self.headers = {"Content-Type": content_type}
        self.url = ""


class FakeSession:
    def __init__(self, responses: list[FakeResponse]) -> None:
        self.responses = list(responses)
        self.calls: list[str] = []

    def get(self, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append(url)
        response = self.responses.pop(0)
        response.url = url
        return response


def test_capability_matrix_reports_live_evidence_and_missing_praw() -> None:
    listing = FakeResponse(
        200,
        json.dumps({"data": {"children": [{"data": {"id": "one"}}]}}),
        "application/json",
    )
    curated_link = "https://www.reddit.com/r/ApplyingToCollege/comments/guide1/guide/"
    curated = FakeResponse(
        200,
        _atom_feed(
            "imxhiu",
            "A2C resources",
            f'<a href="{curated_link}">Guide</a>',
        ),
        "application/atom+xml",
    )
    rss = FakeResponse(
        200,
        _atom_feed("rss1", "Essay guide", "Useful Common App essay steps."),
        "application/atom+xml",
    )
    session = FakeSession([listing, listing, listing, curated, rss])
    client = BoundedHttpClient(
        request_limit=7,
        cache_dir=None,
        session=session,
        max_retries=0,
    )

    results = check_reddit_capabilities(client=client, environ={})
    statuses = {result.provider: result.status for result in results}

    assert statuses == {
        "public_json_top": "PASS",
        "public_json_new": "PASS",
        "public_json_search": "PASS",
        "curated_a2c": "PASS",
        "rss_atom": "PASS",
        "praw_oauth": "MISSING_CREDENTIALS",
        "import_json": "PASS",
    }
    assert len(session.calls) == 5
    assert next(result for result in results if result.provider == "curated_a2c").useful_data == (
        "1 curated A2C post links"
    )


def test_capability_matrix_reports_public_json_403_without_stopping_fallbacks() -> None:
    denied = FakeResponse(403, "denied", "text/html")
    curated_link = "https://www.reddit.com/r/ApplyingToCollege/comments/guide1/guide/"
    curated = FakeResponse(
        200,
        _atom_feed("imxhiu", "Resources", f'<a href="{curated_link}">Guide</a>'),
        "application/atom+xml",
    )
    rss = FakeResponse(
        200,
        _atom_feed("rss1", "Essay guide", "Useful essay steps."),
        "application/atom+xml",
    )
    session = FakeSession([denied, denied, denied, curated, rss])
    client = BoundedHttpClient(
        request_limit=7,
        cache_dir=None,
        session=session,
        max_retries=0,
    )

    results = check_reddit_capabilities(client=client, environ={})

    assert [result.status_code for result in results[:3]] == [403, 403, 403]
    assert all(result.status == "FAIL" for result in results[:3])
    assert next(result for result in results if result.provider == "curated_a2c").status == "PASS"
    assert next(result for result in results if result.provider == "rss_atom").status == "PASS"


def test_capability_cli_prints_concise_matrix(
    monkeypatch: Any,
    capsys: Any,
) -> None:
    monkeypatch.setattr(
        "src.reddit_discovery.capabilities.check_reddit_capabilities",
        lambda: [
            CapabilityResult(
                provider="rss_atom",
                status="PASS",
                credentials="none",
                cost="$0",
                live_tested=True,
                useful_data="5 post records",
                limitations="Development only",
                request_count=1,
                status_code=200,
                content_type="application/atom+xml",
            )
        ],
    )

    assert capability_check_main([]) == 0
    output = capsys.readouterr().out
    assert "rss_atom\tPASS\t200\t1\t5 post records" in output
    assert "Production note" in output


def test_cli_parses_manual_provider_urls() -> None:
    url = "https://www.reddit.com/r/ApplyingToCollege/comments/abc123/guide/"
    args = build_discovery_parser().parse_args(
        ["--provider", "manual", "--reddit-url", url]
    )
    options = options_from_namespace(args)

    assert options.provider == "manual"
    assert options.reddit_urls == (url,)
    assert options.quick is True


def test_run_stop_reason_prioritizes_rate_limit_evidence() -> None:
    assert stop_reason(
        discovery_limit_reached=False,
        provider_statuses=[
            {"status": "completed_with_errors", "status_code": 429},
            {"status": "unavailable", "status_code": 429},
        ],
    ) == "rate_limited"


def _atom_feed(post_id: str, title: str, body_html: str) -> str:
    post_url = f"https://www.reddit.com/r/ApplyingToCollege/comments/{post_id}/guide/"
    content = (
        body_html
        + f'<p>submitted by /u/test <a href="{post_url}">[link]</a> '
        f'<a href="{post_url}">[comments]</a></p>'
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<feed xmlns="http://www.w3.org/2005/Atom">'
        f"<entry><id>t3_{post_id}</id><title>{escape(title)}</title>"
        "<updated>2026-08-01T00:00:00Z</updated><author><name>/u/test</name></author>"
        f'<content type="html">{escape(content)}</content></entry></feed>'
    )
