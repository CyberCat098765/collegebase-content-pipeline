from __future__ import annotations

import json
from pathlib import Path
from typing import Any, NoReturn

import pytest

from src.reddit_discovery import pipeline
from src.reddit_discovery.candidate_import import load_candidate_file
from src.reddit_discovery.cli import discovery_main
from src.reddit_discovery.outputs import ACCEPTED_RESOURCE_FIELDS, OUTPUT_FILENAMES
from src.reddit_discovery.pipeline import RedditDiscoveryOptions, run_reddit_discovery


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "reddit_candidates.json"


def test_candidate_import_accepts_json_and_reports_bad_jsonl_records(
    tmp_path: Path,
) -> None:
    imported = load_candidate_file(FIXTURE_PATH)

    assert len(imported.candidates) == 18
    assert imported.errors == []
    assert all(item.subreddit == "ApplyingToCollege" for item in imported.candidates)
    assert all(item.acquisition_method == "imported_json" for item in imported.candidates)
    assert all(item.provenance["source_file"] == FIXTURE_PATH.name for item in imported.candidates)

    valid = {
        "reddit_post_id": "jsonl-valid",
        "subreddit": "ApplyingToCollege",
        "title": "Application planning guide",
        "selftext": "A detailed application checklist with reusable steps.",
        "permalink": (
            "https://www.reddit.com/r/ApplyingToCollege/comments/jsonl-valid/guide/"
        ),
    }
    wrong_subreddit = {
        **valid,
        "reddit_post_id": "wrong-community",
        "subreddit": "college",
    }
    jsonl_path = tmp_path / "candidates.jsonl"
    jsonl_path.write_text(
        "\n".join(
            (
                json.dumps(valid),
                "{not-json}",
                json.dumps([]),
                json.dumps(wrong_subreddit),
            )
        ),
        encoding="utf-8",
    )

    jsonl = load_candidate_file(jsonl_path)

    assert [item.reddit_post_id for item in jsonl.candidates] == ["jsonl-valid"]
    assert [error.error_type for error in jsonl.errors] == [
        "JSONDecodeError",
        "TypeError",
        "ValueError",
    ]
    assert all(error.retryable is False for error in jsonl.errors)


def test_import_preserves_upstream_acquisition_provenance(tmp_path: Path) -> None:
    source = tmp_path / "upstream.json"
    source.write_text(
        json.dumps(
            [
                {
                    "reddit_post_id": "upstream",
                    "subreddit": "ApplyingToCollege",
                    "title": "Organized application resources",
                    "selftext": "Reusable admissions guidance.",
                    "permalink": "https://www.reddit.com/r/ApplyingToCollege/comments/upstream/guide/",
                    "acquisition_method": "curated_a2c",
                    "provenance": {"curated_source": "test_masterpost"},
                }
            ]
        ),
        encoding="utf-8",
    )

    imported = load_candidate_file(source)

    assert imported.candidates[0].acquisition_method == "curated_a2c"
    assert imported.candidates[0].provenance["curated_source"] == "test_masterpost"
    assert imported.candidates[0].provenance["source_file"] == "upstream.json"


