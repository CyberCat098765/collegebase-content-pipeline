from __future__ import annotations

import html
import re


_WHITESPACE_RE = re.compile(r"[ \t\r\f\v]+")
_BLANK_LINES_RE = re.compile(r"\n{3,}")


def clean_text(text: str) -> str:
    if not text:
        return ""

    decoded = html.unescape(text)
    decoded = decoded.replace("\u00a0", " ").replace("\x00", "")
    decoded = re.sub(r"https?://\S+", lambda match: match.group(0).rstrip(".,)"), decoded)
    lines = [_WHITESPACE_RE.sub(" ", line).strip() for line in decoded.splitlines()]
    non_empty = [line for line in lines if line]
    return _BLANK_LINES_RE.sub("\n\n", "\n".join(non_empty)).strip()


def clean_title(title: str) -> str:
    return clean_text(title).replace("\n", " ")
