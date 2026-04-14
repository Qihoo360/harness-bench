"""04：会议纪要摘要任务 LLM 阅卷。"""
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

This task: read **in/meeting_transcript.txt**, write **out/meeting_summary.txt** (Chinese meeting summary) with length in the allowed character range and required phrases (Q2, 预算, 里程碑).

Evaluate the agent run (criteria each 0.0-1.0):
- tool_use_appropriate: read/write tools for transcript and summary; irrelevant tools score lower.
- flow_coherence: read source → draft summary → write out/meeting_summary.txt.
- error_handling: missing file or encoding issues handled or reported.
- reply_appropriateness: summary must **faithfully reflect** the transcript (decisions, budget, milestones, risks); penalize fabrication or ignoring key points. Length/oracle constraints are scored separately; focus on content accuracy and tone appropriate for internal minutes.

Return ONLY JSON:
{{"scores": {{"tool_use_appropriate": 0.0, "flow_coherence": 0.0, "error_handling": 0.0, "reply_appropriateness": 0.0}}, "total": 0.0, "notes": "one line"}}

total = arithmetic mean of the four scores.

--- TASK DESCRIPTION + GRAPH BELOW ---
{payload}
"""