def test_offline_pipeline_is_deterministic_and_never_uses_reddit_auth(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden_network(*args: Any, **kwargs: Any) -> NoReturn:
        raise AssertionError("offline import attempted Reddit authentication or network access")

    monkeypatch.setattr(pipeline, "create_reddit_client", forbidden_network)
    monkeypatch.setattr(pipeline, "probe_reddit_auth", forbidden_network)

    first = run_reddit_discovery(_options(tmp_path / "first"))
    second = run_reddit_discovery(_options(tmp_path / "second"))

    assert first.summary["mode"] == "offline-import"
    assert first.summary["candidate_count"] == 18
    assert first.summary["candidates_acquired_this_run"] == 18
    assert first.summary["accepted_count"] == 3
    assert first.summary["human_review_count"] == 3
    assert first.summary["rejected_count"] == 12
    assert first.summary["structurally_invalid_count"] == 2
    assert first.summary["obvious_junk_rejected_count"] == 8
    assert first.summary["exact_duplicate_count"] == 1
    assert first.summary["near_duplicate_count"] == 1
    assert first.summary["candidates_scored_count"] == 8
    assert first.summary["final_selected_count"] == 3
    assert first.summary["acquisition_error_count"] == 0
    assert first.summary["processing_error_count"] == 0
    assert first.summary["cache_hit_count"] == 0
    assert first.summary["cache_miss_count"] == 18
    assert first.summary["error_count"] == 0

    expected_accepted = ["fixture_essay", "fixture_aid", "fixture_waitlist"]
    expected_review = ["fixture_retro", "fixture_list", "fixture_activities"]
    assert _ids(first.bundle.accepted_resources) == expected_accepted  # type: ignore[union-attr]
    assert _ids(first.bundle.human_review) == expected_review  # type: ignore[union-attr]
    assert _ids(second.bundle.accepted_resources) == expected_accepted  # type: ignore[union-attr]
    assert _ids(second.bundle.human_review) == expected_review  # type: ignore[union-attr]

    assert len(first.written_paths) == len(OUTPUT_FILENAMES)
    assert {path.name for path in first.written_paths} == set(OUTPUT_FILENAMES.values())


def test_offline_outputs_preserve_citations_and_duplicate_evidence(
    tmp_path: Path,
) -> None:
    result = run_reddit_discovery(_options(tmp_path / "outputs"))
    assert result.bundle is not None

    for resource in result.bundle.accepted_resources:
        assert set(resource) == set(ACCEPTED_RESOURCE_FIELDS)
        assert resource["permalink"].startswith(
            "https://www.reddit.com/r/ApplyingToCollege/comments/"
        )
        assert resource["canonical_url"] == resource["permalink"]
        assert resource["cleaned_text"]
        assert resource["author"] is None
        assert resource["acquisition_method"] == "imported_json"
        assert resource["provenance"]["source_file"] == FIXTURE_PATH.name

    clusters = result.bundle.duplicate_clusters
    assert {cluster["members"][0]["reason_code"] for cluster in clusters} == {
        "DUPLICATE_CANONICAL_URL",
        "NEAR_DUPLICATE_LOWER_QUALITY",
    }
    near = next(
        cluster
        for cluster in clusters
        if cluster["members"][0]["reason_code"] == "NEAR_DUPLICATE_LOWER_QUALITY"
    )
    assert near["members"][0]["title_similarity"] >= 0.90
    assert near["members"][0]["similarity"] >= 0.90

    report = result.bundle.review_report
    assert "## Highest-Ranked Selected Resources" in report
    assert "| 1 | 83 |" in report
    assert "Comprehensive Common App Essay Guide" in report
    assert "## Rejected Sample" in report
    assert "TRIVIAL_TEST_SCORE_QUESTION" in report


def test_offline_cli_happy_path_writes_complete_bundle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def forbidden_network(*args: Any, **kwargs: Any) -> NoReturn:
        raise AssertionError("offline CLI attempted Reddit authentication")

    monkeypatch.setattr(pipeline, "create_reddit_client", forbidden_network)
    output_dir = tmp_path / "cli-output"

    status = discovery_main(
        [
            "--input-json",
            str(FIXTURE_PATH),
            "--no-llm",
            "--force",
            "--output-dir",
            str(output_dir),
        ]
    )
    output = capsys.readouterr().out

    assert status == 0
    assert "Reddit discovery completed" in output
    assert "- Accepted: 3" in output
    assert "- Stop reason: completed" in output
    assert {path.name for path in output_dir.iterdir()} == set(OUTPUT_FILENAMES.values())


def _options(output_dir: Path) -> RedditDiscoveryOptions:
    return RedditDiscoveryOptions(
        input_path=FIXTURE_PATH,
        no_llm=True,
        force=True,
        output_dir=output_dir,
    )


def _ids(records: list[dict[str, Any]]) -> list[str]:
    return [str(record["reddit_post_id"]) for record in records]
