from __future__ import annotations

import html
import json
import logging
import re
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import requests

from src.config import TranscriptFileConfig, YouTubeConfig
from src.models import PipelineError, SourceItem
from src.processing.cleaner import clean_text, clean_title

LOGGER = logging.getLogger(__name__)


class YouTubeCollector:
    def __init__(self, config: YouTubeConfig) -> None:
        self.config = config
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "collegebase-content-pipeline/0.1.0"})

    def collect(self, collected_at: str) -> tuple[list[SourceItem], list[PipelineError]]:
        items: list[SourceItem] = []
        errors: list[PipelineError] = []

        for url in self.config.videos:
            item, error = self._collect_video(url, collected_at)
            if item:
                items.append(item)
            if error:
                errors.append(error)

        for transcript_file in self.config.transcript_files:
            item, error = self._collect_transcript_file(transcript_file, collected_at)
            if item:
                items.append(item)
            if error:
                errors.append(error)

        return items, errors

    def _collect_video(
        self, url: str, collected_at: str
    ) -> tuple[SourceItem | None, PipelineError | None]:
        video_id = extract_video_id(url)
        if not video_id:
            return None, PipelineError("youtube", url, "Invalid or unsupported YouTube URL.")

        title, channel = self._fetch_oembed_metadata(url)
        segments, transcript_source, yt_metadata, error = self._fetch_transcript(video_id, url)
        if error:
            return None, PipelineError("youtube", url, error)

        title = title or clean_title(str(yt_metadata.get("title", "")))
        channel = channel or clean_title(
            str(yt_metadata.get("channel") or yt_metadata.get("uploader") or "")
        )
        raw_text = clean_text(" ".join(segment["text"] for segment in segments))
        if not raw_text:
            return None, PipelineError("youtube", url, "Transcript was empty.")

        return (
            SourceItem(
                source_type="youtube",
                source_name=channel or "YouTube",
                title=title or f"YouTube video {video_id}",
                url=url,
                author_or_channel=channel,
                published_date="",
                collected_at=collected_at,
                raw_text=raw_text,
                metadata={
                    "video_id": video_id,
                    "transcript_segment_count": len(segments),
                    "transcript_source": transcript_source,
                    "duration_seconds": yt_metadata.get("duration"),
                    "_transcript_segments": segments,
                },
            ),
            None,
        )

    def _collect_transcript_file(
        self,
        transcript_file: TranscriptFileConfig,
        collected_at: str,
    ) -> tuple[SourceItem | None, PipelineError | None]:
        segments, error = parse_transcript_file(transcript_file.path)
        if error:
            return None, PipelineError("youtube", transcript_file.path, error)

        raw_text = clean_text(" ".join(str(segment["text"]) for segment in segments))
        if not raw_text:
            return None, PipelineError("youtube", transcript_file.path, "Transcript file was empty.")

        return (
            SourceItem(
                source_type="youtube",
                source_name=transcript_file.channel or "Manual transcript",
                title=transcript_file.title,
                url=transcript_file.source_url,
                author_or_channel=transcript_file.channel,
                published_date=transcript_file.published_date,
                collected_at=collected_at,
                raw_text=raw_text,
                metadata={
                    "transcript_source": "manual_file",
                    "transcript_file": transcript_file.path,
                    "transcript_segment_count": len(segments),
                    "_transcript_segments": segments,
                },
            ),
            None,
        )

    def _fetch_oembed_metadata(self, url: str) -> tuple[str, str]:
        try:
            response = self.session.get(
                "https://www.youtube.com/oembed",
                params={"url": url, "format": "json"},
                timeout=self.config.request_timeout_seconds,
            )
            if response.status_code >= 400:
                return "", ""
            data = response.json()
        except (requests.RequestException, ValueError) as exc:
            LOGGER.warning("Could not fetch YouTube oEmbed metadata for %s: %s", url, exc)
            return "", ""

        return clean_title(str(data.get("title", ""))), clean_title(str(data.get("author_name", "")))

    def _fetch_transcript(
        self, video_id: str, url: str
    ) -> tuple[list[dict[str, object]], str, dict[str, object], str | None]:
        api_error = ""
        try:
            from youtube_transcript_api import YouTubeTranscriptApi
        except ImportError:
            api_error = "youtube-transcript-api is not installed."
        else:
            try:
                raw_segments = _fetch_transcript_segments(
                    api_class=YouTubeTranscriptApi,
                    video_id=video_id,
                    languages=self.config.transcript_languages,
                )
                segments = [_normalize_segment(segment) for segment in raw_segments]
                segments = [segment for segment in segments if segment["text"]]
                return segments, "youtube-transcript-api", {}, None
            except Exception as exc:
                api_error = _transcript_error_message(exc)

        yt_segments, yt_metadata, yt_error = self._fetch_yt_dlp_captions(url)
        if yt_segments:
            return yt_segments, "yt-dlp captions", yt_metadata, None

        message = api_error
        if yt_error:
            message = f"{message}; yt-dlp captions unavailable: {yt_error}"
        return [], "", yt_metadata, message

    def _fetch_yt_dlp_captions(
        self, url: str
    ) -> tuple[list[dict[str, object]], dict[str, object], str]:
        try:
            from yt_dlp import YoutubeDL
        except ImportError:
            return [], {}, "yt-dlp is not installed."

        try:
            with YoutubeDL({"quiet": True, "no_warnings": True, "skip_download": True}) as ydl:
                info = ydl.extract_info(url, download=False)
        except Exception as exc:
            return [], {}, type(exc).__name__

        metadata = _yt_dlp_metadata(info)
        for caption in _caption_candidates(info, self.config.transcript_languages):
            caption_url = str(caption.get("url") or "")
            if not caption_url:
                continue
            try:
                response = self.session.get(caption_url, timeout=self.config.request_timeout_seconds)
            except requests.RequestException:
                continue
            if response.status_code >= 400:
                continue

            segments = _parse_caption_payload(response.text, str(caption.get("ext") or ""))
            if segments:
                return segments, metadata, ""

        return [], metadata, "no usable English captions found"


