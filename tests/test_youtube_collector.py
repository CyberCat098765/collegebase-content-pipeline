from pathlib import Path

from src.collectors.youtube_collector import (
    _parse_srt_captions,
    _parse_txt_transcript,
    _parse_vtt_captions,
    parse_transcript_file,
)


def test_parse_vtt_captions_extracts_text_and_timestamps() -> None:
    payload = """WEBVTT

00:00:01.000 --> 00:00:04.500
College essays should sound specific.

00:00:05.000 --> 00:00:07.000
Use details from your own application story.
"""

    segments = _parse_vtt_captions(payload)

    assert segments == [
        {
            "text": "College essays should sound specific.",
            "start": 1.0,
            "end": 4.5,
        },
        {
            "text": "Use details from your own application story.",
            "start": 5.0,
            "end": 7.0,
        },
    ]


def test_parse_srt_captions_extracts_text_and_timestamps() -> None:
    payload = """1
00:00:02,000 --> 00:00:05,500
Start the activities list with clear verbs.

2
00:00:06,000 --> 00:00:09,000
Add measurable details when they are accurate.
"""

    segments = _parse_srt_captions(payload)

    assert segments[0]["text"] == "Start the activities list with clear verbs."
    assert segments[0]["start"] == 2.0
    assert segments[0]["end"] == 5.5
    assert segments[1]["text"] == "Add measurable details when they are accurate."


def test_parse_txt_transcript_returns_single_segment() -> None:
    segments = _parse_txt_transcript(" College essays need specific examples. ")

    assert segments == [
        {
            "text": "College essays need specific examples.",
            "start": 0.0,
            "end": 0.0,
        }
    ]


def test_parse_transcript_file_supports_vtt(tmp_path: Path) -> None:
    transcript = tmp_path / "sample.vtt"
    transcript.write_text(
        "WEBVTT\n\n00:00:01.000 --> 00:00:03.000\nAsk for recommendations early.\n",
        encoding="utf-8",
    )

    segments, error = parse_transcript_file(transcript)

    assert error is None
    assert segments[0]["text"] == "Ask for recommendations early."
