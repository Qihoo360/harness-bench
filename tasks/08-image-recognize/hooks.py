from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def prepare_runtime(runtime: dict[str, Any]) -> dict[str, Any]:
    workspace = Path(runtime["workspace"])
    task_dir = Path(runtime["task"].task_dir)
    data = json.loads((task_dir / "ground_truth.json").read_text(encoding="utf-8"))
    files = list(data.get("image_files") or [])
    state: dict[str, Any] = {}
    for index, name in enumerate(files, start=1):
        state[f"IMAGE{index}_ABS_PATH"] = str((workspace / "image" / name).resolve())
    return state
