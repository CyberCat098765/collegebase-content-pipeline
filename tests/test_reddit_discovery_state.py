from __future__ import annotations

from pathlib import Path

import pytest

from src.reddit_discovery import bundle_storage
from src.reddit_discovery.checkpoint import (
    DiscoveryCheckpoint,
    save_checkpoint,
    stable_fingerprint,
)
from src.reddit_discovery.outputs import (
    OUTPUT_FILENAMES,
    build_output_bundle,
    validate_output_bundle,
    write_output_bundle,
)
from src.reddit_discovery.registry import REQUIRED_REGISTRY_FIELDS, SourceRegistry
from src.reddit_discovery.storage import atomic_write_json


SEEN_AT = "2026-08-01T12:00:00Z"
PROCESSED_AT = "2026-08-01T12:01:00Z"
PIPELINE_VERSION = "test-pipeline-v1"


def test_registry_skips_unchanged_post_and_carries_forward_record() -> None:
    registry = _completed_registry()

    decision = registry.observe(
        _candidate(),
        seen_at="2026-08-02T12:00:00Z",
        pipeline_version=PIPELINE_VERSION,
    )

    assert decision.should_skip
    assert decision.reason == "unchanged"
    assert decision.carried_record == {"resource_id": "reddit_abc123"}
    entry = registry.get("abc123")
    assert entry is not None
    assert entry["first_seen_at"] == SEEN_AT
    assert entry["last_seen_at"] == "2026-08-02T12:00:00Z"
    assert set(REQUIRED_REGISTRY_FIELDS).issubset(entry)


def test_registry_reprocesses_changed_content_hash() -> None:
    registry = _completed_registry()
    changed = _candidate(content_hash="content-v2")

    decision = registry.observe(
        changed,
        seen_at="2026-08-02T12:00:00Z",
        pipeline_version=PIPELINE_VERSION,
    )

    assert decision.should_process
    assert decision.reason == "content_changed"
    assert decision.carried_record is None
    assert registry.get("abc123")["processing_status"] == "pending"  # type: ignore[index]


def test_registry_force_reprocesses_unchanged_post() -> None:
    registry = _completed_registry()

    decision = registry.observe(
        _candidate(),
        seen_at="2026-08-02T12:00:00Z",
        pipeline_version=PIPELINE_VERSION,
        force=True,
    )

    assert decision.should_process
    assert decision.reason == "force"


def test_checkpoint_resumes_matching_route_state(tmp_path: Path) -> None:
    run_fingerprint = stable_fingerprint({"mode": "quick", "subreddit": "ApplyingToCollege"})
    route_fingerprint = stable_fingerprint({"route": "top:year", "limit": 150})
    checkpoint = DiscoveryCheckpoint(run_fingerprint=run_fingerprint)
    checkpoint.mark_route_started(
        "top:year",
        route_fingerprint=route_fingerprint,
        state={"candidates_seen": 37, "after": "cursor-value"},
        updated_at=SEEN_AT,
    )
    checkpoint_path = tmp_path / "checkpoint.json"
    save_checkpoint(checkpoint, checkpoint_path)

    resumed = DiscoveryCheckpoint.load(
        checkpoint_path,
        expected_run_fingerprint=run_fingerprint,
    )

    assert resumed.load_errors == []
    assert resumed.resume_state(
        "top:year", route_fingerprint=route_fingerprint
    ) == {"candidates_seen": 37, "after": "cursor-value"}
    assert resumed.resume_state(
        "top:year", route_fingerprint=stable_fingerprint("changed-route")
    ) is None


def test_corrupt_checkpoint_returns_empty_state_with_error(tmp_path: Path) -> None:
    checkpoint_path = tmp_path / "checkpoint.json"
    checkpoint_path.write_text('{"routes": ', encoding="utf-8")

    checkpoint = DiscoveryCheckpoint.load(
        checkpoint_path,
        expected_run_fingerprint="current-run",
    )

    assert checkpoint.run_fingerprint == "current-run"
    assert checkpoint.routes == {}
    assert len(checkpoint.load_errors) == 1
    assert "Could not load checkpoint" in checkpoint.load_errors[0]


@pytest.mark.parametrize("mode", ["dry_run", "validate_only"])
def test_non_persistent_output_modes_create_no_files(tmp_path: Path, mode: str) -> None:
    bundle = _output_bundle()
    output_dir = tmp_path / "outputs"

    written = write_output_bundle(bundle, output_dir, **{mode: True})

    assert written == ()
    assert not output_dir.exists()


