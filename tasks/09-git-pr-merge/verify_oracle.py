#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

from oracle_grade import score_workspace


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: verify_oracle.py <WORKSPACE>", file=sys.stderr)
        return 2
    w = Path(sys.argv[1]).resolve()
    r = score_workspace(w)
    if r.get("error") and not r.get("checks"):
        print(f"VERIFY fail: {r['error']}", file=sys.stderr)
        return 1
    for c in r.get("checks") or []:
        if not c.get("pass"):
            d = c.get("detail")
            extra = f" — {d}" if d else ""
            print(f"VERIFY fail 09-git-pr-merge: {c.get('label', c.get('id'))}{extra}", file=sys.stderr)
            return 1
    sc = float(r.get("outcome_score") or 0.0)
    if sc < 1.0:
        print(f"VERIFY fail 09-git-pr-merge: outcome_score={sc}", file=sys.stderr)
        return 1
    print("VERIFY ok 09-git-pr-merge: review + bare main + push + merge ancestry")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
