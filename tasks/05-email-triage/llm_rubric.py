"""邮件分拣任务专用 LLM rubric（覆盖 default 的 USER_TEMPLATE）。"""
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

This task: read workspace **emails.json**, write **out/triage.json**, **out/delete_ids.txt**, and **out/replies/<id>.txt** for each needs_reply mail.

Evaluate the agent run (criteria each 0.0-1.0):
- tool_use_appropriate: tools fit file read/write, JSON handling, mkdir; irrelevant tools score lower.
- flow_coherence: logical order (read emails → classify → write triage → delete list → reply files); penalize useless repeated reads/writes of the same paths.
- error_handling: tool failures recovered or avoided; successful writes visible where expected.
- reply_appropriateness: for **reply body text** the agent (or tools) place in **out/replies/*.txt**: must **directly answer** each sender’s ask (e.g. availability, sending meeting notes). Penalize off-topic or generic fluff. For **triage.json** short reasons, penalize labels/reasons that contradict the email content. If the transcript shows no substantive reply drafting step, score 1.0 only if the task did not require replies yet; otherwise score lower.

Return ONLY JSON:
{{"scores": {{"tool_use_appropriate": 0.0, "flow_coherence": 0.0, "error_handling": 0.0, "reply_appropriateness": 0.0}}, "total": 0.0, "notes": "one line"}}

total = arithmetic mean of the four scores.

--- TASK DESCRIPTION + GRAPH BELOW ---
{payload}
"""
