"""17-like-record: LLM辅助评分框架"""
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
    
    lines = [
        "【任务概述】",
        "社交平台点赞推流分析：从截图中OCR提取点赞数据 → 全局去重 → 流量特征分析 → 推流策略建议",
        "",
        "【核心要求】",
        "1. OCR数据提取：user_id, action_time, note_id, author_id, category",
        "2. 全局去重：基于 (user_id, note_id) 联合主键",
        "3. 特征分析：Top作者、优势分区、高峰时段",
        "4. 策略推荐：不少于100字的针对性推流建议",
        "",
        "【产出要求】",
        "- cleaned_data.csv: RFC 4180标准CSV，去重数据集，65行有效记录",
        "- analysis_report.md: 清洗统计、作者画像、爆款成因（>=200字），明确指出110292为第一名",
        "- strategy_recommendation.txt: 推流加权建议（>=100字）",
        "- progress.md: 子任务执行进度追踪，记录OCR->Cleaning->Strategy的时间戳",
        "",
        "【数据约束】",
        "- 禁止幻觉：结论必须基于实际数据",
        "- 全局去重：跨所有输入图片的去重",
        "- 数字ID：user_id/author_id/note_id必须为纯数字",
        "- 时间格式：action_time须为有效时间戳或日期",
    ]
    return "\n".join(lines)


RUBRIC_SYSTEM = """你是一个数据分析与OCR处理专家，擅长评估：
- OCR提取的数据准确性和完整性
- 数据清洗逻辑的合理性和全面性
- 统计分析的严谨性和结论的可靠性
- 业务推荐的针对性和可行性"""

USER_TEMPLATE = """## 任务：17-like-record 社交平台点赞推流分析

{reference}

---

## Agent执行结果

{payload}

---

## 评分维度（各0.0-1.0）

1. **data_extraction_accuracy**：OCR提取的数据准确率、字段完整性、噪声修正效果
   - 数字ID是否纯数字、时间格式是否有效、字段是否齐全

2. **deduplication_quality**：全局去重的合理性和完整性
   - 是否基于 (user_id, note_id) 联合主键
   - 是否覆盖全量输入数据

3. **analysis_rigor**：流量特征分析的严谨性
   - Top作者识别是否正确、分析是否基于数据支撑
   - 分区优势和高峰时段分析是否合理

4. **recommendation_quality**：推流策略建议的针对性和可行性
   - 建议是否基于分析结论、是否具体可执行
   - 字数是否满足要求（>=100字）

5. **process_tracking**：进度追踪的清晰性
   - progress.md是否记录了各子任务状态

---

## 返回格式

仅返回以下JSON，无其他说明：

```json
{
  "scores": {
    "data_extraction_accuracy": 0.0,
    "deduplication_quality": 0.0,
    "analysis_rigor": 0.0,
    "recommendation_quality": 0.0,
    "process_tracking": 0.0
  },
  "total": 0.0,
  "notes": "简要评价（一句话）"
}
```

total = 五个维度的算术平均值。
""".format(reference=_reference_block(), payload="{payload}")
