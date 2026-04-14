"""03-browser：Oracle 只判摘录文件；是否用浏览器/curl 等访问本地页由过程分体现。"""
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

This task: visit **http://127.0.0.1:<HTTP_PORT>/** (local static page), then write visible page content into **out/page_extract.txt**. Oracle only checks that file contains a fixed substring; it does **not** verify HTTP or which tool was used.

Evaluate the agent run (criteria each 0.0-1.0):
- tool_use_appropriate: **prioritize** use of **browser** and/or **terminal fetch** (curl/wget) against the given URL, and file write to **out/page_extract.txt**; penalize skipping page access if the transcript shows no reasonable way the excerpt could come from the page.
- flow_coherence: open/fetch page → extract relevant text → write **out/page_extract.txt** (or equivalent).
- error_handling: connection/refusal or wrong port handled or retried; write failures surfaced.
- reply_appropriateness: if the task requires user-facing prose beyond the graded file, judge accuracy; otherwise 1.0 when the run is tool-focused.

Return ONLY JSON:
{{"scores": {{"tool_use_appropriate": 0.0, "flow_coherence": 0.0, "error_handling": 0.0, "reply_appropriateness": 0.0}}, "total": 0.0, "notes": "one line"}}

total = arithmetic mean of the four scores.

--- TASK DESCRIPTION + GRAPH BELOW ---
{payload}
"""