def extract_video_id(url: str) -> str:
    parsed = urlparse(url)
    host = parsed.netloc.lower()

    if host in {"youtu.be", "www.youtu.be"}:
        return parsed.path.strip("/").split("/")[0]

    if "youtube.com" in host:
        query_video_id = parse_qs(parsed.query).get("v", [""])[0]
        if query_video_id:
            return query_video_id

        match = re.search(r"/(?:shorts|embed)/([^/?#]+)", parsed.path)
        if match:
            return match.group(1)

    return ""


def _fetch_transcript_segments(
    api_class: object,
    video_id: str,
    languages: list[str],
) -> object:
    api = api_class()
    if hasattr(api, "fetch"):
        return api.fetch(video_id, languages=languages)

    if hasattr(api_class, "get_transcript"):
        return api_class.get_transcript(video_id, languages=languages)

    if hasattr(api, "list"):
        transcript_list = api.list(video_id)
    else:
        transcript_list = api_class.list_transcripts(video_id)

    transcript = transcript_list.find_transcript(languages)
    return transcript.fetch()


def _transcript_error_message(error: Exception) -> str:
    error_type = type(error).__name__
    if error_type == "IpBlocked":
        return (
            "Transcript unavailable: YouTube blocked transcript requests from this IP. "
            "Retry from a local network or configure a supported proxy."
        )
    return f"Transcript unavailable: {error_type}"


def _yt_dlp_metadata(info: object) -> dict[str, object]:
    if not isinstance(info, dict):
        return {}
    return {
        "title": info.get("title") or "",
        "channel": info.get("channel") or "",
        "uploader": info.get("uploader") or "",
        "duration": info.get("duration"),
        "webpage_url": info.get("webpage_url") or "",
    }


def _caption_candidates(info: object, languages: list[str]) -> list[dict[str, object]]:
    if not isinstance(info, dict):
        return []

    candidates: list[dict[str, object]] = []
    for caption_group in (info.get("subtitles"), info.get("automatic_captions")):
        if not isinstance(caption_group, dict):
            continue
        for language in _language_preferences(languages, caption_group):
            captions = caption_group.get(language, [])
            if isinstance(captions, list):
                candidates.extend(caption for caption in captions if isinstance(caption, dict))

    return sorted(candidates, key=lambda caption: str(caption.get("ext")) != "json3")


def _language_preferences(languages: list[str], caption_group: dict[str, object]) -> list[str]:
    requested = [language for language in languages if language in caption_group]
    english = [language for language in caption_group if language.lower().startswith("en")]
    ordered: list[str] = []
    for language in [*requested, *english]:
        if language not in ordered:
            ordered.append(language)
    return ordered


def _parse_caption_payload(payload: str, extension: str) -> list[dict[str, object]]:
    if extension == "json3":
        return _parse_json3_captions(payload)
    return _parse_vtt_captions(payload)


