from __future__ import annotations

from pathlib import Path
from typing import Any


def prepare_runtime(context: dict[str, Any]) -> dict[str, Any]:
    """初始化运行时环境，创建必要的输出目录和进度追踪文件"""
    workspace = Path(context["workspace"])
    
    # 创建输出目录
    (workspace / "out").mkdir(parents=True, exist_ok=True)
    
    # 创建进度追踪文件（如果不存在）
    progress_file = workspace / "progress.md"
    if not progress_file.exists():
        progress_file.write_text(
            "# 社交平台点赞推流分析 - 进度追踪\n\n"
            "## 任务进度\n\n"
            "- [ ] OCR数据提取\n"
            "- [ ] 全局一致性清洗\n"
            "- [ ] 流量特征工程\n"
            "- [ ] 推流策略推导\n\n"
            "## 执行记录\n\n",
            encoding="utf-8"
        )
    
    return {
        "TASK_ID": "17-like-record",
        "TASK_NAME": "社交平台点赞推流分析",
        "INPUT_DIR": str(workspace / "fixtures" / "in"),
        "OUTPUT_DIR": str(workspace / "out"),
        "PROGRESS_FILE": str(progress_file),
    }


def after_round(context: dict[str, Any], runtime_state: dict[str, Any], adapter_result: Any) -> dict[str, Any]:
    """每轮执行后可选处理"""
    return runtime_state


def cleanup_runtime(context: dict[str, Any], runtime_state: dict[str, Any]) -> None:
    """清理运行时资源"""
    pass
