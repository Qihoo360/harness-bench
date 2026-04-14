"""21-us-bank-failures-history: LLM辅助评分框架"""
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
    validation_constraints = data.get("validation_constraints", {})

    lines = [
        "【任务概述】",
        "美国银行倒闭潮历史图表分析：从柱状图中识别三波倒闭高峰 → 推算时间区间 → 检索宏观经济背景 → 生成结构化分析报告",
        "",
        "【核心要求】",
        "1. 图表读取：观察柱状图分布，识别三波高峰的时间区间",
        "2. 时间推算：基于X轴刻度推算各波的具体年份范围",
        "3. 背景检索：针对每波高峰，查证对应的金融危机事件",
        "4. 结构化输出：生成包含时间段和背景描述的JSON数组",
        "",
        "【期望答案结构】",
        "- crises: 数组，包含3个对象",
        "- 每个对象: time_period（年份区间） + background（>=50字的历史背景）",
        "",
        "【关键历史事件】",
        "- 第一波：1930年代末-1940年代初，大萧条余波",
        "- 第二波：1980年代末-1990年代初，储贷危机",
        "- 第三波：2008-2012年左右，次贷危机/全球金融危机",
        "",
        "【评分维度】",
        "1. 视觉图表读取准确性：时间区间推算的准确性",
        "2. 历史背景检索准确性：危机事件识别的正确性",
        "3. 格式合规性：输出格式的规范性和完整性",
        "",
        "【约束条件】",
        "- 零幻觉：时间推算基于图表，背景基于真实历史",
        "- 格式严格：crises数组长度为3，每个对象包含time_period和background",
    ]
    return "\n".join(lines)


RUBRIC_SYSTEM = """你是一个专业的金融历史分析师，擅长：
- 评估图表数据读取的准确性和推算逻辑
- 验证宏观经济历史事件的客观性和正确性
- 检查输出格式的规范性和合规性"""

USER_TEMPLATE = """## 任务：21-us-bank-failures-history 美国历史银行倒闭潮图表分析与背景检索

{reference}

---

## Agent执行结果

{payload}

---

## 评分维度（各0.0-1.0）

1. **vision_chart_reading_accuracy**：视觉图表读取准确性
   - 时间轴推算是否准确识别了三波高峰的年份区间
   - 是否正确理解了图表的X轴刻度和数据分布
   - 时间区间的表述是否合理且基于图表证据

2. **historical_retrieval_accuracy**：历史背景检索准确性
   - 每波危机的历史事件识别是否正确
   - 背景描述是否包含关键的历史事实和经济事件
   - 是否避免了幻觉和主观臆断，基于客观史实

3. **format_compliance_strict**：格式合规性
   - result.json是否仅包含crises数组
   - crises数组长度是否严格为3
   - 每个对象是否包含time_period和background字段
   - background长度是否>=50字

---

## 返回格式

仅返回以下JSON，无其他说明：
{{
  "vision_chart_reading_accuracy": 0.0,
  "historical_retrieval_accuracy": 0.0,
  "format_compliance_strict": 0.0
}}"""
