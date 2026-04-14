RUBRIC_SYSTEM = "你是一个 OpenClaw / LLM API 网关评测专家，擅长审计多供应商路由、缓存、工具调用和故障切换策略。"

USER_TEMPLATE = """任务：18-provider-failover-audit

请评估 Agent 的输出是否：
1. 正确区分 Anthropic、OpenAI、Gemini 在缓存、结构化输出、工具调用、多模态和延迟方面的差异。
2. 能把 traces、配置、工作负载需求关联成可执行的 provider routing 和 fallback 策略。
3. 能避免错误的跨厂商统一缓存阈值，并提出 OpenClaw cache trace / usage 诊断方案。
4. 产物格式完整：provider_scorecard.json、openclaw_config_patch.json、failover_playbook.md、audit_notes.md。

返回 JSON：
{
  "scores": {
    "provider_reasoning": 0.0,
    "routing_quality": 0.0,
    "diagnostic_playbook": 0.0,
    "artifact_completeness": 0.0
  },
  "total": 0.0,
  "notes": "简要评价"
}

Agent 输出：
---
{payload}
---
"""
