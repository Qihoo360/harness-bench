"""20-football-shot-map-analysis: LLM辅助评分框架"""
from __future__ import annotations

import json
from pathlib import Path

_TASK_DIR = Path(__file__).resolve().parent
_GT = _TASK_DIR / "ground_truth.json"


def _reference_block() -> str:
    """生成评分参考"""
    if not _GT.is_file():
        return "（未找到 ground_truth.json）"

    data = json.loads(_GT.read_text(encoding="utf-8"))
    exact_match = data.get("validation_constraints", {}).get("exact_match_target", {})

    lines = [
        "【任务概述】",
        "足球比赛射门分布图数据统计：从可视化射门图中识别图例 → 分类计数不同类型射门 → 汇总统计数据",
        "",
        "【核心要求】",
        "1. 图例识别：理解不同标记点（空心圆、带点圆、足球图标等）代表的射门类型",
        "2. 精确计数：扫描整张图片，准确统计各类图标数量",
        "3. 数据汇总：计算总射门数、射正数、进球数",
        "",
        "【期望答案】",
        f"- total_shots: {exact_match.get('total_shots', 19)} (总射门数)",
        f"- shots_on_target: {exact_match.get('shots_on_target', 6)} (射正球门次数)",
        f"- goals: {exact_match.get('goals', 3)} (进球数)",
        "",
        "【产出要求】",
        "- result.json: 仅包含 total_shots、shots_on_target、goals 三个整数字段",
        "- progress.md: 记录图例识别过程、各类图标计数明细",
        "",
        "【评分维度】",
        "1. 视觉计数准确性：图标识别和计数的准确性",
        "2. 领域推理合理性：足球统计逻辑的合理性",
        "3. 格式合规性：输出格式的规范性和完整性",
        "",
        "【约束条件】",
        "- 零幻觉：数据必须基于图片标记点",
        "- 逻辑一致：total_shots >= shots_on_target >= goals",
        "- 格式严格：所有值都是纯整数",
    ]
    return "\n".join(lines)


RUBRIC_SYSTEM = """你是一个专业的体育数据分析师，擅长：
- 评估图像中标记点识别的准确性和完整性
- 验证足球统计数据的客观性和逻辑性
- 检查输出格式的规范性和合规性"""

USER_TEMPLATE = """## 任务：20-football-shot-map-analysis 足球比赛射门分布图数据统计

{reference}

---

## Agent执行结果

{payload}

---

## 评分维度（各0.0-1.0）

1. **vision_counting_accuracy**：视觉计数准确性
   - 图例识别是否准确理解不同标记点的含义
   - 各类图标的计数是否精确无遗漏
   - 是否正确区分了射门类型（总射门、射正、进球）

2. **domain_reasoning_reasonable**：领域推理合理性
   - 数据是否符合足球统计的基本逻辑
   - 射门数据的数学关系是否合理（总射门 >= 射正 >= 进球）
   - 推理过程是否基于客观事实而非主观臆断

3. **format_compliance_strict**：格式合规性
   - result.json是否仅包含total_shots、shots_on_target、goals三个字段
   - 所有值是否都是纯整数格式（无字符串、单位等）
   - JSON格式是否规范且可解析

---

## 返回格式

仅返回以下JSON，无其他说明：
{{
  "vision_counting_accuracy": 0.0,
  "domain_reasoning_reasonable": 0.0,
  "format_compliance_strict": 0.0
}}"""
