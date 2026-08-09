from __future__ import annotations

import copy
import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.reddit_discovery.storage import atomic_write_json


CHECKPOINT_SCHEMA_VERSION = 1
ROUTE_STATUSES = frozenset({"pending", "in_progress", "completed", "failed"})


@dataclass(slots=True)
class DiscoveryCheckpoint:
    run_fingerprint: str
    routes: dict[str, dict[str, Any]] = field(default_factory=dict)
    updated_at: str = ""
    load_errors: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.routes = dict(self.routes)
        self.load_errors = list(self.load_errors)

    @classmethod
    def load(
        cls,
        path: str | Path,
        *,
        expected_run_fingerprint: str | None = None,
    ) -> DiscoveryCheckpoint:
        source = Path(path)
        empty_fingerprint = expected_run_fingerprint or ""
        if not source.exists():
            return cls(run_fingerprint=empty_fingerprint)
        try:
            payload = json.loads(source.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            return cls(
                run_fingerprint=empty_fingerprint,
                load_errors=[f"Could not load checkpoint {source}: {exc}"],
            )
        if not isinstance(payload, Mapping):
            return cls(
                run_fingerprint=empty_fingerprint,
                load_errors=[f"Checkpoint {source} is not a JSON object"],
            )

        schema_version = payload.get("schema_version", CHECKPOINT_SCHEMA_VERSION)
        if schema_version != CHECKPOINT_SCHEMA_VERSION:
            return cls(
                run_fingerprint=empty_fingerprint,
                load_errors=[
                    f"Checkpoint {source} uses unsupported schema version {schema_version!r}",
                ],
            )

        actual_fingerprint = str(payload.get("run_fingerprint", ""))
        if expected_run_fingerprint and actual_fingerprint != expected_run_fingerprint:
            return cls(
                run_fingerprint=expected_run_fingerprint,
                load_errors=[
                    "Checkpoint run fingerprint does not match the current discovery plan; "
                    "starting with empty route state",
                ],
            )

        routes = payload.get("routes", {})
        if not isinstance(routes, Mapping):
            return cls(
                run_fingerprint=actual_fingerprint or empty_fingerprint,
                load_errors=[f"Checkpoint {source} has no valid routes map"],
            )

        checkpoint = cls(
            run_fingerprint=actual_fingerprint or empty_fingerprint,
            updated_at=str(payload.get("updated_at", "")),
        )
        for route_id, route in routes.items():
            if not isinstance(route, Mapping):
                checkpoint.load_errors.append(f"Ignored malformed checkpoint route: {route_id}")
                continue
            normalized = _normalize_route(str(route_id), route)
            if normalized is None:
                checkpoint.load_errors.append(
                    f"Ignored checkpoint route with invalid state or fingerprint: {route_id}"
                )
                continue
            checkpoint.routes[str(route_id)] = normalized
        return checkpoint

    def set_route_state(
        self,
        route_id: str,
        *,
        route_fingerprint: str,
        status: str,
        state: Mapping[str, Any] | None,
        updated_at: str,
        error: str | None = None,
    ) -> dict[str, Any]:
        route_key = route_id.strip()
        if not route_key:
            raise ValueError("route_id must not be empty")
        if not route_fingerprint.strip():
            raise ValueError("route_fingerprint must not be empty")
        if status not in ROUTE_STATUSES:
            allowed = ", ".join(sorted(ROUTE_STATUSES))
            raise ValueError(f"route status must be one of: {allowed}")
        if state is not None and not isinstance(state, Mapping):
            raise TypeError("route checkpoint state must be a mapping")

        route = {
            "route_id": route_key,
            "fingerprint": route_fingerprint,
            "status": status,
            "state": copy.deepcopy(dict(state or {})),
            "updated_at": updated_at,
            "error": error,
        }
        self.routes[route_key] = route
        self.updated_at = updated_at
        return copy.deepcopy(route)

    def mark_route_started(
        self,
        route_id: str,
        *,
        route_fingerprint: str,
        state: Mapping[str, Any] | None,
        updated_at: str,
    ) -> dict[str, Any]:
        return self.set_route_state(
            route_id,
            route_fingerprint=route_fingerprint,
            status="in_progress",
            state=state,
            updated_at=updated_at,
        )

    def mark_route_completed(
        self,
        route_id: str,
        *,
        route_fingerprint: str,
        state: Mapping[str, Any] | None,
        updated_at: str,
    ) -> dict[str, Any]:
        return self.set_route_state(
            route_id,
            route_fingerprint=route_fingerprint,
            status="completed",
            state=state,
            updated_at=updated_at,
        )

    def mark_route_failed(
        self,
        route_id: str,
        *,
        route_fingerprint: str,
        state: Mapping[str, Any] | None,
        updated_at: str,
        error: str,
    ) -> dict[str, Any]:
        return self.set_route_state(
            route_id,
            route_fingerprint=route_fingerprint,
            status="failed",
            state=state,
            updated_at=updated_at,
            error=error,
        )

    def get_route(
        self,
        route_id: str,
        *,
        route_fingerprint: str | None = None,
    ) -> dict[str, Any] | None:
        route = self.routes.get(route_id)
        if route is None:
            return None
        if route_fingerprint is not None and route.get("fingerprint") != route_fingerprint:
            return None
        return copy.deepcopy(route)

    def resume_state(
        self,
        route_id: str,
        *,
        route_fingerprint: str,
    ) -> dict[str, Any] | None:
        route = self.get_route(route_id, route_fingerprint=route_fingerprint)
        if route is None or route.get("status") == "completed":
            return None
        state = route.get("state")
        return copy.deepcopy(state) if isinstance(state, dict) else None

    def is_route_complete(self, route_id: str, *, route_fingerprint: str) -> bool:
        route = self.get_route(route_id, route_fingerprint=route_fingerprint)
        return bool(route and route.get("status") == "completed")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": CHECKPOINT_SCHEMA_VERSION,
            "run_fingerprint": self.run_fingerprint,
            "updated_at": self.updated_at,
            "routes": {key: copy.deepcopy(self.routes[key]) for key in sorted(self.routes)},
        }


def stable_fingerprint(value: Any) -> str:
    """Create a stable resume fingerprint for a plan or individual route."""

    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def load_checkpoint(
    path: str | Path,
    *,
    expected_run_fingerprint: str | None = None,
) -> DiscoveryCheckpoint:
    return DiscoveryCheckpoint.load(
        path,
        expected_run_fingerprint=expected_run_fingerprint,
    )


def save_checkpoint(checkpoint: DiscoveryCheckpoint | Mapping[str, Any], path: str | Path) -> Path:
    payload = checkpoint.to_dict() if isinstance(checkpoint, DiscoveryCheckpoint) else copy.deepcopy(checkpoint)
    return atomic_write_json(path, payload, sort_keys=True)


def _normalize_route(route_id: str, route: Mapping[str, Any]) -> dict[str, Any] | None:
    fingerprint = str(route.get("fingerprint", "")).strip()
    status = str(route.get("status", "pending"))
    state = route.get("state", {})
    if not fingerprint or status not in ROUTE_STATUSES or not isinstance(state, Mapping):
        return None
    return {
        "route_id": str(route.get("route_id", route_id)),
        "fingerprint": fingerprint,
        "status": status,
        "state": copy.deepcopy(dict(state)),
        "updated_at": str(route.get("updated_at", "")),
        "error": route.get("error"),
    }
