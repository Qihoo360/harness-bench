from __future__ import annotations

from pathlib import Path
from typing import Any


def prepare_runtime(context: dict[str, Any]) -> dict[str, Any]:
    """初始化足球射门数据统计任务的运行环境"""
    workspace = Path(context["workspace"])

    # 创建输出目录
    (workspace / "out").mkdir(parents=True, exist_ok=True)

    # 创建进度追踪文件（如果不存在）
    progress_file = workspace / "progress.md"
    if not progress_file.exists():
        progress_file.write_text(
            "# 足球比赛射门分布图数据统计 - 进度追踪\n\n"
            "## 任务进度\n\n"
            "- [ ] 图例语义识别与分类\n"
            "- [ ] 不同类型射门计数\n"
            "- [ ] 射门数据汇总统计\n\n"
            "## 执行记录\n\n",
            encoding="utf-8"
        )

    return {
        "TASK_ID": "20-football-shot-map-analysis",
        "TASK_NAME": "足球比赛射门分布图数据统计",
        "INPUT_IMAGE_DIR": str(workspace / "fixtures" / "in"),
        "OUTPUT_DIR": str(workspace / "out"),
        "PROGRESS_FILE": str(progress_file),
        "EXPECTED_TOTAL_SHOTS": 19,
        "EXPECTED_SHOTS_ON_TARGET": 6,
        "EXPECTED_GOALS": 3,
    }


def after_round(context: dict[str, Any], runtime_state: dict[str, Any], adapter_result: Any) -> dict[str, Any]:
    """每轮执行后可选处理"""
    return runtime_state


def cleanup_runtime(context: dict[str, Any], runtime_state: dict[str, Any]) -> None:
    """清理运行时资源"""
    pass
