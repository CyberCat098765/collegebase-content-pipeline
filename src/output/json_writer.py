from __future__ import annotations

import json
from pathlib import Path

from src.models import PipelineOutput


def write_pipeline_output(output: PipelineOutput, out_path: str | Path) -> None:
    path = Path(out_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(output.to_dict(), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
