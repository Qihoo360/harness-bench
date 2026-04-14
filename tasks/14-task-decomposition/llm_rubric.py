"""15-task-decomposition: 任务分解与执行 - 大模型过程评分"""

RUBRIC_SYSTEM = (
    "你是一个项目管理与规划专家，擅长评估任务分解与执行的质量。"
    "评分应基于 Agent 的对话过程和产出文件（subtasks/、progress.md、final_report.md）。"
    "重点关注：分解的合理性、执行的完整性、报告的质量、过程监控的清晰度。"
)

USER_TEMPLATE = """任务名称：{task_name}
任务类型：复杂任务分解与执行（策划并执行虚拟产品发布会）

请基于以下对话过程，评估 Agent 的表现。

=== 评分维度 ===
1. decomposition（分解合理性，0.0-1.0）
   - 子任务是否覆盖关键环节（调研、策划、执行、复盘）？
   - 粒度是否适中（不过粗不过细）？
   - 依赖关系是否清晰？

2. execution（执行完整性，0.0-1.0）
   - 是否完成了所有声明的子任务？
   - 每个子任务的产出是否合理可用？

3. report_quality（报告质量，0.0-1.0）
   - 最终报告（final_report.md）是否方案可行、有创意、细节充分？
   - 是否包含数据或证据支持？

4. progress_tracking（过程监控，0.0-1.0）
   - progress.md 是否清晰记录了进度、遇到的困难和调整？
   - 是否体现了对时间或资源的管理意识？

=== 对话记录 ===
{payload}

=== 输出要求 ===
返回 JSON，格式如下：
{{
  "scores": {{
    "decomposition": 0.0,
    "execution": 0.0,
    "report_quality": 0.0,
    "progress_tracking": 0.0
  }},
  "total": 0.0,   // 四项算术平均，范围 0.0-1.0
  "notes": "简要评价 Agent 的任务分解和执行过程"
}}
"""