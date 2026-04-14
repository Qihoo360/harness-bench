"""双图多模态：按 vision_breakdown 与过程三维打分；最终 combined 由评测程序按固定公式合成。"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

_g = Path(__file__).resolve().parent.parent.parent / "grading" / "default_rubric.py"
_spec = importlib.util.spec_from_file_location("_bench_default_rubric", _g)
assert _spec and _spec.loader
_dr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_dr)

RUBRIC_SYSTEM = _dr.RUBRIC_SYSTEM

_TASK_DIR = Path(__file__).resolve().parent
_GT = _TASK_DIR / "ground_truth.json"


def _reference_block() -> str:
    if not _GT.is_file():
        return "（未找到 ground_truth.json；请人工对照任务说明中的两张图。）"
    data = json.loads(_GT.read_text(encoding="utf-8"))
    files = data.get("image_files") or []
    ref = data.get("rubric_reference") or {}
    axes = data.get("vision_score_axes") or {}
    agg = axes.get("aggregation", "")
    i1 = axes.get("image1") or {}
    i2 = axes.get("image2") or {}

    lines = [
        "【参考答案语义（用于打分，不要求模型输出逐字一致）】",
        f"- 第一张（{files[0] if len(files) > 0 else '?'}）：{ref.get('image1', '')}",
        f"- 第二张（{files[1] if len(files) > 1 else '?'}）：{ref.get('image2', '')}",
        "",
        "【视觉分项（每项只能是 0、0.5 或 1，须写入 vision_breakdown；禁止其它小数）】",
        "第一张图 → out/image1_answer.txt 应对应：",
        f"  • shape: {i1.get('shape', '')}",
        f"  • foreground_color: {i1.get('foreground_color', '')}",
        f"  • background: {i1.get('background', '')}",
        "第二张图 → out/image2_answer.txt 应对应：",
        f"  • shape: {i2.get('shape', '')}",
        f"  • foreground_color: {i2.get('foreground_color', '')}",
        f"  • background: {i2.get('background', '')}",
        "",
        f"【汇总规则】{agg}",
        "",
        "【产出要求】模型应写入 out/image1_answer.txt 与 out/image2_answer.txt，各一行。",
    ]
    return "\n".join(lines)


_REF = _reference_block()

USER_TEMPLATE = (
    "Task name: {task_name}\n\n"
    "本题仅按本 rubric 阅卷（字符串 oracle 不参与最终合成）。\n\n"
    + _REF
    + "\n\n"
    "【计分规则（由评测程序执行，你只需输出下列 JSON 字段）】\n"
    "1) vision_breakdown：两图各 shape / foreground_color / background，每项仅 0、0.5、1。\n"
    "2) 评测程序会根据 vision_breakdown **自动**用六项三档计算 scores.reply_appropriateness（视觉结果分），\n"
    "   你填的 scores.reply_appropriateness 若与六项不一致会被覆盖，可填 0 占位。\n"
    "3) scores 另三维（每项 0.0–1.0）：tool_use_appropriate、flow_coherence、error_handling —— 表示工具/流程/错误处理质量。\n"
    "4) **最终榜单分 combined** = reply_appropriateness × mean(tool_use_appropriate, flow_coherence, error_handling)。\n"
    "5) 另有独立 oracle 仅检查答案文件是否存在（oracle_outcome_score），**不参与 combined**。\n"
    "6) **不要**输出 total 字段；不要自行计算「四维平均」。\n\n"
    "Process dimensions（scores 中后三维）：\n"
    "- tool_use_appropriate：mkdir/out；合理写入 image1_answer.txt、image2_answer.txt。\n"
    "- flow_coherence：按两图顺序处理；勿对调文件；啰嗦重复可降分。\n"
    "- error_handling：读图失败或路径问题时是否合理恢复或说明。\n\n"
    "**Vision — reply_appropriateness（由程序从 vision_breakdown 汇总，勿手写复杂推导）**\n"
    "在 vision_breakdown 中为六项**只填 0、0.5 或 1**：\n"
    "  image1.shape, image1.foreground_color, image1.background,\n"
    "  image2.shape, image2.foreground_color, image2.background。\n"
    "若答案文件与图顺序写反或明显幻觉，对应项须为 0。\n\n"
    "Return ONLY JSON (no markdown outside the object):\n"
    "{{\n"
    '  "vision_breakdown": {{\n'
    '    "image1": {{"shape": 1, "foreground_color": 0.5, "background": 0}},\n'
    '    "image2": {{"shape": 0, "foreground_color": 0, "background": 1}}\n'
    "  }},\n"
    '  "scores": {{\n'
    '    "tool_use_appropriate": 0.0,\n'
    '    "flow_coherence": 0.0,\n'
    '    "error_handling": 0.0,\n'
    '    "reply_appropriateness": 0.0\n'
    "  }},\n"
    '  "notes": "one line; may briefly cite weak axes"\n'
    "}}\n\n"
    "--- PROXY TRACE JSON BELOW ---\n"
    "{payload}"
)
