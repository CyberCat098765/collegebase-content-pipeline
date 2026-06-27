from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from src.runner import run_pipeline
from src.runtime import load_jobs, load_source_registry


TRANSCRIPT_TEXT = """WEBVTT

00:00:00.000 --> 00:00:10.000
College application essays should connect a specific student story to what admissions officers need to understand. Strong application writing gives concrete evidence, explains why the moment mattered, and avoids repeating the same activity list details.

00:00:10.000 --> 00:00:20.000
The Common App activities list should explain impact, leadership, and time commitment for each extracurricular activity. Admissions readers need concise context about the student's role, the scale of the work, and the contribution that would not be obvious from a title alone.
"""


def test_run_budget_checkpoint_and_resume(tmp_path: Path) -> None:
    first_config = _write_config(tmp_path, max_sources_total=1)
    out_path = tmp_path / "processed" / "output.json"

    first_output = run_pipeline(first_config, out_path=out_path)

    runtime_dir = tmp_path / "runtime"
    assert (runtime_dir / "jobs.json").exists()
    assert (runtime_dir / "checkpoint.json").exists()
    assert first_output.run_summary["stop_reason"] == "max_sources_reached"
    assert first_output.run_summary["sources_succeeded"] == 1

    second_config = _write_config(tmp_path, max_sources_total=5)
    second_output = run_pipeline(second_config, resume=True, out_path=out_path)
    jobs = load_jobs(runtime_dir)

    assert second_output.run_summary["sources_succeeded"] == 2
    assert second_output.run_summary["stop_reason"] == "completed"
    assert [job.status for job in jobs] == ["succeeded", "succeeded"]
    assert [job.attempts for job in jobs] == [1, 1]


def test_dry_run_does_not_create_outputs(tmp_path: Path) -> None:
    config_path = _write_config(tmp_path, max_sources_total=5)
    out_path = tmp_path / "processed" / "output.json"

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "src.main",
            "--config",
            str(config_path),
            "--dry-run",
        ],
        cwd=Path(__file__).resolve().parents[1],
        text=True,
        capture_output=True,
        check=True,
    )

    assert "Jobs that would be created: 2" in result.stdout
    assert "No scraping performed" in result.stdout
    assert not out_path.exists()
    assert not (tmp_path / "runtime" / "jobs.json").exists()


def test_source_registry_skips_successful_sources_on_second_run(tmp_path: Path) -> None:
    config_path = _write_config(tmp_path, max_sources_total=5)

    first_output = run_pipeline(config_path, force=True)
    second_output = run_pipeline(config_path)
    registry = load_source_registry(tmp_path / "runtime")

    assert first_output.run_summary["sources_succeeded"] == 2
    assert first_output.run_summary["admissions_relevant_chunks"] == 2
    assert second_output.run_summary["sources_succeeded"] == 0
    assert second_output.run_summary["sources_skipped_from_cache"] == 2
    assert second_output.run_summary["admissions_relevant_chunks"] == 0
    assert len(registry["sources"]) == 2


def _write_config(tmp_path: Path, max_sources_total: int) -> Path:
    transcript_one = tmp_path / "one.vtt"
    transcript_two = tmp_path / "two.vtt"
    transcript_one.write_text(TRANSCRIPT_TEXT, encoding="utf-8")
    transcript_two.write_text(TRANSCRIPT_TEXT, encoding="utf-8")
    config_path = tmp_path / "sources.yaml"
    config_path.write_text(
        f"""
sources:
  youtube:
    transcript_files:
      - path: "one.vtt"
        source_url: "https://example.com/video-one"
        title: "College Essay Transcript"
      - path: "two.vtt"
        source_url: "https://example.com/video-two"
        title: "Common App Transcript"
processing:
  chunk_max_chars: 500
run:
  max_runtime_minutes: 30
  max_sources_total: {max_sources_total}
  max_chunks_total: 50
  max_failures_total: 10
  request_delay_seconds: 0
  timeout_seconds: 5
  retries_per_source: 0
  checkpoint_every_n_sources: 1
  runtime_dir: "{(tmp_path / 'runtime').as_posix()}"
""",
        encoding="utf-8",
    )
    return config_path
