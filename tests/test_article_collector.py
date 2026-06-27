import pytest

from src.collectors import article_collector


def test_article_extraction_falls_back_to_readability(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(article_collector, "_trafilatura_article", lambda html, url: ("", "", "", ""))
    monkeypatch.setattr(
        article_collector,
        "_readability_text",
        lambda html: "College essays need specific stories and clear reflection. " * 20,
    )

    title, author, publish_date, text, extractor = article_collector._extract_article_fields(
        "<html><head><title>Essay Tips</title></head><body></body></html>",
        "https://example.com/essay",
    )

    assert title == "Essay Tips"
    assert author == ""
    assert publish_date == ""
    assert "College essays" in text
    assert extractor == "readability-lxml"
