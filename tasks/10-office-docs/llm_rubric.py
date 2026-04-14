"""办公文档任务（CSV / PDF / DOCX）专用 LLM rubric。"""
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

This task: read **sales.csv** and **policy.pdf**, write **out/summary.json** and **out/report.docx** with correct regional totals and policy citation.

Evaluate the agent run (criteria each 0.0-1.0):
- tool_use_appropriate: tools fit reading CSV/PDF, writing JSON and DOCX; irrelevant tools score lower.
- flow_coherence: logical order (inspect policy → parse CSV → compute totals → write summary + memo); penalize useless repeated reads of the same large files.
- error_handling: failures on missing files or bad formats recovered or reported; outputs present when expected.
- reply_appropriateness: for **report.docx** and **summary.json** text/numbers: totals must match policy (exclude return rows), policy id POLICY-2024-Q3 cited, regional and grand totals consistent between JSON and memo. Penalize wrong arithmetic or missing policy reference.

Return ONLY JSON:
{{"scores": {{"tool_use_appropriate": 0.0, "flow_coherence": 0.0, "error_handling": 0.0, "reply_appropriateness": 0.0}}, "total": 0.0, "notes": "one line"}}

total = arithmetic mean of the four scores.

--- TASK DESCRIPTION + GRAPH BELOW ---
{payload}
"""
