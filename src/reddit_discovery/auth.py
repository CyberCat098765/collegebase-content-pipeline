from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from src.reddit_discovery.constants import (
    REDDIT_CLIENT_ID_ENV,
    REDDIT_CLIENT_SECRET_ENV,
    REDDIT_ENV_VARS,
    REDDIT_USER_AGENT_ENV,
    SUPPORTED_SUBREDDIT,
)
from src.reddit_discovery.models import AuthProbeResult


MISSING_CREDENTIALS_MESSAGE = (
    "Missing Reddit API credentials. Set REDDIT_CLIENT_ID, "
    "REDDIT_CLIENT_SECRET, and REDDIT_USER_AGENT."
)
MISSING_CREDENTIALS_NEXT_STEP = (
    "Request or register authorized Reddit Data API access, put the three values "
    "in the local .env, then run python scripts/check_reddit_auth.py again."
)
INVALID_CREDENTIALS_NEXT_STEP = (
    "Verify Reddit approved the intended use and check the client ID, client secret, "
    "and descriptive user agent in the local .env, then run the auth check again."
)


class RedditAuthError(RuntimeError):
    """A safe-to-display Reddit authentication failure."""


class MissingRedditCredentials(RedditAuthError):
    pass


@dataclass(frozen=True, slots=True)
class RedditCredentials:
    client_id: str = field(repr=False)
    client_secret: str = field(repr=False)
    user_agent: str = field(repr=False)


def load_reddit_environment(
    env_path: str | Path | None = None,
    *,
    override: bool = False,
) -> Path | None:
    """Load only the three Reddit settings from a local dotenv-style file."""
    path = _resolve_env_path(env_path)
    if path is None:
        return None

    try:
        values = _read_env_values(path)
    except OSError as exc:
        raise RedditAuthError(f"Could not read the local environment file: {type(exc).__name__}") from exc

    for name in REDDIT_ENV_VARS:
        value = values.get(name, "").strip()
        if value and (override or not os.getenv(name, "").strip()):
            os.environ[name] = value
    return path


def credentials_from_environment(
    *,
    load_env: bool = True,
    env_path: str | Path | None = None,
    environ: Mapping[str, str] | None = None,
) -> RedditCredentials:
    if load_env and environ is None:
        load_reddit_environment(env_path)

    source: Mapping[str, str] = os.environ if environ is None else environ
    values = {name: str(source.get(name, "") or "").strip() for name in REDDIT_ENV_VARS}
    missing = [name for name, value in values.items() if not value]
    if missing:
        raise MissingRedditCredentials(
            f"{MISSING_CREDENTIALS_MESSAGE} Missing: {', '.join(missing)}."
        )
    if any(_looks_like_placeholder(value) for value in values.values()):
        raise MissingRedditCredentials(
            f"{MISSING_CREDENTIALS_MESSAGE} Replace placeholder values before continuing."
        )

    return RedditCredentials(
        client_id=values[REDDIT_CLIENT_ID_ENV],
        client_secret=values[REDDIT_CLIENT_SECRET_ENV],
        user_agent=values[REDDIT_USER_AGENT_ENV],
    )


def create_reddit_client(
    credentials: RedditCredentials | None = None,
    *,
    load_env: bool = True,
    env_path: str | Path | None = None,
) -> Any:
    credentials = credentials or credentials_from_environment(
        load_env=load_env,
        env_path=env_path,
    )
    try:
        import praw
    except ImportError as exc:
        raise RedditAuthError("PRAW is not installed.") from exc

    try:
        reddit = praw.Reddit(
            client_id=credentials.client_id,
            client_secret=credentials.client_secret,
            user_agent=credentials.user_agent,
            check_for_async=False,
        )
        reddit.read_only = True
    except Exception as exc:
        raise RedditAuthError(safe_auth_error_message(exc)) from exc
    return reddit


def probe_reddit_auth(
    reddit: Any,
    subreddit_name: str = SUPPORTED_SUBREDDIT,
) -> AuthProbeResult:
    """Fetch subreddit metadata, which lazily performs the OAuth/API request."""
    read_only = bool(getattr(reddit, "read_only", False))
    try:
        return probe_reddit_auth_unchecked(reddit, subreddit_name)
    except Exception as exc:
        return AuthProbeResult(
            success=False,
            read_only=read_only,
            subreddit=subreddit_name,
            subreddit_title="",
            accessible=False,
            rate_limit=reddit_rate_limits(reddit),
            error=safe_auth_error_message(exc),
            next_step=INVALID_CREDENTIALS_NEXT_STEP,
        )

def probe_reddit_auth_unchecked(
    reddit: Any,
    subreddit_name: str = SUPPORTED_SUBREDDIT,
) -> AuthProbeResult:
    """Fetch metadata and allow API exceptions to reach bounded retry handling."""
    subreddit = reddit.subreddit(subreddit_name)
    title = str(subreddit.title or "")
    display_name = str(subreddit.display_name or subreddit_name)
    return AuthProbeResult(
        success=True,
        read_only=bool(getattr(reddit, "read_only", False)),
        subreddit=display_name,
        subreddit_title=title,
        accessible=True,
        rate_limit=reddit_rate_limits(reddit),
    )


def reddit_rate_limits(reddit: Any) -> dict[str, Any]:
    auth = getattr(reddit, "auth", None)
    limits = getattr(auth, "limits", {})
    if not isinstance(limits, Mapping):
        return {}
    return {str(key): _json_safe(value) for key, value in limits.items()}


def safe_auth_error_message(error: BaseException) -> str:
    error_type = type(error).__name__
    if error_type in {"OAuthException", "ResponseException"}:
        return f"Reddit rejected the OAuth credentials ({error_type})."
    if error_type == "Forbidden":
        return "Reddit denied access to r/ApplyingToCollege (Forbidden)."
    if error_type == "NotFound":
        return "Reddit could not find r/ApplyingToCollege (NotFound)."
    if error_type == "TooManyRequests":
        return "Reddit temporarily rate-limited the authentication check (TooManyRequests)."
    return f"Reddit authentication check failed: {error_type}."


def _resolve_env_path(env_path: str | Path | None) -> Path | None:
    if env_path is not None:
        path = Path(env_path)
        return path if path.is_file() else None

    candidates = (Path.cwd() / ".env", Path(__file__).resolve().parents[2] / ".env")
    for path in candidates:
        if path.is_file():
            return path
    return None


def _read_env_values(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            continue
        name, raw_value = line.split("=", 1)
        name = name.strip()
        if name not in REDDIT_ENV_VARS:
            continue
        values[name] = _dotenv_value(raw_value)
    return values


def _dotenv_value(raw_value: str) -> str:
    value = raw_value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    if " #" in value:
        value = value.split(" #", 1)[0].rstrip()
    return value


def _looks_like_placeholder(value: str) -> bool:
    lowered = value.lower()
    return any(
        marker in lowered
        for marker in ("replace_with", "your_client", "your_reddit", "changeme")
    )


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    return str(value)
