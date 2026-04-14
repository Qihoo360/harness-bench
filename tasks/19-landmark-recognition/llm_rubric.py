"""19-landmark-recognition: LLM辅助评分框架"""
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
        "著名地标建筑识别与介绍：从图像特征分析 → 建筑识别 → 背景知识检索 → 结构化介绍生成",
        "",
        "【核心要求】",
        "1. 视觉识别：分析建筑的穹顶结构、立面细节及周边环境特征",
        "2. 实体识别：准确识别建筑的具体名称（英文全称）",
        "3. 知识检索：通过外部知识库查证地理位置、历史背景及核心用途",
        "4. 内容生成：整合信息为通顺的中文介绍（不少于50字）",
        "",
        "【期望答案】",
        f"- building_name_en: {exact_match.get('building_name_en', 'Royal Albert Hall')} (纯英文全称)",
        "- introduction: 中文简要介绍，包含位置、历史、用途等关键信息",
        "",
        "【产出要求】",
        "- result.json: 仅包含 building_name_en 和 introduction 两个字段的JSON",
        "- progress.md: 记录图像分析、知识检索、文本生成过程",
        "",
        "【评分维度】",
        "1. 视觉识别准确性：建筑特征分析的准确性和完整性",
        "2. 知识检索准确性：背景信息的客观性和正确性",
        "3. 执行逻辑合理性：推理过程的合理性和连贯性",
        "4. 格式合规性：输出格式的规范性和完整性",
        "5. 过程追踪清晰度：progress.md记录的详细程度",
        "",
        "【约束条件】",
        "- 零幻觉：建筑名称和介绍必须基于客观事实",
        "- 格式严格：building_name_en纯英文，introduction纯中文且>=50字",
        "- 推理可信：逻辑链条清晰可验证",
    ]
    return "\n".join(lines)


RUBRIC_SYSTEM = """你是一个地理信息专家和建筑识别专家，擅长：
- 评估图像建筑特征识别的准确性和完整性
- 验证地理历史知识检索的客观性和正确性
- 检查推理逻辑的合理性和连贯性
- 审查输出格式的规范性和合规性"""

USER_TEMPLATE = """## 任务：19-landmark-recognition 著名地标建筑识别与介绍

{reference}

---

## Agent执行结果

{payload}

---

## 评分维度（各0.0-1.0）

1. **vision_recognition_accuracy**：视觉识别准确性
   - 建筑特征分析是否准确识别了关键视觉元素
   - 是否正确识别了建筑的独特标识（如穹顶、立面等）
   - 图像分析质量如何（包括建筑风格、周边环境）

2. **knowledge_retrieval_accuracy**：知识检索准确性
   - 建筑背景信息是否基于客观事实检索
   - 是否使用了可靠的外部知识源
   - 历史、地理、用途信息的准确性和可验证性

3. **execution_logic_reasonable**：执行逻辑合理性
   - 从图像分析到建筑识别的逻辑链条是否合理
   - 推理过程是否连贯且可信
   - 是否避免了幻觉和主观臆断

4. **format_compliance_strict**：格式合规性
   - result.json是否仅包含building_name_en和introduction字段
   - building_name_en是否为纯英文全称
   - introduction是否为中文且长度>=50字

5. **progress_tracking_clear**：过程追踪清晰度
   - progress.md是否记录了各阶段执行过程
   - 是否包含图像分析结果、检索依据、生成过程
   - 记录是否详细且有助于理解执行逻辑

---

## 返回格式

仅返回以下JSON，无其他说明：
{{
  "vision_recognition_accuracy": 0.0,
  "knowledge_retrieval_accuracy": 0.0,
  "execution_logic_reasonable": 0.0,
  "format_compliance_strict": 0.0,
  "progress_tracking_clear": 0.0
}}"""
