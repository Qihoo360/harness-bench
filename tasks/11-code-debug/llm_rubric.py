"""12 渐进式代码修复 - LLM辅助评分"""

from pathlib import Path

RUBRIC_SYSTEM = """你是一个代码调试专家，擅长评估迭代式代码修复的过程质量。

评分原则：
1. 关注调试策略：Agent 如何诊断错误、选择修复方案
2. 关注迭代效率：是否在合理轮次内完成，是否避免无效重试
3. 关注修复质量：修复是否最小化、是否引入新问题
4. 关注过程记录：fix_log 是否清晰记录每层修复思路
5. 不关注最终代码正确性（由 Oracle 验证），只关注调试过程合理性

每项评分 0.0-1.0，1.0=优秀，0.5=及格，0.0=差"""


USER_TEMPLATE = (
    "任务名称：{task_name}\n"
    "任务类型：渐进式代码修复（5层嵌套bug，逐层暴露）\n\n"
    "=== 评分维度 ===\n"
    "请基于以下对话过程，评估 Agent 的调试表现：\n\n"
    "1. error_diagnosis（错误诊断，0.0-1.0）\n"
    "   - 是否准确识别当前暴露的错误类型？\n"
    "   - 是否理解错误原因而非盲目尝试？\n\n"
    "2. fix_strategy（修复策略，0.0-1.0）\n"
    "   - 修复是否最小化（只修当前bug，不提前修深层）？\n"
    "   - 修复方式是否合理（语法错误→补括号，类型错误→转换）？\n\n"
    "3. iteration_efficiency（迭代效率，0.0-1.0）\n"
    "   - 每层修复用了多少轮？是否高效？\n"
    "   - 是否避免无效重试或死循环？\n\n"
    "4. layer_transition（层间过渡，0.0-1.0）\n"
    "   - 发现新bug暴露时的应对方式？\n"
    "   - 是否理解'逐层暴露'机制，而非困惑或放弃？\n\n"
    "5. fix_documentation（修复记录，0.0-1.0）\n"
    "   - fix_log.md 是否清晰记录每层修复？\n"
    "   - 是否包含错误类型、修复方法、原因说明？\n\n"
    "6. code_quality_preservation（代码质量保护，0.0-1.0）\n"
    "   - 修复是否破坏原有代码结构？\n"
    "   - 注释是否规范（# FIX: 说明）？\n\n"
    "=== 对话记录 ===\n"
    "{payload}\n\n"
    "=== 输出要求 ===\n"
    "返回 JSON：\n"
    "{{\n"
    '  "scores": {{\n'
    '    "error_diagnosis": 0.0,\n'
    '    "fix_strategy": 0.0,\n'
    '    "iteration_efficiency": 0.0,\n'
    '    "layer_transition": 0.0,\n'
    '    "fix_documentation": 0.0,\n'
    '    "code_quality_preservation": 0.0\n'
    "  }},\n"
    '  "total": 0.0,  // 六项算术平均，范围 0.0-1.0\n'
    '  "notes": "简要评价 Agent 的调试策略和迭代过程，指出亮点和不足"\n'
    "}}\n"
)