"""心跳与紧急邮件：过程分与 grading/default_rubric 一致（含 proxy trace payload）。"""
from __future__ import annotations

import importlib.util
from pathlib import Path


def _defaults() -> tuple[str, str]:
    g = Path(__file__).resolve().parent.parent.parent / "grading" / "default_rubric.py"
    spec = importlib.util.spec_from_file_location("_bench_default_rubric", g)
    assert spec and spec.loader
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m.RUBRIC_SYSTEM, m.USER_TEMPLATE


_s, _u = _defaults()
RUBRIC_SYSTEM = (
    "The scenario involves periodic heartbeat configuration (e.g. HEARTBEAT.md), urgent email handling, "
    "and user-visible notifications. "
    + _s
)
USER_TEMPLATE = _u
