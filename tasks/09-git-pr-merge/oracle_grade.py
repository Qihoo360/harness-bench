"""Oracle：审查结论 APPROVE、裸库 main 含 CONTRIBUTING 标记、已与工作区 push 一致、PR 顶提交已进入 main 历史。"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

_TASK_DIR = Path(__file__).resolve().parent
_GT = _TASK_DIR / "ground_truth.json"


def _git(cwd: Path, *args: str, timeout: int = 60) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(cwd), *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def _origin_to_bare_path(url: str) -> Path | None:
    u = url.strip()
    if u.startswith("file:"):
        # file:///path or file:/path
        from urllib.parse import unquote, urlparse

        p = urlparse(u).path
        if p.startswith("/") and len(p) > 2 and p[2] == ":":
            p = p[1:]
        return Path(unquote(p))
    p = Path(u)
    if p.is_absolute() and p.exists():
        return p
    return None


def score_workspace(workspace: Path) -> dict[str, Any]:
    w = workspace.resolve()
    weight = 0.25
    checks: list[dict[str, Any]] = []

    if not _GT.is_file():
        return {
            "task": "09-git-pr-merge",
            "workspace": str(w),
            "checks": [],
            "outcome_score": 0.0,
            "error": f"missing ground_truth.json: {_GT}",
        }

    gt = json.loads(_GT.read_text(encoding="utf-8"))
    pr_branch = str(gt.get("pr_branch") or "feature/pr-add-doc")
    marker = str(gt.get("approve_marker_in_contributing") or "BENCH_PR_OK")
    review_rel = str(gt.get("review_relative_path") or "out/review.txt")
    approve_needle = str(gt.get("approve_line_must_start_with") or "APPROVE")

    if not (w / ".git").is_dir():
        return {
            "task": "09-git-pr-merge",
            "workspace": str(w),
            "checks": [],
            "outcome_score": 0.0,
            "error": "workspace is not a git clone (.git missing)",
        }

    gr = _git(w, "remote", "get-url", "origin")
    bare: Path | None = None
    if gr.returncode == 0 and gr.stdout.strip():
        bare = _origin_to_bare_path(gr.stdout.strip())
    if bare is None or not bare.is_dir():
        checks.append(
            {
                "id": "origin_bare",
                "label": "resolve origin to local bare repo path",
                "pass": False,
                "weight": weight,
                "detail": gr.stdout.strip() if gr.returncode == 0 else gr.stderr.strip(),
            }
        )
        outcome = 0.0
        return {
            "task": "09-git-pr-merge",
            "workspace": str(w),
            "checks": checks,
            "outcome_score": outcome,
        }

    # 1) review.txt first meaningful line starts with APPROVE
    review_path = w / Path(review_rel)
    approve_ok = False
    detail_review = None
    if review_path.is_file():
        try:
            for line in review_path.read_text(encoding="utf-8", errors="replace").splitlines():
                s = line.strip()
                if not s:
                    continue
                approve_ok = s.upper().startswith(approve_needle.upper())
                if not approve_ok:
                    detail_review = f"first non-empty line: {s[:120]!r}"
                break
            else:
                detail_review = "empty file"
        except OSError as e:
            detail_review = str(e)
    else:
        detail_review = "missing review file"
    checks.append(
        {
            "id": "review_approve",
            "label": f"{review_rel} first non-empty line starts with {approve_needle!r}",
            "pass": approve_ok,
            "weight": weight,
            "detail": None if approve_ok else detail_review,
        }
    )

    # 2) CONTRIBUTING.md on bare main contains marker
    show = _git(bare, "show", "main:CONTRIBUTING.md")
    contrib_ok = show.returncode == 0 and marker in (show.stdout or "")
    checks.append(
        {
            "id": "bare_contributing",
            "label": f"bare main:CONTRIBUTING.md contains {marker!r}",
            "pass": contrib_ok,
            "weight": weight,
            "detail": None if contrib_ok else (show.stderr.strip() or show.stdout[:200]),
        }
    )

    # 3) workspace main == bare main (pushed)
    wm = _git(w, "rev-parse", "main")
    bm = _git(bare, "rev-parse", "main")
    push_ok = (
        wm.returncode == 0
        and bm.returncode == 0
        and wm.stdout.strip() == bm.stdout.strip()
        and len(wm.stdout.strip()) >= 7
    )
    checks.append(
        {
            "id": "main_synced",
            "label": "local main SHA equals bare refs/heads/main (push ok)",
            "pass": push_ok,
            "weight": weight,
            "detail": None
            if push_ok
            else f"ws={wm.stdout.strip()[:12]} bare={bm.stdout.strip()[:12]}",
        }
    )

    # 4) PR branch tip is ancestor of main on bare (merged)
    tip = _git(bare, "rev-parse", pr_branch)
    anc = _git(bare, "merge-base", "--is-ancestor", tip.stdout.strip(), "main")
    merge_ok = tip.returncode == 0 and anc.returncode == 0
    checks.append(
        {
            "id": "pr_merged",
            "label": f"bare: tip of {pr_branch!r} is ancestor of main",
            "pass": merge_ok,
            "weight": weight,
            "detail": None if merge_ok else tip.stderr.strip() or anc.stderr.strip(),
        }
    )

    outcome = round(sum(c["weight"] for c in checks if c["pass"]), 4)
    return {
        "task": "09-git-pr-merge",
        "workspace": str(w),
        "checks": checks,
        "outcome_score": outcome,
    }
