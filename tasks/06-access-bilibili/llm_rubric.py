"""任务「访问 bilibili」：本地仿站页上的列表抽取与顺序。"""
from __future__ import annotations

import importlib.util
from pathlib import Path

_g = Path(__file__).resolve().parent.parent.parent / "grading" / "default_rubric.py"
_spec = importlib.util.spec_from_file_location("_bench_default_rubric", _g)
assert _spec and _spec.loader
_dr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_dr)

RUBRIC_SYSTEM = _dr.RUBRIC_SYSTEM

USER_TEMPLATE = """Task name: {task_name}

Theme: **access bilibili-style page** (local mock at **http://127.0.0.1:<PORT>/**). Three videos, play count descending; output **out/titles.txt** and **out/source_url.txt**.

Evaluate (each 0.0-1.0):
- tool_use_appropriate: browser or curl/fetch to open the local mock URL; irrelevant-only runs score lower.
- flow_coherence: load page → extract three titles in list order → write files; penalize useless repeated fetches.
- error_handling: wrong port or empty response recovery where visible.
- reply_appropriateness: **extraction fidelity** — titles and order match the mock page (highest play count first); penalize hallucination if transcript shows no successful read.

Return ONLY JSON:
{{"scores": {{"tool_use_appropriate": 0.0, "flow_coherence": 0.0, "error_handling": 0.0, "reply_appropriateness": 0.0}}, "total": 0.0, "notes": "one line"}}

total = arithmetic mean of the four scores.

--- TASK DESCRIPTION + GRAPH BELOW ---
{payload}
"""
