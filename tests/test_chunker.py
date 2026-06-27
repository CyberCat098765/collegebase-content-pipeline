from src.processing.chunker import chunk_text, chunk_transcript_segments


def test_chunk_text_splits_long_content() -> None:
    text = " ".join(["college admissions essays matter."] * 80)

    chunks = chunk_text(text, max_chars=200)

    assert len(chunks) > 1
    assert all(len(chunk.text) <= 200 for chunk in chunks)


def test_chunk_transcript_segments_preserves_timestamps() -> None:
    segments = [
        {"text": "Start of the admissions advice.", "start": 0.0, "end": 4.0},
        {"text": "More advice about essays.", "start": 4.0, "end": 9.5},
    ]

    chunks = chunk_transcript_segments(segments, max_chars=200)

    assert len(chunks) == 1
    assert chunks[0].start_timestamp == "00:00:00"
    assert chunks[0].end_timestamp == "00:00:09"
