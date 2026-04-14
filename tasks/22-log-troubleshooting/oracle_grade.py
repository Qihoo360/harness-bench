"""Oracle评分：检验生产环境长日志排查与依赖修复任务的输出质量"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _python_syntax_ok(path: Path) -> bool:
    result = subprocess.run(["python3", "-m", "py_compile", str(path)], capture_output=True, text=True)
    return result.returncode == 0


def _requirements_format_ok(path: Path) -> bool:
    lines = [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip() and not line.strip().startswith("#")]
    return all(re.match(r"^[A-Za-z0-9_.+-]+==[0-9]+(?:\.[0-9]+)*$", line) for line in lines)


def _git_commit_exists(workspace: Path, commit_hash: str) -> bool:
    try:
        completed = subprocess.run(
            ["git", "cat-file", "-e", f"{commit_hash}^{{commit}}"],
            cwd=workspace,
            capture_output=True,
            text=True,
            check=False,
        )
        return completed.returncode == 0
    except Exception:
        return False


def score_workspace(workspace: Path) -> dict[str, Any]:
    w = workspace.resolve()
    task_dir = Path(__file__).resolve().parent
    gt_path = task_dir / "ground_truth.json"
    ground_truth = json.loads(gt_path.read_text(encoding="utf-8")) if gt_path.exists() else {}

    weights = ground_truth.get("scoring", {}).get("weights", {
        "error_analysis_accuracy": 0.30,
        "git_tracing": 0.20,
        "minimal_env_fix": 0.30,
        "report_quality": 0.20,
    })

    validation_constraints = ground_truth.get("validation_constraints", {})
    expected_commit_hash = validation_constraints.get("expected_commit_hash", "a1b2c3d")
    expected_urllib3_version = validation_constraints.get("expected_urllib3_version", "1.26.16")
    conflicting_package = validation_constraints.get("conflicting_package", "botocore")
    conflict_snippet = validation_constraints.get(
        "conflict_snippet",
        "botocore 1.29.76 depends on urllib3<1.27 and >=1.25.4",
    )

    checks: list[dict[str, Any]] = []

    # ========== 1. 报错分析准确性 (30%) ==========
    error_score = 0.0
    log_path = task_dir / "fixtures" / "logs" / "build.log"
    error_snippet_found = False
    if log_path.exists():
        log_text = _read_text(log_path)
        error_snippet_found = conflict_snippet in log_text or "ContextualVersionConflict" in log_text
        checks.append({
            "id": "conflict_snippet_present",
            "label": "日志中存在预设依赖冲突信息",
            "pass": error_snippet_found,
            "weight": 0.15,
            "detail": f"冲突片段匹配: {error_snippet_found}"
        })
        if error_snippet_found:
            error_score += 0.15
    else:
        checks.append({
            "id": "build_log_missing",
            "label": "缺少 fixtures/logs/build.log",
            "pass": False,
            "weight": 0.15,
            "detail": "日志文件不存在"
        })

    # ========== 2. Git 溯源与责任定位 (20%) ==========
    git_score = 0.0
    if expected_commit_hash:
        commit_exists = _git_commit_exists(w, expected_commit_hash)
        checks.append({
            "id": "git_commit_exists",
            "label": f"责任 Commit 是否存在: {expected_commit_hash}",
            "pass": commit_exists,
            "weight": 0.20,
            "detail": f"commit exists: {commit_exists}"
        })
        if commit_exists:
            git_score += 0.20
    else:
        checks.append({
            "id": "git_commit_check_missing",
            "label": "未配置 expected_commit_hash",
            "pass": False,
            "weight": 0.20,
            "detail": "Ground truth 缺少 commit hash" 
        })

    # ========== 3. 最小环境修复 (30%) ==========
    fix_score = 0.0
    requirements_path = w / "requirements.txt"
    requirements_fixture_path = task_dir / "fixtures" / "requirements.txt"
    if requirements_path.exists() and requirements_fixture_path.exists():
        requirements_text = _read_text(requirements_path).strip().splitlines()
        fixture_text = _read_text(requirements_fixture_path).strip().splitlines()

        only_changed = []
        for orig, new in zip(fixture_text, requirements_text):
            if orig != new:
                only_changed.append((orig, new))

        urllib3_fixed = any(
            orig.startswith("urllib3==") and new == f"urllib3=={expected_urllib3_version}"
            for orig, new in only_changed
        )
        no_other_changes = len(only_changed) == 1

        checks.append({
            "id": "urllib3_downgrade",
            "label": f"requirements.txt 仅将 urllib3 降级为 {expected_urllib3_version}",
            "pass": urllib3_fixed and no_other_changes,
            "weight": 0.20,
            "detail": f"仅 urllib3 变化: {no_other_changes}, 目标版本: {urllib3_fixed}"
        })
        if urllib3_fixed and no_other_changes:
            fix_score += 0.20

        format_ok = _requirements_format_ok(requirements_path)
        checks.append({
            "id": "requirements_format",
            "label": "requirements.txt 格式检查",
            "pass": format_ok,
            "weight": 0.10,
            "detail": f"requirements 格式有效: {format_ok}"
        })
        if format_ok:
            fix_score += 0.10
    else:
        missing = "requirements.txt" if not requirements_path.exists() else "fixtures/requirements.txt"
        checks.append({
            "id": "requirements_missing",
            "label": "缺少 requirements.txt 文件",
            "pass": False,
            "weight": 0.30,
            "detail": f"缺失: {missing}"
        })

    # ========== 4. 报告与验证脚本质量 (20%) ==========
    report_score = 0.0
    verify_path = w / "verify_import.py"
    rca_path = w / "rca_report.md"
    progress_path = w / "progress.md"

    verify_ok = False
    if verify_path.exists():
        try:
            verify_text = _read_text(verify_path)
            imports_ok = "import urllib3" in verify_text and "import botocore" in verify_text
            success_msg_ok = "Success" in verify_text or "Dependencies loaded successfully" in verify_text
            syntax_ok = _python_syntax_ok(verify_path)
            verify_ok = imports_ok and success_msg_ok and syntax_ok
            checks.append({
                "id": "verify_script_quality",
                "label": "verify_import.py 脚本检查",
                "pass": verify_ok,
                "weight": 0.10,
                "detail": f"imports: {imports_ok}, success 输出: {success_msg_ok}, 语法: {syntax_ok}"
            })
            if verify_ok:
                report_score += 0.10
        except Exception as e:
            checks.append({
                "id": "verify_script_read_error",
                "label": "verify_import.py 读取失败",
                "pass": False,
                "weight": 0.10,
                "detail": str(e)
            })
    else:
        checks.append({
            "id": "verify_script_missing",
            "label": "缺少 verify_import.py",
            "pass": False,
            "weight": 0.10,
            "detail": "文件不存在"
            })

    report_ok = False
    if rca_path.exists():
        rca_text = _read_text(rca_path)
        commit_ok = expected_commit_hash in rca_text
        conflict_ok = conflicting_package in rca_text and "urllib3" in rca_text
        downgrade_ok = expected_urllib3_version in rca_text or "1.26.15" in rca_text
        report_ok = commit_ok and conflict_ok and downgrade_ok
        checks.append({
            "id": "rca_report_quality",
            "label": "rca_report.md 内容有效性",
            "pass": report_ok,
            "weight": 0.05,
            "detail": f"commit: {commit_ok}, 冲突包: {conflict_ok}, 降级版本: {downgrade_ok}"
        })
        if report_ok:
            report_score += 0.05
    else:
        checks.append({
            "id": "rca_report_missing",
            "label": "缺少 rca_report.md",
            "pass": False,
            "weight": 0.05,
            "detail": "文件不存在"
        })

    progress_ok = False
    if progress_path.exists():
        progress_text = _read_text(progress_path)
        progress_ok = "日志" in progress_text and "Git" in progress_text and "requirements" in progress_text
        checks.append({
            "id": "progress_quality",
            "label": "progress.md 记录完整性",
            "pass": progress_ok,
            "weight": 0.05,
            "detail": f"包含关键字: {progress_ok}"
        })
        if progress_ok:
            report_score += 0.05
    else:
        checks.append({
            "id": "progress_missing",
            "label": "缺少 progress.md",
            "pass": False,
            "weight": 0.05,
            "detail": "文件不存在"
        })

    total_score = (
        error_score * weights.get("error_analysis_accuracy", 0.30) +
        git_score * weights.get("git_tracing", 0.20) +
        fix_score * weights.get("minimal_env_fix", 0.30) +
        report_score * weights.get("report_quality", 0.20)
    )

    thresholds = ground_truth.get("scoring", {}).get("thresholds", {
        "excellent": 0.90,
        "good": 0.75,
        "pass": 0.60
    })

    if total_score >= thresholds["excellent"]:
        grade = "excellent"
    elif total_score >= thresholds["good"]:
        grade = "good"
    elif total_score >= thresholds["pass"]:
        grade = "pass"
    else:
        grade = "fail"

    return {
        "grade": grade,
        "score": round(total_score, 3),
        "max_score": 1.0,
        "checks": checks,
        "weights": weights,
        "thresholds": thresholds,
        "metadata": {
            "task_id": "22-log-troubleshooting",
            "expected_commit_hash": expected_commit_hash,
            "expected_urllib3_version": expected_urllib3_version,
        },
    }