def parse_transcript_file(path: str | Path) -> tuple[list[dict[str, object]], str | None]:
    transcript_path = Path(path)
    if not transcript_path.exists():
        return [], f"Transcript file not found: {transcript_path}"

    try:
        payload = transcript_path.read_text(encoding="utf-8-sig")
    except OSError as exc:
        return [], f"Could not read transcript file: {type(exc).__name__}"

    suffix = transcript_path.suffix.lower()
    if suffix == ".txt":
        segments = _parse_txt_transcript(payload)
    elif suffix == ".vtt":
        segments = _parse_vtt_captions(payload)
    elif suffix == ".srt":
        segments = _parse_srt_captions(payload)
    else:
        return [], "Transcript files must be .txt, .vtt, or .srt."

    if not segments:
        return [], "No transcript text could be parsed from file."
    return segments, None


def _parse_txt_transcript(payload: str) -> list[dict[str, object]]:
    text = clean_text(payload)
    if not text:
        return []
    return [{"text": text, "start": 0.0, "end": 0.0}]


def _parse_srt_captions(payload: str) -> list[dict[str, object]]:
    return _parse_vtt_captions(payload)


def _parse_json3_captions(payload: str) -> list[dict[str, object]]:
    try:
        data = json.loads(payload)
    except ValueError:
        return []

    segments: list[dict[str, object]] = []
    for event in data.get("events", []):
        if not isinstance(event, dict):
            continue
        text = "".join(
            str(segment.get("utf8", ""))
            for segment in event.get("segs", [])
            if isinstance(segment, dict)
        )
        text = clean_text(text)
        if not text:
            continue
        start = float(event.get("tStartMs", 0) or 0) / 1000
        duration = float(event.get("dDurationMs", 0) or 0) / 1000
        segments.append({"text": text, "start": start, "end": start + duration})
    return segments


def _parse_vtt_captions(payload: str) -> list[dict[str, object]]:
    segments: list[dict[str, object]] = []
    current_start = 0.0
    current_end = 0.0
    current_lines: list[str] = []

    for line in payload.splitlines():
        stripped = line.strip()
        if not stripped or stripped == "WEBVTT" or stripped.startswith("Kind:"):
            if current_lines:
                segments.append(
                    {
                        "text": _clean_caption_text(" ".join(current_lines)),
                        "start": current_start,
                        "end": current_end,
                    }
                )
                current_lines = []
            continue

        if "-->" in stripped:
            if current_lines:
                segments.append(
                    {
                        "text": _clean_caption_text(" ".join(current_lines)),
                        "start": current_start,
                        "end": current_end,
                    }
                )
                current_lines = []
            start, end = stripped.split("-->", 1)
            current_start = _caption_timestamp_seconds(start.strip())
            current_end = _caption_timestamp_seconds(end.split()[0].strip())
            continue

        if not stripped.isdigit():
            current_lines.append(stripped)

    if current_lines:
        segments.append(
            {
                "text": _clean_caption_text(" ".join(current_lines)),
                "start": current_start,
                "end": current_end,
            }
        )

    return [segment for segment in segments if segment["text"]]


def _caption_timestamp_seconds(value: str) -> float:
    parts = value.replace(",", ".").split(":")
    try:
        if len(parts) == 3:
            hours, minutes, seconds = parts
            return int(hours) * 3600 + int(minutes) * 60 + float(seconds)
        if len(parts) == 2:
            minutes, seconds = parts
            return int(minutes) * 60 + float(seconds)
    except ValueError:
        return 0.0
    return 0.0


def _clean_caption_text(text: str) -> str:
    text = html.unescape(text)
    text = re.sub(r"<[^>]+>", "", text)
    return clean_text(text)


def _normalize_segment(segment: object) -> dict[str, object]:
    if isinstance(segment, dict):
        text = clean_text(str(segment.get("text", "")))
        start = float(segment.get("start", 0.0) or 0.0)
        duration = float(segment.get("duration", 0.0) or 0.0)
        end = float(segment.get("end", start + duration) or start + duration)
    else:
        text = clean_text(str(getattr(segment, "text", "")))
        start = float(getattr(segment, "start", 0.0) or 0.0)
        duration = float(getattr(segment, "duration", 0.0) or 0.0)
        end = float(getattr(segment, "end", start + duration) or start + duration)

    return {"text": text, "start": start, "end": end}
