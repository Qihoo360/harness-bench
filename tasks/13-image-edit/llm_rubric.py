"""14-image-edit：风格迁移与场景扩展；四维与 grading 流水线一致，最终结果分由 reply_appropriateness 承担。"""
from __future__ import annotations

import importlib.util
from pathlib import Path

_g = Path(__file__).resolve().parent.parent.parent / "grading" / "default_rubric.py"
_spec = importlib.util.spec_from_file_location("_bench_default_rubric", _g)
assert _spec and _spec.loader
_dr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_dr)

RUBRIC_SYSTEM = _dr.RUBRIC_SYSTEM

USER_TEMPLATE = (
    "Task name: {task_name}\n\n"
    + _dr.RUBRIC_IGNORE_BOOTSTRAP_READS
    + "\n\n"
    "This task: from workspace `in/cat.jpg`, produce:\n"
    "- `out/cat_styled.png` — style transfer (visibly different art style from the original).\n"
    "- `out/cat_scene.png` — scene extension (new background/scene; cat subject still recognizable).\n"
    "- `out/description.txt` — brief notes on style/scene per image and tools or prompts used.\n\n"
    "Criteria (each 0.0–1.0):\n"
    "- tool_use_appropriate: image-related tools and file writes match the task; sensible paths under the workspace.\n"
    "- flow_coherence: logical progression toward all three artifacts; penalize swapping or confusing styled vs scene outputs.\n"
    "- error_handling: if the trace shows no tool failures, score 1.0; otherwise judge recovery or clear reporting.\n"
    "- reply_appropriateness (**task outcome**): from the proxy trace, judge whether the run plausibly produces **both** "
    "distinct edited images and the description file as required; penalize missing outputs, claims of success without evidence, "
    "or outputs that would not meet style/scene distinctness from the prompt.\n\n"
    "Return ONLY JSON (no markdown outside the object):\n"
    '{{"scores": {{"tool_use_appropriate": 0.0, "flow_coherence": 0.0, "error_handling": 0.0, "reply_appropriateness": 0.0}}, '
    '"total": 0.0, "notes": "one line"}}\n\n'
    "total = arithmetic mean of the four scores (the grader may recompute the mean).\n"
    "For this task id, the benchmark combines scores as: outcome = reply_appropriateness, "
    "process = mean of the other three dimensions.\n\n"
    "--- PROXY TRACE JSON BELOW ---\n"
    "{payload}"
)
