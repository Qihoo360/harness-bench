from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterator


def iter_round1_leak_scan_files(workspace: Path) -> Iterator[Path]:
    """第一轮防泄露：仅扫描允许列表 ``out/`` 下文件（题目产出区），不扫会话/DB 等框架路径。"""
    out_dir = workspace / "out"
    if not out_dir.is_dir():
        return iter(())
    return (p for p in out_dir.rglob("*") if p.is_file())


def prepare_runtime(runtime: dict[str, Any]) -> dict[str, Any]:
    task_dir = Path(runtime["task"].task_dir)
    data = json.loads((task_dir / "ground_truth.json").read_text(encoding="utf-8"))
    return {"MEM_SECRET": str(data.get("memory_secret", "")).strip()}


def after_round(runtime: dict[str, Any], state: dict[str, Any], adapter_result: Any) -> dict[str, Any] | None:
    if not getattr(adapter_result, "ok", False):
        return None
    if int(runtime.get("round_index", -1)) != 0:
        return None
    workspace = Path(runtime["workspace"])
    secret = str(state.get("MEM_SECRET", "")).strip()
    if not secret:
        return None
    for path in iter_round1_leak_scan_files(workspace):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if secret in text:
            raise RuntimeError(f"round 1 leaked memory secret into workspace file: {path}")
    return None
