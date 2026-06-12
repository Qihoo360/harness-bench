# Rubric Bugs in Public Task Set (`tasks/`)

This report documents defects found in the bundled LLM process-grading rubrics
(`tasks/<id>/llm_rubric.py`). These are bugs in the **official task fixtures**, not
in any adapter. When a rubric is defective, the harness cannot compute a `process`
score for that task, so its `process` and `combined` results are reported as **N/A**
(only `outcome`/Completion is available).

We did **not** modify the task sources to work around these. They are listed here so
the upstream maintainers can fix them. A drop-in patch suggestion is given per class.

## Impact summary

| # | Failure class | Tasks affected | When it fails | Effect |
|---|---------------|----------------|---------------|--------|
| 1 | Module-load `.format()` KeyError | `17-like-record`, `18-album-metadata-retrieval` | at `import` of the rubric module | rubric cannot load → process N/A |
| 2 | Render-time `.format()` KeyError | `19-landmark-recognition`, `20-football-shot-map-analysis`, `21-US-bank-failures-history`, `22-log-troubleshooting`, `23-supply-chain-alert`, `24-security-injection-defense`, `25-code-repair-pytest`, `27-provider-failover-audit`, `28-incident-runbook-synthesis`, `29-heartbeat-escalation` | when grader formats `USER_TEMPLATE` | rubric cannot render → process N/A |
| 3 | Output schema mismatch (flat / no `total`) | several of the above once they render | after the grader replies | parser yields no `total` → process N/A |

12 of 28 tasks cannot produce a process score because of classes 1–2 alone.

---

## Bug 1 — `.format()` runs at module load and chokes on JSON braces

**Files:** `tasks/17-like-record/llm_rubric.py`, `tasks/18-album-metadata-retrieval/llm_rubric.py`

The module ends with (17-like-record:102):

```python
USER_TEMPLATE = """... { "scores": { ... } } ...""".format(reference=_reference_block(), payload="{payload}")
```

`str.format()` treats every `{` / `}` as a field. The literal JSON example inside the
template — e.g. `{\n  "scores": ...}` — is parsed as a replacement field named
`'\n  "scores"'`, raising `KeyError: '\n  "scores"'` **at import time**. The rubric
module never loads.

**Fix:** escape every literal brace that is not a real placeholder (`{{` / `}}`), or
build the template without a module-level `.format()` (use `.replace("{reference}", _reference_block())`
and keep `{payload}` as the only real placeholder).

---

## Bug 2 — `USER_TEMPLATE` contains an unescaped `{reference}` / `{...}`

**Files:** `19-landmark-recognition`, `20-football-shot-map-analysis`,
`21-US-bank-failures-history`, `22-log-troubleshooting`, `23-supply-chain-alert`,
`24-security-injection-defense`, `25-code-repair-pytest`, `27-provider-failover-audit`,
`28-incident-runbook-synthesis`, `29-heartbeat-escalation`

The harness renders the rubric with:

```python
# src/clawbench_v2/process_grading.py
user = template.format(task_name=task_id, payload=payload)
```

Only `{task_name}` and `{payload}` are supplied. But these templates contain other
unescaped braces:

- `{reference}` left as a literal placeholder (e.g. `19-landmark-recognition:60`), and/or
- the JSON example block `{ "scores": {...} }` (e.g. `27-provider-failover-audit`).

Rendering raises `KeyError: 'reference'` or `KeyError: '\n  "scores"'`, so the rubric
is skipped and process is N/A.

**Fix:** inline `{reference}` into the string at build time (it is static once
`_reference_block()` is evaluated), and escape all JSON-example braces to `{{` / `}}`.
After the fix `USER_TEMPLATE.format(task_name=..., payload=...)` must succeed with only
those two keys.

---

## Bug 3 — Grader output schema does not match the parser contract

After Bugs 1–2 are fixed and the rubric renders, several templates instruct the grader
to return a **flat** object or omit `total`:

```json
{"vision_recognition_accuracy": 1.0, "knowledge_retrieval_accuracy": 1.0, ...}
```

while the harness parser (`_format_rubric_response`) expects the default-rubric contract:

```json
{"scores": { ...dimensions... }, "total": 0.0, "notes": "..."}
```

With no `scores` wrapper and no `total`, the parser returns `total = None` → process N/A.
`26-db-doc-consistency` is worse: its template uses a 0–100 points prose scheme with no
JSON instruction at all, so the grader replies in Markdown.

**Fix (task side):** make every rubric emit the standard
`{"scores": {...}, "total": <mean>, "notes": "..."}` contract, matching
`grading/default_rubric.py`.

**Mitigation (harness side, already applied in this fork):**
`src/clawbench_v2/process_grading.py` was hardened so the parser is tolerant of
benign grader-output variation **without** masking the task bugs above:
- accept a flat `{dimension: score}` object and treat top-level numeric fields as the
  dimension scores;
- compute `total` as the arithmetic mean of the dimension scores when `total` is absent;
- when the grader emits several JSON objects (plan/status preamble + final verdict),
  pick the last scoring-shaped object instead of the first.

This mitigation only recovers process scores for rubrics that *render and run*. Tasks
broken by Bug 1 or Bug 2 still produce **no process score** until the task sources are
fixed upstream — those remain reported as N/A by design.

---

## Reproduction

```bash
python - <<'PY'
import importlib.util
from pathlib import Path
for name in ["17-like-record","19-landmark-recognition","27-provider-failover-audit"]:
    p = Path("tasks")/name/"llm_rubric.py"
    spec = importlib.util.spec_from_file_location("r", p)
    m = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(m)              # Bug 1 fails here
        m.USER_TEMPLATE.format(task_name=name, payload="{}")  # Bug 2 fails here
        print(name, "OK")
    except Exception as e:
        print(name, type(e).__name__, repr(str(e)))
PY
```
