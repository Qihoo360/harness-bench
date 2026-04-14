"""22-log-troubleshooting: LLM辅助评分框架"""
from __future__ import annotations

import json
from pathlib import Path

_TASK_DIR = Path(__file__).resolve().parent
_GT = _TASK_DIR / "ground_truth.json"


def _reference_block() -> str:
    if not _GT.is_file():
        return "（未找到 ground_truth.json）"
    data = json.loads(_GT.read_text(encoding="utf-8"))
    constraints = data.get("validation_constraints", {})

    lines = [
        "【任务概述】",
        "生产环境构建日志排查：从长日志中定位依赖冲突 → Git 溯源责任提交 → 最小化修复 requirements.txt → 验证修复效果",
        "",
        "【核心要求】",
        "1. 报错分析：识别日志中的依赖冲突，尤其是 urllib3 与 botocore 的版本不兼容",
        "2. Git 溯源：定位引入冲突依赖的 Commit Hash",
        "3. 最小修改：仅修复 urllib3 版本，不改变其他依赖",
        "4. 验证执行：编写 verify_import.py 并确认导入 botocore 与 urllib3 成功",
        "",
        "【期望答案】",
        f"- expected_commit_hash: {constraints.get('expected_commit_hash', 'a1b2c3d')}",
        f"- expected_urllib3_version: {constraints.get('expected_urllib3_version', '1.26.16')}",
        f"- conflicting_package: {constraints.get('conflicting_package', 'botocore')}",
        "",
        "【产出要求】",
        "- requirements.txt: 仅将 urllib3 从 2.0.2 降级到 1.26.16",
        "- verify_import.py: 包含 import urllib3 与 import botocore，且能成功执行",
        "- rca_report.md: 包含报错摘要、责任 Commit、修复策略",
        "- progress.md: 记录日志分析和 Git 溯源过程",
        "",
        "【评分维度】",
        "1. error_analysis_accuracy: 日志报错提取是否精准",
        "2. git_tracing: 责任 Commit 定位是否准确",
        "3. minimal_env_fix: requirements 修复是否最小且正确",
        "4. report_quality: RCA 报告和验证脚本是否完整",
    ]

    return "\n".join(lines)


RUBRIC_SYSTEM = """你是一个资深运维与问题排查专家，擅长：
- 从长日志中定位关键错误信息
- 利用 Git 历史定位问题根源
- 执行最小化依赖修复并验证环境可用性
- 输出清晰的 RCA 报告"""

USER_TEMPLATE = """## 任务：22-log-troubleshooting 生产环境长日志排查与依赖修复

{reference}

---

## Agent执行结果

{payload}

---

## 评分维度（各0.0-1.0）

1. **error_analysis_accuracy**：报错分析准确性
   - 是否从长日志中提取了关键依赖冲突信息
   - 是否明确识别了 urllib3 与 botocore 的版本不兼容
   - 是否避免直接 cat 全量日志，采用精准过滤方式

2. **git_tracing**：Git 溯源准确性
   - 是否定位到了引入错误依赖的 Commit Hash
   - 是否证明该 Commit Hash 在仓库历史中真实存在
   - 是否基于日志中的关键词做了合理溯源

3. **minimal_env_fix**：最小环境修复
   - 是否仅将 urllib3 从 2.0.2 修改为 1.26.16
   - 是否保留了其他依赖版本不变
   - 是否提供合法的 requirements.txt 格式

4. **report_quality**：报告与验证脚本质量
   - rca_report.md 是否同时包含报错摘要、责任 Commit、修复策略
   - verify_import.py 是否包含 import urllib3 和 import botocore
   - progress.md 是否记录日志分析、Git 溯源和修复验证过程

---

## 返回格式

仅返回以下JSON，无其他说明：
{{
  "error_analysis_accuracy": 0.0,
  "git_tracing": 0.0,
  "minimal_env_fix": 0.0,
  "report_quality": 0.0
}}"""