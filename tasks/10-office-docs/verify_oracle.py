#!/usr/bin/env python3
"""Oracle：summary.json 与 out/report.docx 关键内容。"""
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
        print("VERIFY fail office-docs: no checks produced", file=sys.stderr)
        return 1
    for c in checks:
        if not c.get("pass"):
            detail = c.get("detail")
            extra = f" — {detail}" if detail else ""
            print(f"VERIFY fail office-docs: {c.get('label', c.get('id'))}{extra}", file=sys.stderr)
            return 1
    print("VERIFY ok office-docs: summary.json + report.docx")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
