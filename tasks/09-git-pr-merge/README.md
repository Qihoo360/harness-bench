# 09-git-pr-merge

**任务描述：** 本机 **裸库 + clone** 模拟 PR：`feature/pr-add-doc` 比 `main` 多带 `BENCH_PR_OK` 的 `CONTRIBUTING.md`。Agent 需写审查结论、合并并 `push origin main`。

| 文件/目录 | 用途 |
|-----------|------|
| `run.sh` | 创建裸库、推送两分支、clone 到 `WORKSPACE`、demo/live、跑验收 |
| `prompt.txt` | 题面（审查 + 合并 + push） |
| `ground_truth.json` | 分支名、标记串、review 文件路径等 |
| `oracle_grade.py` | `grade_pipeline --workspace`：review / 裸库内容 / main 同步 / 合并祖先 |
| `llm_rubric.py` | LLM 过程评分：审查链路、合并与 push 行为一致性 |
| `verify_oracle.py` | `run.sh` 末尾 Oracle 全通过自检 |
| `grade.sh` | 调 `grade_pipeline`；`--workspace` 依次为：参数、`WORKSPACE`、`.last_workspace`，或通配 `${TMPDIR:-/tmp}/oc-bench-*/workspace`（多目录时取最新 mtime） |
| `.last_workspace` | 各任务 run 成功时写入（见仓库根目录 `.gitignore`），一行路径；本任务 `grade.sh` 默认 Oracle 会读 |
| `.workspace_runs.log` | 各任务同上；每次成功 run 追加：时间戳、`BENCH_SESSION_TAG`、`WORKSPACE` |

**远端在哪：** `origin` 指向本机目录下的 `remote.git`，**不是** GitHub。

**清理：** `run` 结束**不删**目录；**`grade_pipeline` 成功写分后**会删除匹配的 `oc-bench-*/` 整目录（可用 `BENCH_DELETE_AFTER_GRADE=0` 或 `--no-delete-sandbox-after-grade` 保留）。只跑 run、不 grade 时用 **`BENCH_DELETE_SANDBOX_NOW=1`**。
