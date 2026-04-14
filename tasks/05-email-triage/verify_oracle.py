#!/usr/bin/env python3
"""Oracle：triage 标签、delete_ids；需回复邮件仅要求 out/replies/<id>.txt 存在且非空（质量由 LLM rubric）。"""
from __future__ import annotations

import sys
from pathlib import Path

from oracle_grade import score_workspace


def main() -> int:
    if len(sys.argv) != 3:
        print("usage: verify_oracle.py <WORKSPACE> <ground_truth.json>", file=sys.stderr)
        return 2
    w = Path(sys.argv[1]).resolve()
    gt_path = Path(sys.argv[2]).resolve()
    r = score_workspace(w, ground_truth_path=gt_path)
    if r.get("error"):
        print(f"VERIFY fail: {r['error']}", file=sys.stderr)
        return 1
    checks = r.get("checks") or []
    if not checks:
        print("VERIFY fail email-triage: no checks produced", file=sys.stderr)
        return 1
    for c in checks:
        if not c.get("pass"):
            detail = c.get("detail")
            extra = f" — {detail}" if detail else ""
            print(f"VERIFY fail email-triage: {c.get('label', c.get('id'))}{extra}", file=sys.stderr)
            return 1
    print("VERIFY ok email-triage: labels, delete_ids, non-empty reply files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
