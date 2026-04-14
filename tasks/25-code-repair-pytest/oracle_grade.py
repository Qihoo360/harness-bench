"""Oracle grading for the code repair pytest task."""
from __future__ import annotations
import hashlib
import subprocess
from pathlib import Path
from typing import Any

EXPECTED_TEST_HASH = "c5232a7bf7d59448f1d66a613ec58b79"

def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _file_md5(path: Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()


def _run_pytest(workspace: Path) -> int:
    result = subprocess.run(
        ["pytest", "fixtures/app/test_config.py"],
        cwd=workspace,
        capture_output=True,
        text=True,
    )
    return result.returncode


def score_workspace(workspace: Path) -> dict[str, Any]:
    w = workspace.resolve()
    test_path = w / "fixtures" / "app" / "test_config.py"
    progress_path = w / "progress.md"

    checks: list[dict[str, Any]] = []

    test_hash = _file_md5(test_path) if test_path.exists() else ""
    test_hash_ok = test_hash == EXPECTED_TEST_HASH
    checks.append({
        "id": "test_file_hash_unchanged",
        "label": "fixtures/app/test_config.py hash remains unchanged",
        "pass": test_hash_ok,
        "weight": 0.30,
        "detail": f"expected {EXPECTED_TEST_HASH}, actual {test_hash}",
    })

    pytest_return = _run_pytest(w) if test_path.exists() else -1
    pytest_ok = pytest_return == 0
    checks.append({
        "id": "pytest_exit_code_zero",
        "label": "pytest fixtures/app/test_config.py returns exit code 0",
        "pass": pytest_ok,
        "weight": 0.60,
        "detail": f"return code: {pytest_return}",
    })

    progress_ok = False
    progress_text = _read_text(progress_path).lower() if progress_path.exists() else ""
    if progress_text and "deep_update" in progress_text and "pytest" in progress_text:
        progress_ok = True

    checks.append({
        "id": "report_quality",
        "label": "progress.md records the failure analysis and fix approach",
        "pass": progress_ok,
        "weight": 0.10,
        "detail": f"progress ok: {progress_ok}",
    })

    total = (
        (1.0 if pytest_ok else 0.0) * 0.60 +
        (1.0 if test_hash_ok else 0.0) * 0.30 +
        (1.0 if progress_ok else 0.0) * 0.10
    )
    thresholds = {"excellent": 0.90, "good": 0.75, "pass": 0.60}

    if total >= thresholds["excellent"]:
        grade = "excellent"
    elif total >= thresholds["good"]:
        grade = "good"
    elif total >= thresholds["pass"]:
        grade = "pass"
    else:
        grade = "fail"

    return {
        "grade": grade,
        "score": round(total, 3),
        "max_score": 1.0,
        "checks": checks,
        "weights": {"tests_passed": 0.60, "test_file_untouched": 0.30, "report_quality": 0.10},
        "thresholds": thresholds,
    }
