"""多轮会话记忆：两轮 prompt、中间是否把秘密落盘、第二轮是否依赖记忆。"""
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

Two-round **same session-id** benchmark: round 1 stores a passphrase in chat-only rules (must not write secret to workspace); round 2 recalls it to **out/recalled.txt**.

Evaluate (each 0.0-1.0):
- tool_use_appropriate: file writes align with instructions (phase1_done, recalled); no abusive full-disk search for cheating.
- flow_coherence: two user turns in order; assistant acknowledges round 1 then produces recall in round 2; penalize pointless repetition across rounds.
- error_handling: graceful if tools fail mid-way.
- reply_appropriateness: **memory narrative** — does the transcript plausibly show the model **remembered** the secret from turn 1 (vs guessing or obvious file-read of a leaked secret)? Penalize if round-2 behavior clearly contradicts “no secret on disk after round 1”.

Return ONLY JSON:
{{"scores": {{"tool_use_appropriate": 0.0, "flow_coherence": 0.0, "error_handling": 0.0, "reply_appropriateness": 0.0}}, "total": 0.0, "notes": "one line"}}

total = arithmetic mean of the four scores.

--- TASK DESCRIPTION + GRAPH BELOW ---
{payload}
"""
