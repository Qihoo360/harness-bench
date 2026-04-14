# 05-email-triage

**任务描述：** 多封邮件 fixtures + 分拣规则，产出分类标签、待删/需回复 id 等；结构化结果走 Oracle，回复质量可走 rubric。

| 文件/目录 | 用途 |
|-----------|------|
| `run.sh` | 拷贝 fixtures、跑 agent |
| `prompt.txt` | 题面 |
| `fixtures/emails.json` | 邮件数据 |
| `ground_truth.json` | 期望标签等（Oracle 参考） |
| `oracle_grade.py` | 结果检查点 |
| `verify_oracle.py` | `run.sh` 末尾自检 |
| `llm_rubric.py` | 含 `reply_appropriateness` 等 |
