"""23-supply-chain-alert: LLM辅助评分框架"""
from __future__ import annotations

import json
from pathlib import Path

_TASK_DIR = Path(__file__).resolve().parent
_GT = _TASK_DIR / "ground_truth.json"


def _reference_block() -> str:
    if not _GT.is_file():
        return "（未找到 ground_truth.json）"
    lines = [
        "【任务概述】",
        "跨表数据关联与库存预警分析：读取 CSV 与 JSON 数据 → 关联商品库存与销售趋势 → 计算可用天数 → 筛选高危断货商品",
        "",
        "【核心要求】",
        "1. 数据读取：读取 fixtures/data/consumer_trends.csv 和 fixtures/data/warehouse_stock.json",
        "2. 跨表关联：按 Product_ID 合并数据，并计算 Days_Remaining = Current_Stock / Daily_Sales_Velocity",
        "3. 异常检测：筛选 Search_Volume > 5000 且 Days_Remaining < 7 的商品",
        "4. 输出格式：alert.txt 每行仅包含 Product_ID: 可用天数，保留一位小数",
        "",
        "【参考输出】",
        "- P-102: 5.0",
        "- P-104: 5.0",
        "",
        "【产出要求】",
        "- analysis.py: 使用 pandas 或原生 csv/json 读取、关联、计算并生成 alert.txt",
        "- alert.txt: 严格格式为 Product_ID: 可用天数，每行一个商品",
        "- progress.md: 记录读取、关联、过滤和输出过程",
        "",
        "【评分维度】",
        "1. data_join_accuracy: 数据关联与读取是否正确",
        "2. logic_calculation: 逻辑计算和条件过滤是否正确",
        "3. output_formatting: alert.txt 格式和分析脚本是否规范",
    ]
    return "\n".join(lines)


RUBRIC_SYSTEM = """你是一个资深数据分析师，擅长：
- 处理 CSV 和 JSON 跨表数据
- 计算业务指标并进行条件过滤
- 检查输出结果格式是否严格符合要求
- 判断分析脚本是否有数据处理能力"""

USER_TEMPLATE = """## 任务：23-supply-chain-alert 跨表数据关联与库存预警分析

{reference}

---

## Agent执行结果

{payload}

---

## 评分维度（各0.0-1.0）

1. **data_join_accuracy**：数据关联与读取准确性
   - 是否正确读取 consumer_trends.csv 和 warehouse_stock.json
   - 是否基于 Product_ID 关联两表数据
   - 是否计算了 Days_Remaining 并用于后续筛选

2. **logic_calculation**：逻辑计算准确性
   - 是否正确应用 Search_Volume > 5000 条件
   - 是否正确计算 Days_Remaining = Current_Stock / Daily_Sales_Velocity
   - 是否正确筛选出 Days_Remaining < 7 的商品

3. **output_formatting**：输出格式与脚本规范性
   - alert.txt 是否仅包含 Product_ID: 可用天数
   - 可用天数是否保留一位小数
   - analysis.py 是否具备跨表关联与 JSON/CSV 读取逻辑

---

## 返回格式

仅返回以下JSON，无其他说明：
{{
  "data_join_accuracy": 0.0,
  "logic_calculation": 0.0,
  "output_formatting": 0.0
}}"""