#!/usr/bin/env python3
"""Demo：写入符合 ground_truth 的 meeting_summary.txt。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_TASK = Path(__file__).resolve().parent.parent

_SUMMARY = """本次会议围绕 OpenClaw 产品线 2026 Q2 规划对齐。产品确认 Q2 里程碑以 4 月底 P0 清零与 5 月中 M1 演示为节点，并说明预算科目已锁定，追加须走变更。研发侧提示网关与 embedded 回归风险，人力有限时可能裁减多模态插件子项。财务强调本季度研发预算不得挪作市场费用，差旅冻结。运营将提供脱敏样例任务。未决事项为招聘冻结是否延续，若延续则产品需调整 M1 范围。结论：在预算内按里程碑推进，并同步路线图与风险清单。"""


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: demo_apply.py <WORKSPACE>", file=sys.stderr)
        return 2
    w = Path(sys.argv[1]).resolve()
    gt = json.loads((_TASK / "ground_truth.json").read_text(encoding="utf-8"))
    rel = gt.get("summary_path", "out/meeting_summary.txt")
    out = w / rel
    out.parent.mkdir(parents=True, exist_ok=True)
    text = _SUMMARY.strip()
    out.write_text(text + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
