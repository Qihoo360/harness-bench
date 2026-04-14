# 02-exec

**任务描述：** 在 workspace 内用终端完成三步：算术展开写出 **`42`**、`basename` 得到 **`c.txt`**、管道 **`tr`** 得到 **`hello`**，分别写入 `out/step1.txt`～`step3.txt`（Oracle 与 `run.sh` 一致）。

| 文件/目录 | 用途 |
|-----------|------|
| `run.sh` | 入口：demo 模拟成功路径 / live 调 OpenClaw |
| `prompt.txt` | 任务说明（类 terminal bench：算术 / basename / 管道） |
| `oracle_grade.py` | 三文件各 1/3 权重 |
| `llm_rubric.py` | LLM 阅卷提示 |
