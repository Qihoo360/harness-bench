"""过程分：proxy trace → 任务 llm_rubric → LLM；与 oracle 结果分按乘积合成。"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path
from typing import Any

from clawbench_v2.config import resolve_project_root
from clawbench_v2.extract_proxy_trace import extract_proxy_trace
from clawbench_v2.grading.rubric_llm import run_llm_rubric
from clawbench_v2.models import TaskSpec

_RUBRIC_SCORE_KEYS = (
    "tool_use_appropriate",
    "flow_coherence",
    "error_handling",
    "reply_appropriateness",
)

# 视觉/图片产出类：最终 combined 以 LLM rubric 为准（reply_appropriateness=结果分，其余三维均值=过程分），oracle 结果分仅作审计字段 oracle_outcome_score。
_RUBRIC_PRIMARY_OUTCOME_TASK_IDS = frozenset({"08-image-recognize", "14-image-edit"})


def _load_default_rubric_strings(project_root: Path) -> tuple[str, str]:
    p = project_root / "grading" / "default_rubric.py"
    spec = importlib.util.spec_from_file_location("clawbenchv2_default_rubric", p)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load default rubric: {p}")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    s = getattr(m, "RUBRIC_SYSTEM", None)
    t = getattr(m, "USER_TEMPLATE", None)
    if not isinstance(s, str) or not isinstance(t, str):
        raise RuntimeError("grading/default_rubric.py must define RUBRIC_SYSTEM and USER_TEMPLATE")
    return s, t


def load_rubric_prompts(task: TaskSpec, payload: str, project_root: Path) -> tuple[str, str, str]:
    default_s, default_t = _load_default_rubric_strings(project_root)
    task_name = task.task_id
    td = task.task_dir
    if td is None:
        u = default_t.format(task_name=task_name, payload=payload)
        return default_s, u, "grading/default_rubric.py (no task_dir)"

    mod_path = td / "llm_rubric.py"
    if not mod_path.is_file():
        u = default_t.format(task_name=task_name, payload=payload)
        return default_s, u, "grading/default_rubric.py (missing llm_rubric.py)"

    mod_name = f"task_llm_rubric_{td.name.replace('-', '_')}"
    spec = importlib.util.spec_from_file_location(mod_name, mod_path)
    if spec is None or spec.loader is None:
        u = default_t.format(task_name=task_name, payload=payload)
        return default_s, u, "grading/default_rubric.py (spec error)"
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    try:
        spec.loader.exec_module(mod)
    except Exception:
        u = default_t.format(task_name=task_name, payload=payload)
        return default_s, u, "grading/default_rubric.py (llm_rubric import error)"

    system = getattr(mod, "RUBRIC_SYSTEM", None)
    tpl = getattr(mod, "USER_TEMPLATE", None)
    if not isinstance(system, str):
        system = default_s
    if not isinstance(tpl, str):
        tpl = default_t
    try:
        user = tpl.format(task_name=task_name, payload=payload)
    except KeyError as e:
        u = default_t.format(task_name=task_name, payload=payload)
        return default_s, u, f"grading/default_rubric.py (USER_TEMPLATE KeyError: {e})"

    return system, user, f"tasks/{td.name}/llm_rubric.py"


def _recompute_rubric_total_from_scores(rubric: dict[str, Any]) -> None:
    if rubric.get("skipped") or rubric.get("parse_error"):
        return
    s = rubric.get("scores")
    if not isinstance(s, dict):
        return
    vals: list[float] = []
    for k in _RUBRIC_SCORE_KEYS:
        v = s.get(k)
        if isinstance(v, (int, float)):
            vals.append(float(v))
        else:
            return
    if len(vals) != 4:
        return
    raw = rubric.get("total")
    if isinstance(raw, (int, float)):
        rubric["total_llm"] = float(raw)
    rubric["total"] = round(sum(vals) / 4.0, 4)
    rubric["total_source"] = "mean_of_four_scores"


def _quantize_trinary(x: float) -> float:
    t = max(0.0, min(1.0, float(x)))
    return round(t * 2.0) / 2.0


def _sync_08_reply_from_vision_breakdown(rubric: dict[str, Any]) -> None:
    if rubric.get("skipped") or rubric.get("parse_error"):
        return
    s = rubric.get("scores")
    if not isinstance(s, dict):
        return
    vb = rubric.get("vision_breakdown")
    if not isinstance(vb, dict):
        return
    vals: list[float] = []
    for img in ("image1", "image2"):
        d = vb.get(img)
        if not isinstance(d, dict):
            return
        for k in ("shape", "foreground_color", "background"):
            v = d.get(k)
            if not isinstance(v, (int, float)):
                return
            q = _quantize_trinary(float(v))
            d[k] = q
            vals.append(q)
    if len(vals) != 6:
        return
    mean_v = round(sum(vals) / 6.0, 4)
    s["reply_appropriateness"] = mean_v
    rubric["reply_appropriateness_source"] = "mean_of_six_vision_axes_trinary"


def compute_scoring(
    task: TaskSpec,
    sandbox: Path,
    oracle_result: dict[str, Any],
    *,
    max_payload_chars: int = 24000,
    process_default_if_no_llm: float = 1.0,
    openclaw_config: Path | None = None,
) -> dict[str, Any]:
    """
    抽取 ``usage-proxy`` 最后一轮 JSON → LLM 过程分；``combined_score = outcome_score * process_effective``。
    环境 ``CLAWBENCHV2_SKIP_PROCESS_GRADE=1`` 时跳过 LLM，过程分取 ``process_default_if_no_llm``。
    """
    skip_flag = os.environ.get("CLAWBENCHV2_SKIP_PROCESS_GRADE", "").strip().lower() in ("1", "true", "yes")
    project_root = resolve_project_root()
    cfg_oc = openclaw_config
    if cfg_oc is None:
        raw = os.environ.get("CLAWBENCHV2_OPENCLAW_CONFIG", "").strip()
        if raw:
            cfg_oc = Path(raw).expanduser()

    proxy_dir = sandbox / "usage-proxy"
    trace = extract_proxy_trace(proxy_dir, all_rounds=False)
    trace_error = trace.get("error")

    outcome_raw = oracle_result.get("outcome_score")
    outcome_score: float | None = float(outcome_raw) if isinstance(outcome_raw, (int, float)) else None

    base: dict[str, Any] = {
        "blend": "multiply",
        "extract_mode": trace.get("extract_mode"),
        "source_response_file": trace.get("source_response_file"),
        "proxy_trace_error": trace_error,
        "rubric_prompt_source": None,
        "rubric": None,
        "process_score": None,
        "process_effective": None,
        "outcome_score": outcome_score,
        "oracle_outcome_score": outcome_score,
        "combined_score": None,
        "notes": "",
    }

    if skip_flag:
        base["rubric"] = {"skipped": True, "reason": "CLAWBENCHV2_SKIP_PROCESS_GRADE"}
        pe = float(process_default_if_no_llm)
        base["process_effective"] = round(pe, 4)
        base["notes"] = "process grade skipped by env; process_effective = default"
        if outcome_score is not None:
            base["combined_score"] = round(outcome_score * pe, 4)
        else:
            base["combined_score"] = round(pe, 4)
        return base

    if trace_error:
        base["rubric"] = {"skipped": True, "reason": f"no proxy trace: {trace_error}"}
        pe = float(process_default_if_no_llm)
        base["process_effective"] = round(pe, 4)
        base["notes"] = "no usage-proxy; process_effective = default"
        if outcome_score is not None:
            base["combined_score"] = round(outcome_score * pe, 4)
        else:
            base["combined_score"] = round(pe, 4)
        return base

    payload = json.dumps(trace, ensure_ascii=False)
    if len(payload) > max_payload_chars:
        payload = payload[:max_payload_chars] + "\n...[truncated]"

    system, user, src = load_rubric_prompts(task, payload, project_root)
    base["rubric_prompt_source"] = src

    rubric = run_llm_rubric(system=system, user=user, openclaw_config=cfg_oc)
    base["rubric"] = rubric

    if task.task_id == "08-image-recognize":
        _sync_08_reply_from_vision_breakdown(rubric)
    _recompute_rubric_total_from_scores(rubric)

    process_score: float | None = None
    if not rubric.get("skipped") and not rubric.get("parse_error"):
        t = rubric.get("total")
        if isinstance(t, (int, float)):
            process_score = float(t)

    pe = process_score if process_score is not None else float(process_default_if_no_llm)
    base["process_score"] = process_score
    base["process_effective"] = round(pe, 4)

    rubric_split: dict[str, Any] | None = None
    if (
        task.task_id in _RUBRIC_PRIMARY_OUTCOME_TASK_IDS
        and not rubric.get("skipped")
        and not rubric.get("parse_error")
    ):
        s = rubric.get("scores")
        if isinstance(s, dict):

            def _sf(key: str) -> float | None:
                v = s.get(key)
                return float(v) if isinstance(v, (int, float)) else None

            ra = _sf("reply_appropriateness")
            t1 = _sf("tool_use_appropriate")
            fc = _sf("flow_coherence")
            eh = _sf("error_handling")
            if ra is not None and all(x is not None for x in (t1, fc, eh)):
                # oracle_outcome_score 已在 base 中保留；此处 outcome_score 改为 rubric 结果分
                outcome_score = ra
                base["outcome_score"] = ra
                process_score = (t1 + fc + eh) / 3.0
                base["process_score"] = process_score
                base["process_effective"] = round(float(process_score), 4)
                pe = float(process_score)
                rubric_split = {
                    "task": task.task_id,
                    "outcome_from": "reply_appropriateness",
                    "process_from": "mean(tool_use_appropriate, flow_coherence, error_handling)",
                }

    if rubric_split is not None:
        base["rubric_split"] = rubric_split
        r0 = base.get("rubric")
        if isinstance(r0, dict):
            pev = base.get("process_effective")
            if isinstance(pev, (int, float)):
                r0["total"] = round(float(pev), 4)
                r0["total_source"] = "rubric_primary_mean_three_process_dims"
            r0.pop("total_llm", None)

    if outcome_score is not None:
        base["combined_score"] = round(outcome_score * pe, 4)
        if rubric_split is not None:
            base["notes"] = (
                "rubric-primary 图像题：combined = reply_appropriateness × mean(tool, flow, error)；"
                "reply_appropriateness 由 vision_breakdown 六项（0/0.5/1）均值得到；"
                "oracle_outcome_score 仅审计产物文件，不参与 combined"
            )
        else:
            base["notes"] = "combined = outcome_score × process_effective"
    else:
        base["combined_score"] = round(pe, 4)
        base["notes"] = "no oracle outcome_score; combined = process_effective only"

    return base
