"""
Task 26: Database & Documentation Consistency Audit
LLM evaluation rubric for qualitative assessment.
"""

REFERENCE = """
Task 26 requires comparing database configuration with deployment documentation 
to identify inconsistencies.

Key Success Criteria:
1. SQL Extraction (30%): Correctly parse backup.sql to extract all system_config entries
   - The database contains: max_db_connections=500, cache_ttl_seconds=600, 
     api_rate_limit=100, default_theme=dark, worker_timeout=30
   - Can use regex on INSERT statements OR import into SQLite for reliable parsing

2. Markdown Extraction (30%): Understand natural language descriptions in deployment_guide.md
   - "最大数据库连接数 (`max_db_connections`)" → 200
   - "缓存过期时间 (`cache_ttl_seconds`)" → 600 秒
   - "全局限流策略 (`api_rate_limit`)" → 500 次请求每分钟
   - "强制退出时间 (`worker_timeout`)" → 120 秒

3. Contradiction Detection (40%): Find items where DB ≠ Doc
   - CRITICAL: Only report contradictions where BOTH sources have the key AND values differ
   - Example: max_db_connections exists in both, DB=500 vs Doc=200 → Include in report
   - Counter-example: cache_ttl_seconds exists in both, DB=600 vs Doc=600 → EXCLUDE from report
   - Do NOT report items that are only in DB or only in Doc

Expected Output Format (audit_report.csv):
```
Config_Key,DB_Value,Doc_Value
max_db_connections,500,200
api_rate_limit,100,500
worker_timeout,30,120
```

Common Mistakes to Avoid:
- Including cache_ttl_seconds (it's consistent, not a contradiction)
- Hallucinating config keys not in the database
- Misreading markdown values (e.g., confusing 200 vs 500)
- Not capturing all three genuine contradictions
- Creating malformed CSV (missing headers or wrong column names)
"""

USER_TEMPLATE = """
Please evaluate the Agent's audit report for Task 26: Database & Documentation Consistency Audit.

The Agent task was to:
1. Parse a SQL database backup (backup.sql) to extract system_config entries
2. Read a Markdown deployment guide and extract configuration parameters
3. Identify and report only the contradictions (items in both sources with different values)
4. Generate audit_report.csv with columns: Config_Key, DB_Value, Doc_Value

Evaluation Dimensions:

** SQL Parsing Competence (30 points max)**
- Did the Agent correctly extract all 5 config entries from backup.sql?
- Was the text parsing approach (regex or SQLite) executed properly?
- Award full points if exact values match: max_db_connections=500, cache_ttl_seconds=600, 
  api_rate_limit=100, worker_timeout=30, default_theme=dark

** Markdown Understanding (30 points max)**
- Did the Agent understand natural language descriptions in the guide?
- Could it map "最大数据库连接数" to max_db_connections and value 200?
- Could it correctly identify api_rate_limit=500, worker_timeout=120?
- Award full points if all 4 documented configs are correctly extracted.

** Contradiction Detection Accuracy (40 points max)**
- Does the final report contain EXACTLY 3 contradictions?
- Are they the correct ones: max_db_connections, api_rate_limit, worker_timeout?
- CRITICAL: Does it correctly EXCLUDE cache_ttl_seconds (values are identical)?
- Are DB_Value and Doc_Value columns numerically accurate?
- Award full points only if all 4 criteria are perfectly met.

Overall Assessment:
- Excellent (90%+): All three dimensions correct with no false positives
- Good (75-89%): Two dimensions correct or minor value mismatches
- Pass (60-74%): One dimension correct OR multiple small errors
- Fail (<60%): Major parsing failures or incorrect contradiction count
"""

DEFAULT_SCORE = 0.0
