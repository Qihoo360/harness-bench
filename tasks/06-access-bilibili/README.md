# 06-access-bilibili

**任务描述：** 用本地静态页模拟列表（不访问真站），按规则从「页面」抽取标题顺序等写入文件；可选 Gateway。

| 文件/目录 | 用途 |
|-----------|------|
| `run.sh` | mock 站 + http.server + agent |
| `prompt.txt` | 题面 |
| `fixtures/www/` | 仿 B 站列表的 HTML |
| `ground_truth.json` | 期望标题顺序、`source_url` 等 |
| `oracle_grade.py` | 结果分 |
| `verify_oracle.py` | 跑后验收 |
| `llm_rubric.py` | LLM 阅卷 |
