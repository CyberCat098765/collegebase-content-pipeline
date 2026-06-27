from pathlib import Path

from src.config import load_config


def test_config_loads_manual_transcript_file(tmp_path: Path) -> None:
    config_path = tmp_path / "sources.yaml"
    transcript_path = tmp_path / "sample.vtt"
    transcript_path.write_text("WEBVTT\n", encoding="utf-8")
    config_path.write_text(
        """
sources:
  youtube:
    videos:
      - url: "https://www.youtube.com/watch?v=abc123abc12"
    transcript_files:
      - path: "sample.vtt"
        source_url: "https://example.com/video"
        title: "Manual Transcript"
processing:
  chunk_max_chars: 900
run:
  max_sources_total: 3
  max_chunks_total: 40
  request_delay_seconds: 0
  runtime_dir: "runtime"
""",
        encoding="utf-8",
    )

    config = load_config(config_path)

    assert config.youtube.videos == ["https://www.youtube.com/watch?v=abc123abc12"]
    assert config.youtube.transcript_files[0].path == str(transcript_path)
    assert config.chunk_max_chars == 900
    assert config.run.max_sources_total == 3
    assert config.run.max_chunks_total == 40
    assert config.run.runtime_dir == "runtime"
