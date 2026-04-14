"""LLM 过程分 rubric；与 grading/default_rubric.py 一致，可按本任务改写。"""
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


RUBRIC_SYSTEM, USER_TEMPLATE = _defaults()
