"""Oracle：titles.txt 按播放量降序时前三行须与 ground_truth.expected_ordered_titles 一致；格式与 source_url。"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_TASK_DIR = Path(__file__).resolve().parent
_DEFAULT_GT = _TASK_DIR / "ground_truth.json"


def score_workspace(
    workspace: Path,
    *,
    ground_truth_path: Path | None = None,
) -> dict[str, Any]:
    w = workspace.resolve()
    gt_path = ground_truth_path or _DEFAULT_GT
    checks: list[dict[str, Any]] = []

    if not gt_path.is_file():
        return {
            "task": "06-access-bilibili",
            "workspace": str(w),
            "checks": [],
            "outcome_score": 0.0,
            "error": f"missing ground_truth: {gt_path}",
        }

    gt = json.loads(gt_path.read_text(encoding="utf-8"))
    min_lines = int(gt.get("min_line_count", 3))
    min_c = int(gt.get("min_line_chars", 2))
    max_c = int(gt.get("max_line_chars", 240))
    expected: list[str] = list(gt.get("expected_ordered_titles") or [])

    titles_path = w / "out" / "titles.txt"
    lines: list[str] = []
    if titles_path.is_file():
        try:
            raw = titles_path.read_text(encoding="utf-8", errors="strict")
            lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
        except (OSError, UnicodeError):
            lines = []

    need = max(min_lines, len(expected)) if expected else min_lines
    pass_file = titles_path.is_file() and len(lines) >= need
    checks.append(
        {
            "id": "titles_min_lines",
            "label": f"out/titles.txt exists with >= {need} non-empty lines",
            "pass": pass_file,
            "weight": 0.15,
            "detail": None if pass_file else f"got {len(lines)}, need {need}",
        }
    )

    prefix_ok = False
    prefix_detail = None
    if expected:
        if len(lines) < len(expected):
            prefix_detail = f"need {len(expected)} lines for prefix check, got {len(lines)}"
        else:
            mismatches = [
                i
                for i, exp in enumerate(expected)
                if lines[i] != exp
            ]
            if mismatches:
                i = mismatches[0]
                prefix_detail = f"line {i + 1}: expected {expected[i]!r}, got {lines[i]!r}"
            else:
                prefix_ok = True
    else:
        prefix_ok = True
    checks.append(
        {
            "id": "expected_top_by_views",
            "label": "first N lines match expected_ordered_titles (play-count desc snapshot)",
            "pass": prefix_ok,
            "weight": 0.5,
            "detail": prefix_detail,
        }
    )

    bad_fmt = False
    detail_fmt = None
    for i, ln in enumerate(lines):
        if len(ln) < min_c or len(ln) > max_c:
            bad_fmt = True
            detail_fmt = f"line {i + 1} length {len(ln)} not in [{min_c},{max_c}]"
            break
    pass_fmt = bool(lines) and not bad_fmt
    checks.append(
        {
            "id": "title_line_shape",
            "label": f"each title line length in [{min_c},{max_c}]",
            "pass": pass_fmt,
            "weight": 0.2,
            "detail": detail_fmt,
        }
    )

    src = w / "out" / "source_url.txt"
    url_ok = False
    if src.is_file():
        try:
            u = src.read_text(encoding="utf-8", errors="replace").strip()
            url_ok = len(u) >= 8 and ("http" in u or "bilibili" in u.lower())
        except OSError:
            url_ok = False
    checks.append(
        {
            "id": "source_url",
            "label": "out/source_url.txt non-empty and looks like a URL/page ref",
            "pass": url_ok,
            "weight": 0.15,
            "detail": None if url_ok else "missing or too short",
        }
    )

    outcome = round(sum(c["weight"] for c in checks if c["pass"]), 4)
    return {
        "task": "06-access-bilibili",
        "workspace": str(w),
        "checks": checks,
        "outcome_score": outcome,
    }
