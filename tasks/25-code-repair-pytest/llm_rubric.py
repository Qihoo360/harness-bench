"""25-code-repair-pytest: LLM evaluation rubric."""
from __future__ import annotations

RUBRIC_SYSTEM = """You are a senior Python developer and bug repair expert.
- Interpret pytest failure output and locate root cause bugs in deep dictionary merge logic.
- Understand the difference between shallow assignment and recursive deep update for nested dictionaries.
- Judge whether the agent preserved test integrity and fixed the source bug correctly.
"""

REFERENCE = """Task overview:
This task requires running pytest on fixtures/app/test_config.py, identifying the shallow merge bug in fixtures/app/config_manager.py, and correcting deep_update so that nested dictionaries are merged recursively rather than overwritten.

Key expectations:
1. Run pytest fixtures/app/test_config.py and inspect the AssertionError.
2. Identify that config_manager.py currently does base_dict[key] = value on nested dictionaries, which drops nested keys.
3. Fix deep_update with isinstance checks and recursive updates for dict values.
4. Do not modify test_config.py or change test assertions.
5. Rerun pytest until the test suite exits with code 0.
"""

USER_TEMPLATE = """## Task: 25-code-repair-pytest Deep Dictionary Merge Bug Fix with Pytest Closure

{reference}

---

## Agent Output

{payload}

---

## Scoring dimensions (0.0-1.0)

1. tests_passed:
   - Did the agent run pytest on fixtures/app/test_config.py?
   - Did pytest exit with code 0 after the fix?
2. test_file_untouched:
   - Did the agent preserve fixtures/app/test_config.py unchanged?
   - Did the agent avoid cheating by altering the test file?
3. report_quality:
   - Does progress.md explain the failure, root cause, and recursive fix?
   - Does it mention pytest verification?

---

Return only JSON with the following keys:
{
  "tests_passed": 0.0,
  "test_file_untouched": 0.0,
  "report_quality": 0.0
}
"""


def get_rubric() -> tuple[str, str, str]:
    return RUBRIC_SYSTEM, REFERENCE, USER_TEMPLATE
