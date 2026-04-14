from __future__ import annotations

from pathlib import Path
from typing import Any


def prepare_runtime(context: dict[str, Any]) -> dict[str, Any]:
    """初始化地标建筑识别任务的运行环境"""
    workspace = Path(context["workspace"])

    # 创建输出目录
    (workspace / "out").mkdir(parents=True, exist_ok=True)

    # 创建进度追踪文件（如果不存在）
    progress_file = workspace / "progress.md"
    if not progress_file.exists():
        progress_file.write_text(
            "# 著名地标建筑识别与介绍 - 进度追踪\n\n"
            "## 任务进度\n\n"
            "- [ ] 图像特征提取与地标识别\n"
            "- [ ] 建筑背景知识检索\n"
            "- [ ] 结构化介绍文本生成\n\n"
            "## 执行记录\n\n",
            encoding="utf-8"
        )

    return {
        "TASK_ID": "19-landmark-recognition",
        "TASK_NAME": "著名地标建筑识别与介绍",
        "INPUT_IMAGE_DIR": str(workspace / "fixtures" / "in"),
        "OUTPUT_DIR": str(workspace / "out"),
        "PROGRESS_FILE": str(progress_file),
        "EXPECTED_BUILDING_NAME_EN": "Royal Albert Hall",
    }


def after_round(context: dict[str, Any], runtime_state: dict[str, Any], adapter_result: Any) -> dict[str, Any]:
    """每轮执行后可选处理"""
    return runtime_state


def cleanup_runtime(context: dict[str, Any], runtime_state: dict[str, Any]) -> None:
    """清理运行时资源"""
    pass
