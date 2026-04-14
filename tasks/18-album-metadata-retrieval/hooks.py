from __future__ import annotations

from pathlib import Path
from typing import Any


def prepare_runtime(context: dict[str, Any]) -> dict[str, Any]:
    """初始化专辑元数据检索任务的运行环境"""
    workspace = Path(context["workspace"])

    # 创建输出目录
    (workspace / "out").mkdir(parents=True, exist_ok=True)

    # 创建进度追踪文件（如果不存在）
    progress_file = workspace / "progress.md"
    if not progress_file.exists():
        progress_file.write_text(
            "# 专辑封面元数据提取与检索 - 进度追踪\n\n"
            "## 任务进度\n\n"
            "- [ ] 图像文本提取与OCR识别\n"
            "- [ ] 实体推断与中文名映射\n"
            "- [ ] 专辑发行年份外部检索\n\n"
            "## 执行记录\n\n",
            encoding="utf-8"
        )

    return {
        "TASK_ID": "18-album-metadata-retrieval",
        "TASK_NAME": "专辑封面元数据提取与知识检索",
        "INPUT_IMAGE_DIR": str(workspace / "fixtures" / "in"),
        "OUTPUT_DIR": str(workspace / "out"),
        "PROGRESS_FILE": str(progress_file),
        "EXPECTED_SINGER": "梁静茹",
        "EXPECTED_YEAR": 2009,
    }


def after_round(context: dict[str, Any], runtime_state: dict[str, Any], adapter_result: Any) -> dict[str, Any]:
    """每轮执行后可选处理"""
    return runtime_state


def cleanup_runtime(context: dict[str, Any], runtime_state: dict[str, Any]) -> None:
    """清理运行时资源"""
    pass
