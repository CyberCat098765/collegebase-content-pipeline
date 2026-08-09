from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any, TextIO


def atomic_write_text(
    path: str | Path,
    content: str,
    *,
    encoding: str = "utf-8",
) -> Path:
    """Write text through a temporary file beside the destination."""

    return _atomic_write(
        path,
        lambda handle: handle.write(content),
        encoding=encoding,
    )


def atomic_write_json(
    path: str | Path,
    value: Any,
    *,
    indent: int = 2,
    sort_keys: bool = False,
) -> Path:
    """Atomically write a UTF-8 JSON document with a trailing newline."""

    def write_json(handle: TextIO) -> None:
        json.dump(
            value,
            handle,
            ensure_ascii=False,
            indent=indent,
            sort_keys=sort_keys,
            allow_nan=False,
        )
        handle.write("\n")

    return _atomic_write(path, write_json)


def atomic_write_jsonl(path: str | Path, records: Iterable[Any]) -> Path:
    """Atomically write one compact JSON value per line."""

    def write_jsonl(handle: TextIO) -> None:
        for record in records:
            json.dump(
                record,
                handle,
                ensure_ascii=False,
                separators=(",", ":"),
                allow_nan=False,
            )
            handle.write("\n")

    return _atomic_write(path, write_jsonl)


# Descriptive alias for callers that avoid the JSONL abbreviation.
atomic_write_json_lines = atomic_write_jsonl


def _atomic_write(
    path: str | Path,
    writer: Callable[[TextIO], object],
    *,
    encoding: str = "utf-8",
) -> Path:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp_path: Path | None = None

    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding=encoding,
            newline="\n",
            dir=destination.parent,
            prefix=f".{destination.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temp_path = Path(handle.name)
            writer(handle)
            handle.flush()
            os.fsync(handle.fileno())

        os.replace(temp_path, destination)
        return destination
    except BaseException:
        if temp_path is not None:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass
        raise
