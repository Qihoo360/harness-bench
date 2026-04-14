# 04-meeting-summary

**任务描述：** 阅读 `fixtures/in/meeting_transcript.txt` 长文，写出中文 `out/meeting_summary.txt`。**Oracle** 校验 **字符数区间**（默认 180–480）及必含词 **「Q2」「预算」「里程碑」**；摘要质量由 **LLM rubric** 评是否忠实原文。

| 文件/目录 | 用途 |
|-----------|------|
| `run.sh` | demo / live，无 HTTP 依赖 |
| `prompt.txt` | 题面（字数、关键词、路径） |
| `fixtures/in/meeting_transcript.txt` | 会议纪要全文 |
| `ground_truth.json` | `summary_min_chars` / `summary_max_chars` / `required_phrases` |
| `oracle_grade.py` | `score_workspace` |
| `verify_oracle.py` | run 末尾自检 |
| `scripts/demo_apply.py` | demo 合法摘要 |
