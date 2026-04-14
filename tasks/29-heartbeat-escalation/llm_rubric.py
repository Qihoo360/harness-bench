RUBRIC_SYSTEM = "你是自治 agent 和 heartbeat 任务评估专家，重视异步事件处理、去重、升级策略和资源控制。"

USER_TEMPLATE = """任务：20-heartbeat-escalation

请评估 Agent 是否：
1. 使用 heartbeat/持续检查处理异步注入邮件，而不是只扫描一次。
2. 正确过滤 high/normal/low，且不会重复通知同一邮件。
3. 根据同一 thread 的多封 high 邮件触发升级。
4. 通过 sleep/轮询间隔避免忙等。
5. 产物完整：HEARTBEAT.md、notifications.txt、heartbeat_state.json、escalation_summary.md。

返回 JSON：
{
  "scores": {
    "heartbeat_design": 0.0,
    "async_handling": 0.0,
    "dedupe_and_escalation": 0.0,
    "resource_control": 0.0,
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