def test_output_writer_creates_exactly_eight_required_files(tmp_path: Path) -> None:
    bundle = _output_bundle()
    output_dir = tmp_path / "outputs"

    written = write_output_bundle(bundle, output_dir)

    expected_names = set(OUTPUT_FILENAMES.values())
    assert len(written) == 8
    assert {path.name for path in written} == expected_names
    assert {path.name for path in output_dir.iterdir()} == expected_names
    assert not list(output_dir.glob(".*.tmp"))


def test_output_schema_validation_accepts_complete_resource_and_rejects_missing_field() -> None:
    bundle = _output_bundle()

    assert validate_output_bundle(bundle) == []

    del bundle.accepted_resources[0]["summary"]
    errors = validate_output_bundle(bundle)

    assert errors == ["accepted_resources[0] missing fields: summary"]


def test_atomic_write_failure_preserves_existing_destination(tmp_path: Path) -> None:
    destination = tmp_path / "state.json"
    destination.write_text("original contents\n", encoding="utf-8")

    with pytest.raises(TypeError):
        atomic_write_json(destination, {"not_json_serializable": {"a", "set"}})

    assert destination.read_text(encoding="utf-8") == "original contents\n"
    assert not list(tmp_path.glob(".state.json.*.tmp"))


def test_bundle_commit_failure_restores_every_previous_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output_dir = tmp_path / "outputs"
    first = _output_bundle()
    write_output_bundle(first, output_dir)
    previous = {
        name: (output_dir / name).read_bytes()
        for name in OUTPUT_FILENAMES.values()
    }
    second = _output_bundle()
    second.run_summary["status"] = "new-generation"
    real_replace = bundle_storage.os.replace

    def fail_on_accepted(source: object, destination: object) -> None:
        if Path(destination) == output_dir / "accepted_resources.json":
            raise PermissionError("simulated Windows file lock")
        real_replace(source, destination)

    monkeypatch.setattr(bundle_storage.os, "replace", fail_on_accepted)
    with pytest.raises(PermissionError, match="simulated Windows file lock"):
        write_output_bundle(second, output_dir)

    assert {
        name: (output_dir / name).read_bytes()
        for name in OUTPUT_FILENAMES.values()
    } == previous
    assert not list(output_dir.glob(".reddit-bundle-*"))


def _completed_registry() -> SourceRegistry:
    registry = SourceRegistry()
    first_decision = registry.observe(
        _candidate(),
        seen_at=SEEN_AT,
        pipeline_version=PIPELINE_VERSION,
    )
    assert first_decision.reason == "new_post"
    registry.record_result(
        "abc123",
        processed_at=PROCESSED_AT,
        processing_status="accepted",
        accepted=True,
        final_usefulness_score=82,
        output_resource_id="reddit_abc123",
        record_data={"resource_id": "reddit_abc123"},
    )
    return registry


def _output_bundle():
    candidate = _candidate()
    registry = _completed_registry()
    return build_output_bundle(
        raw_candidates=[candidate],
        accepted_resources=[candidate],
        human_review=[],
        rejected_candidates=[],
        duplicate_clusters=[],
        source_registry=registry,
        pipeline_version=PIPELINE_VERSION,
    )


def _candidate(*, content_hash: str = "content-v1") -> dict[str, object]:
    return {
        "reddit_post_id": "abc123",
        "canonical_url": "https://www.reddit.com/r/ApplyingToCollege/comments/abc123/guide/",
        "content_hash": content_hash,
        "subreddit": "ApplyingToCollege",
        "title": "Detailed college essay guide",
        "author_name": None,
        "created_utc": 1_700_000_000.0,
        "retrieved_at": SEEN_AT,
        "score": 500,
        "upvote_ratio": 0.97,
        "num_comments": 42,
        "link_flair_text": "Advice",
        "primary_topic": "personal_essay",
        "secondary_topics": ["common_app"],
        "audience": ["high_school_students"],
        "summary": "A reusable guide to planning and revising an application essay.",
        "key_takeaways": ["Start with reflection", "Revise for clarity"],
        "why_useful": "It provides durable, concrete steps.",
        "limitations_or_cautions": ["Examples are community-authored"],
        "freshness_status": "durable",
        "heuristic_score": 80,
        "llm_adjustment": 2,
        "final_usefulness_score": 82,
        "score_breakdown": {
            "content_depth": {
                "score": 18,
                "max_score": 20,
                "reasons": ["structured explanation"],
            }
        },
        "confidence": 0.9,
        "selection_reasons": ["high content depth"],
        "discovered_by": ["top:year"],
        "comments": [],
    }
