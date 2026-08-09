from __future__ import annotations

import sys
from types import SimpleNamespace
from typing import Any, NoReturn

import pytest

from src.reddit_discovery import cli
from src.reddit_discovery import pipeline
from src.reddit_discovery.auth import MissingRedditCredentials
from src.reddit_discovery.models import AuthProbeResult
from src.reddit_discovery.options import options_from_namespace
from src.reddit_discovery.pipeline import RedditDiscoveryOptions


REQUIRED_FLAGS = (
    "--subreddit",
    "--max-range",
    "--quick",
    "--input-json",
    "--include-comments",
    "--max-comments-per-post",
    "--no-comments",
    "--no-llm",
    "--force",
    "--resume",
    "--dry-run",
    "--validate-only",
    "--output-dir",
    "--candidate-limit",
    "--accepted-limit",
    "--minimum-usefulness-score",
    "--verbose",
)


def test_help_exposes_every_required_flag() -> None:
    help_text = cli.build_discovery_parser().format_help()

    for flag in REQUIRED_FLAGS:
        assert flag in help_text


def test_discovery_defaults_match_required_values() -> None:
    args = cli.build_discovery_parser().parse_args([])

    assert args.subreddit == "ApplyingToCollege"
    assert args.max_comments_per_post == 15
    assert args.minimum_usefulness_score == 70
    assert args.candidate_limit == 500
    assert args.include_comments is False
    assert args.no_comments is False
    assert options_from_namespace(args).quick is True


@pytest.mark.parametrize(
    "arguments",
    [
        ["--max-range", "--quick"],
        ["--max-range", "--input-json", "candidates.json"],
        ["--include-comments", "--no-comments"],
    ],
)
def test_mutually_exclusive_flags_are_rejected(arguments: list[str]) -> None:
    with pytest.raises(SystemExit) as captured:
        cli.build_discovery_parser().parse_args(arguments)

    assert captured.value.code == 2


def test_non_applying_to_college_subreddit_is_rejected(
    capsys: pytest.CaptureFixture[str],
) -> None:
    status = cli.discovery_main(["--subreddit", "college", "--dry-run"])
    captured = capsys.readouterr()

    assert status == 2
    assert "Only r/ApplyingToCollege is supported" in captured.err


def test_auth_checker_missing_credentials_is_clear_and_nonzero(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def missing_client(**kwargs: Any) -> NoReturn:
        raise MissingRedditCredentials(
            "Missing Reddit API credentials. Missing: REDDIT_CLIENT_SECRET."
        )

    monkeypatch.setattr(cli, "create_reddit_client", missing_client)
    status = cli.auth_check_main([])
    output = capsys.readouterr().out

    assert status == 2
    assert "Authentication: failure" in output
    assert "Subreddit accessible: false" in output
    assert "Missing Reddit API credentials" in output
    assert "Next step:" in output


def test_auth_checker_never_prints_secret_on_client_failure(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    secret = "client-secret-that-must-never-print"
    monkeypatch.setenv("REDDIT_CLIENT_ID", "client-id")
    monkeypatch.setenv("REDDIT_CLIENT_SECRET", secret)
    monkeypatch.setenv(
        "REDDIT_USER_AGENT",
        "windows:collegebase-reddit-discovery:v1.0.0 (by u/tester)",
    )

    def failing_constructor(**kwargs: Any) -> NoReturn:
        raise RuntimeError(kwargs["client_secret"])

    monkeypatch.setitem(sys.modules, "praw", SimpleNamespace(Reddit=failing_constructor))
    status = cli.auth_check_main([])
    captured = capsys.readouterr()

    assert status == 3
    assert "Authentication: failure" in captured.out
    assert secret not in captured.out
    assert secret not in captured.err


def test_auth_checker_never_prints_secret_on_probe_failure(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    secret = "probe-secret-that-must-never-print"

    class BrokenSubreddit:
        display_name = "ApplyingToCollege"

        @property
        def title(self) -> str:
            raise RuntimeError(secret)

    reddit = SimpleNamespace(
        read_only=True,
        auth=SimpleNamespace(limits={"used": 1}),
        subreddit=lambda name: BrokenSubreddit(),
    )
    monkeypatch.setattr(cli, "create_reddit_client", lambda **kwargs: reddit)
    status = cli.auth_check_main([])
    captured = capsys.readouterr()

    assert status == 4
    assert "Authentication: failure" in captured.out
    assert "Subreddit accessible: false" in captured.out
    assert secret not in captured.out
    assert secret not in captured.err


def test_successful_auth_checker_prints_every_required_field(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(cli, "create_reddit_client", lambda **kwargs: object())
    monkeypatch.setattr(
        cli,
        "probe_reddit_auth",
        lambda reddit, subreddit: AuthProbeResult(
            success=True,
            read_only=True,
            subreddit="ApplyingToCollege",
            subreddit_title="College Admissions",
            accessible=True,
            rate_limit={"remaining": 599, "used": 1},
        ),
    )
    status = cli.auth_check_main([])
    output = capsys.readouterr().out

    assert status == 0
    assert "Authentication: success" in output
    assert "Read-only: true" in output
    assert "Subreddit name: ApplyingToCollege" in output
    assert "Subreddit title: College Admissions" in output
    assert "Subreddit accessible: true" in output
    assert 'Rate-limit information: {"remaining": 599, "used": 1}' in output


@pytest.mark.parametrize("mode", ["dry_run", "validate_only"])
def test_planning_modes_make_no_network_calls_or_persistent_writes(
    mode: str,
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output_dir = tmp_path / "must-not-exist"

    def forbidden_action(*args: Any, **kwargs: Any) -> NoReturn:
        raise AssertionError("planning mode attempted network access or a write")

    monkeypatch.setattr(pipeline, "validate_dependencies", lambda **_: None)
    monkeypatch.setattr(pipeline, "create_reddit_client", forbidden_action)
    monkeypatch.setattr(pipeline, "archive_existing_outputs", forbidden_action)
    monkeypatch.setattr(pipeline, "write_output_bundle", forbidden_action)
    options = RedditDiscoveryOptions(
        dry_run=mode == "dry_run",
        validate_only=mode == "validate_only",
        output_dir=output_dir,
    )

    result = pipeline.run_reddit_discovery(options)

    assert result.summary["status"] == "validated"
    assert result.bundle is None
    assert result.written_paths == ()
    assert not output_dir.exists()
