# 10-office-docs

**任务描述：** 读取 CSV 与真实二进制 PDF 规则，按政策汇总区域金额；产出 **`out/summary.json`** 与修改后的 **`out/report.docx`**；Oracle 校验数值与 DOCX 关键片段。

| 文件/目录 | 用途 |
|-----------|------|
| `run.sh` | 拷贝 fixtures、demo 或 live agent；与其它任务相同，成功且未 `BENCH_DELETE_SANDBOX_NOW=1` 时写入 `.last_workspace` 并追加 `.workspace_runs.log`（见仓库根目录 `.gitignore`） |
| `grade.sh` | 调 `grade_pipeline`；`--workspace`：参数、`$WORKSPACE`、`.last_workspace`、或带 `.bench-10-office-docs` 的 `oc-bench-*/workspace` |
| `prompt.txt` | 题面 |
| `fixtures/sales.csv`、`fixtures/policy.pdf`、`fixtures/template.docx` | 输入（PDF/DOCX 可由 `scripts/build_fixtures.py` 重新生成） |
| `ground_truth.json` | Oracle 期望 |
| `oracle_grade.py` | `score_workspace` |
| `verify_oracle.py` | `run.sh` 末尾自检 |
| `scripts/demo_apply.py` | demo 模式写入合法产出 |
| `llm_rubric.py` | LLM 阅卷 |
