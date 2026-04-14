"""18-album-metadata-retrieval: LLM辅助评分框架"""
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
        "专辑封面元数据提取与知识检索：从图片OCR识别 → 歌手推理 → 年份检索",
        "",
        "【核心要求】",
        "1. 视觉识别：OCR提取图片中的文字信息",
        "2. 实体推理：基于文字线索推断歌手中文全名",
        "3. 知识检索：通过外部搜索确认专辑发行年份",
        "",
        "【期望答案】",
        f"- singer: {exact_match.get('singer', '梁静茹')} (纯中文汉字)",
        f"- year: {exact_match.get('year', 2009)} (4位整数)",
        "",
        "【产出要求】",
        "- result.json: 仅包含 singer 和 year 两个字段的JSON",
        "- progress.md: 记录OCR结果、推理逻辑、检索依据",
        "",
        "【评分维度】",
        "1. 视觉识别准确性：OCR提取的完整性和准确性",
        "2. 知识检索准确性：年份信息的客观性和正确性",
        "3. 执行逻辑合理性：推理过程的合理性和连贯性",
        "4. 格式合规性：输出格式的规范性和完整性",
        "5. 过程追踪清晰度：progress.md记录的详细程度",
        "",
        "【约束条件】",
        "- 零幻觉：所有信息必须基于图片或客观事实",
        "- 格式严格：singer纯汉字，year纯数字",
        "- 推理可信：逻辑链条清晰可验证",
    ]
    return "\n".join(lines)


RUBRIC_SYSTEM = """你是一个音乐元数据专家和OCR识别专家，擅长：
- 评估图像文字识别的准确性和完整性
- 验证音乐知识检索的客观性和正确性
- 检查推理逻辑的合理性和连贯性
- 审查输出格式的规范性和合规性"""

USER_TEMPLATE = """## 任务：18-album-metadata-retrieval 专辑封面元数据提取与知识检索

{reference}

---

## Agent执行结果

{payload}

---

## 评分维度（各0.0-1.0）

1. **vision_recognition_accuracy**：视觉识别准确性
   - OCR提取的文字信息是否完整准确
   - 是否正确识别了专辑名称和相关标识
   - 文字识别质量如何（包括中文、繁体、英文）

2. **knowledge_retrieval_accuracy**：知识检索准确性
   - 年份信息是否基于客观事实检索
   - 是否使用了可靠的外部知识源
   - 年份信息的准确性和可验证性

3. **execution_logic_reasonable**：执行逻辑合理性
   - 从OCR到歌手推理的逻辑链条是否合理
   - 推理过程是否连贯且可信
   - 是否避免了幻觉和主观臆断

4. **format_compliance_strict**：格式合规性
   - result.json是否仅包含singer和year字段
   - singer是否为纯中文汉字（无拼音或英文）
   - year是否为4位整数格式

5. **progress_tracking_clear**：过程追踪清晰度
   - progress.md是否记录了各阶段执行过程
   - 是否包含OCR结果、推理依据、检索过程
   - 记录是否详细且有助于理解执行逻辑

---

## 返回格式

仅返回以下JSON，无其他说明：

```json
{
  "scores": {
    "vision_recognition_accuracy": 0.0,
    "knowledge_retrieval_accuracy": 0.0,
    "execution_logic_reasonable": 0.0,
    "format_compliance_strict": 0.0,
    "progress_tracking_clear": 0.0
  },
  "total": 0.0,
  "notes": "简要评价（一句话）"
}
```

total = 五个维度的算术平均值。
""".format(reference=_reference_block(), payload="{payload}")
