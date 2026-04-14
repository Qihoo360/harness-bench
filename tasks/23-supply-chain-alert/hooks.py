from __future__ import annotations

from pathlib import Path
from typing import Any


def prepare_runtime(context: dict[str, Any]) -> dict[str, Any]:
    """初始化库存预警分析任务的运行环境"""
    workspace = Path(context["workspace"])

    # 创建输出目录
    (workspace / "out").mkdir(parents=True, exist_ok=True)

    # 创建进度追踪文件
    progress_file = workspace / "progress.md"
    if not progress_file.exists():
        progress_file.write_text(
            "# 供应链库存预警分析 - 进度追踪\n\n"
            "## 任务进度\n\n"
            "- [ ] 读取 consumer_trends.csv 和 warehouse_stock.json\n"
            "- [ ] 关联商品数据并计算可用天数\n"
            "- [ ] 筛选 Search_Volume > 5000 且 Days_Remaining < 7 的商品\n"
            "- [ ] 生成 alert.txt 与 analysis.py\n\n"
            "## 执行记录\n\n",
            encoding="utf-8"
        )

    return {
        "TASK_ID": "23-supply-chain-alert",
        "TASK_NAME": "跨表数据关联与库存预警分析",
        "INPUT_DATA_DIR": str(workspace / "fixtures" / "data"),
        "OUTPUT_DIR": str(workspace / "out"),
        "PROGRESS_FILE": str(progress_file),
        "ALERT_FILE": str(workspace / "alert.txt"),
        "ANALYSIS_SCRIPT": str(workspace / "analysis.py"),
    }


def after_round(context: dict[str, Any], runtime_state: dict[str, Any], adapter_result: Any) -> dict[str, Any]:
    """每轮执行后可选处理"""
    return runtime_state


def cleanup_runtime(context: dict[str, Any], runtime_state: dict[str, Any]) -> None:
    """清理运行时资源"""
    pass
