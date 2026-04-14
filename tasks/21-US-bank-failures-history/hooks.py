from __future__ import annotations

from pathlib import Path
from typing import Any


def prepare_runtime(context: dict[str, Any]) -> dict[str, Any]:
    """初始化美国银行倒闭潮历史图表分析任务的运行环境"""
    workspace = Path(context["workspace"])

    # 创建输出目录
    (workspace / "out").mkdir(parents=True, exist_ok=True)

    # 创建进度追踪文件（如果不存在）
    progress_file = workspace / "progress.md"
    if not progress_file.exists():
        progress_file.write_text(
            "# 美国银行倒闭潮历史图表分析与背景检索 - 进度追踪\n\n"
            "## 任务进度\n\n"
            "- [ ] 图表时间轴与峰值定位\n"
            "- [ ] 宏观经济历史背景检索\n"
            "- [ ] 结构化分析报告生成\n\n"
            "## 执行记录\n\n",
            encoding="utf-8"
        )

    return {
        "TASK_ID": "21-us-bank-failures-history",
        "TASK_NAME": "美国历史银行倒闭潮图表分析与背景检索",
        "INPUT_IMAGE_DIR": str(workspace / "fixtures" / "in"),
        "OUTPUT_DIR": str(workspace / "out"),
        "PROGRESS_FILE": str(progress_file),
        "EXPECTED_CRISIS_COUNT": 3,
    }


def after_round(context: dict[str, Any], runtime_state: dict[str, Any], adapter_result: Any) -> dict[str, Any]:
    """每轮执行后可选处理"""
    return runtime_state


def cleanup_runtime(context: dict[str, Any], runtime_state: dict[str, Any]) -> None:
    """清理运行时资源"""
    pass
