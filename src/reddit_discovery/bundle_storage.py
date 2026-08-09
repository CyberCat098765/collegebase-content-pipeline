from __future__ import annotations

import os
import shutil
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from src.reddit_discovery.storage import (
    atomic_write_json,
    atomic_write_jsonl,
    atomic_write_text,
)


def write_bundle_files(bundle: Any, paths: Mapping[str, Path]) -> tuple[Path, ...]:
    """Stage a complete output generation and restore the prior one on failure."""

    destinations = tuple(paths.values())
    if not destinations:
        return ()
    output_dir = destinations[0].parent
    if any(path.parent != output_dir for path in destinations):
        raise ValueError("All Reddit bundle outputs must share one directory.")
    output_dir.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".reddit-bundle-", dir=output_dir))
    staged_dir = staging / "next"
    backup_dir = staging / "previous"

    try:
        staged_paths = _stage_bundle(bundle, paths, staged_dir)
        backup_dir.mkdir(parents=True, exist_ok=True)
        existed = {destination: destination.exists() for destination in destinations}
        for destination in destinations:
            if existed[destination]:
                shutil.copy2(destination, backup_dir / destination.name)

        replaced: list[Path] = []
        try:
            for destination in destinations:
                os.replace(staged_paths[destination], destination)
                replaced.append(destination)
        except BaseException as exc:
            rollback_errors = _restore_previous_generation(
                replaced,
                existed,
                backup_dir,
            )
            if rollback_errors:
                names = ", ".join(path.name for path in rollback_errors)
                raise OSError(
                    f"Output commit failed and rollback was incomplete for: {names}"
                ) from exc
            raise
        return destinations
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def _stage_bundle(
    bundle: Any,
    paths: Mapping[str, Path],
    staged_dir: Path,
) -> dict[Path, Path]:
    staged = {destination: staged_dir / destination.name for destination in paths.values()}
    atomic_write_jsonl(staged[paths["raw_candidates"]], bundle.raw_candidates)
    atomic_write_json(staged[paths["accepted_resources"]], bundle.accepted_resources)
    atomic_write_json(staged[paths["human_review"]], bundle.human_review)
    atomic_write_jsonl(staged[paths["rejected_candidates"]], bundle.rejected_candidates)
    atomic_write_json(staged[paths["duplicate_clusters"]], bundle.duplicate_clusters)
    atomic_write_json(
        staged[paths["source_registry"]], bundle.source_registry, sort_keys=True
    )
    atomic_write_text(staged[paths["review_report"]], _trailing_newline(bundle.review_report))
    atomic_write_json(staged[paths["run_summary"]], bundle.run_summary)
    return staged


def _restore_previous_generation(
    replaced: list[Path],
    existed: Mapping[Path, bool],
    backup_dir: Path,
) -> list[Path]:
    errors: list[Path] = []
    for destination in reversed(replaced):
        try:
            if existed[destination]:
                os.replace(backup_dir / destination.name, destination)
            else:
                destination.unlink(missing_ok=True)
        except OSError:
            errors.append(destination)
    return errors


def _trailing_newline(value: str) -> str:
    return value.rstrip() + "\n"
