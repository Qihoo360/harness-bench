"""ClawBench v2 默认过程分：上下文为 usage-proxy 抽取的 JSON（最后一轮含完整 request_messages）。"""
from __future__ import annotations

RUBRIC_SYSTEM = """You are a strict benchmark grader.
You ONLY output one JSON object, no markdown fences, no extra text.
Score process quality from 0.0 to 1.0 per criterion based on the proxy trace JSON and task description.
Penalize missing tool_result after tool_call, chaotic ordering, excessive pointless repetition of the same tool pattern, or task-required generated text that is inaccurate or off-brief.
Reward clear progression: user task → tool usage → results.
Do not treat reads of standard workspace bootstrap files (SOUL.md, AGENTS.md, MEMORY.md, BOOT.md, TOOLS.md, HEARTBEAT.md, memory/*.md, etc.) as irrelevant tool use—ignore those when judging tool_use_appropriate and flow_coherence unless the task explicitly required different inputs."""

RUBRIC_IGNORE_BOOTSTRAP_READS = """**Bootstrap reads (do not penalize):** The trace may include tool results touching standard workspace files (e.g. SOUL.md, AGENTS.md, MEMORY.md, BOOT.md, TOOLS.md, HEARTBEAT.md, or paths under `memory/`). Do **not** lower tool_use_appropriate or flow_coherence for these alone. Score those dimensions from the parts of the run that fulfill the **stated task** (correct tools, task outputs, recovery from material errors). ENOENT/missing optional bootstrap files should not dominate error_handling if the agent later succeeds on the task."""

USER_TEMPLATE = """Task name: {task_name}

""" + RUBRIC_IGNORE_BOOTSTRAP_READS + """

Evaluate the agent run from the **proxy trace JSON** below: focus on tool/process structure for the first three criteria; reply_appropriateness also judges substantive generated text when the task requires it (see below).

Criteria (each 0.0-1.0):
- tool_use_appropriate: chosen tools match the task (read/write/exec/browser/etc.); ill-suited or irrelevant tools score lower.
- flow_coherence: sequence of user → assistant → tool_call → tool_result is mostly logical and goal-directed; unnecessary repeated calls may lower this score.
- error_handling: If the trace shows **no** tool failures (no error in tool role content / no clear error path that the agent should address), score **1.0**—a clean successful run does not require visible "recovery". Penalize only when failures occur and the agent stalls, ignores them, or repeats the same failing pattern without adaptation.
- reply_appropriateness: whenever the task involves **producing text for a user-visible or graded artifact** (e.g. email replies, summaries, reasons/labels explained in prose, filled templates, file bodies that must satisfy the prompt): judge whether that text is **accurate** (faithful to sources/prompt, no obvious fabrication) and **appropriate** (on-topic, correct tone/format, meets stated constraints). If the run is only tool I/O with no meaningful generation beyond brief status lines, score 1.0 (N/A).

Return ONLY JSON:
{{"scores": {{"tool_use_appropriate": 0.0, "flow_coherence": 0.0, "error_handling": 0.0, "reply_appropriateness": 0.0}}, "total": 0.0, "notes": "one line"}}

total = arithmetic mean of the four scores.

--- PROXY TRACE JSON BELOW ---
{payload}
"""
