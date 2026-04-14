#!/usr/bin/env python3
"""Shell 评测入口：与 oracle_grade 一致。"""
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
    for c in r.get("checks") or []:
        if not c.get("pass"):
            d = c.get("detail")
            extra = f" — {d}" if d else ""
            print(f"VERIFY fail 06-access-bilibili: {c.get('label', c.get('id'))}{extra}", file=sys.stderr)
            return 1
    print("VERIFY ok 06-access-bilibili: play-desc top-3 titles + shape + source_url")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
