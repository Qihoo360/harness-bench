from __future__ import annotations

from pathlib import Path
from typing import Any


def prepare_runtime(context: dict[str, Any]) -> dict[str, Any]:
    """初始化生产环境长日志排查与依赖修复任务的运行环境"""
    workspace = Path(context["workspace"])

    # 创建输出目录
    (workspace / "out").mkdir(parents=True, exist_ok=True)

    # 创建进度追踪文件
    progress_file = workspace / "progress.md"
    if not progress_file.exists():
        progress_file.write_text(
            "# CI 构建日志排查与依赖修复 - 进度追踪\n\n"
            "## 任务进度\n\n"
            "- [ ] 报错信息提取与依赖冲突分析\n"
            "- [ ] Git 历史溯源并定位责任提交\n"
            "- [ ] requirements.txt 最小化修复\n"
            "- [ ] verify_import.py 验证执行\n"
            "- [ ] RCA 报告撰写\n\n"
            "## 执行记录\n\n",
            encoding="utf-8"
        )

    return {
        "TASK_ID": "22-log-troubleshooting",
        "TASK_NAME": "生产环境长日志排查与依赖修复",
        "INPUT_LOG_DIR": str(workspace / "fixtures" / "logs"),
        "OUTPUT_DIR": str(workspace / "out"),
        "PROGRESS_FILE": str(progress_file),
        "EXPECTED_COMMIT_HASH": "a1b2c3d",
        "EXPECTED_URLLIB3_VERSION": "1.26.16",
        "CONFLICTING_PACKAGE": "botocore",
        "CONFLICT_SNIPPET": "botocore 1.29.76 depends on urllib3<1.27 and >=1.25.4",
    }


def after_round(context: dict[str, Any], runtime_state: dict[str, Any], adapter_result: Any) -> dict[str, Any]:
    """每轮执行后可选处理"""
    return runtime_state


def cleanup_runtime(context: dict[str, Any], runtime_state: dict[str, Any]) -> None:
    """清理运行时资源"""
    pass
