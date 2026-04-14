from __future__ import annotations

import argparse
import json
import time
import traceback

from clawbench_v2.config import load_app_config, load_model_config
from clawbench_v2.runner import run_task
from clawbench_v2.tasks import load_tasks


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="clawbench_v2")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("tasks")

    run_task_p = sub.add_parser("run-task")
    run_task_p.add_argument("--task", required=True)
    run_task_p.add_argument("--model", required=True)
    run_task_p.add_argument("--mode", default="live")
    run_task_p.add_argument("--delete-sandbox", action="store_true")

    run_suite_p = sub.add_parser("run-suite")
    run_suite_p.add_argument("--model", required=True)
    run_suite_p.add_argument("--mode", default="live")
    run_suite_p.add_argument("--delete-sandbox", action="store_true")
    run_suite_p.add_argument(
        "--from-task",
        metavar="TASK_ID",
        default=None,
        help="从该 task_id 起续跑（含该项）；按 tasks 排序后的切片。用于整套评测中断后从某一题接着跑。",
    )
    return p


def main() -> int:
    args = _build_parser().parse_args()
    app_cfg = load_app_config()
    model_cfgs = load_model_config()
    tasks = load_tasks(app_cfg.tasks_dir)

    if args.cmd == "tasks":
        print(json.dumps({k: {"title": v.title, "tags": v.tags} for k, v in tasks.items()}, ensure_ascii=False, indent=2))
        return 0

    if args.model not in model_cfgs:
        raise SystemExit(f"unknown model config: {args.model}")
    model_cfg = model_cfgs[args.model]

    if args.cmd == "run-task":
        if args.task not in tasks:
            raise SystemExit(f"unknown task: {args.task}")
        print(f"[clawbench_v2] run-task {args.task} (model={args.model}, mode={args.mode}) ...", flush=True)
        t0 = time.perf_counter()
        result = run_task(app_cfg, tasks[args.task], args.model, model_cfg, args.mode, keep_workspace=not args.delete_sandbox)
        elapsed_sec = round(time.perf_counter() - t0, 3)
        ok = getattr(result.adapter_result, "ok", False)
        print(f"[clawbench_v2] run-task {args.task} finished adapter_ok={ok} elapsed={elapsed_sec}s", flush=True)
        print(
            json.dumps(
                {
                    "task_id": result.task_id,
                    "elapsed_sec": elapsed_sec,
                    "usage_summary": result.usage_summary,
                    "oracle_result": result.oracle_result,
                    "scoring": result.scoring,
                    "process_result": result.process_result,
                    "combined_result": result.combined_result,
                    "sandbox": str(result.sandbox),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    if args.cmd == "run-suite":
        outputs = []
        had_failures = False
        all_ids = sorted(tasks)
        task_ids = all_ids
        if getattr(args, "from_task", None):
            ft = str(args.from_task).strip()
            if ft not in tasks:
                raise SystemExit(f"unknown --from-task: {ft!r} (not in loaded tasks)")
            task_ids = all_ids[all_ids.index(ft) :]
            print(
                f"[clawbench_v2] run-suite resuming from {ft!r}: {len(task_ids)} task(s) "
                f"(skipped {len(all_ids) - len(task_ids)} before it)",
                flush=True,
            )
        total = len(task_ids)
        if total == 0:
            print("[clawbench_v2] run-suite: no tasks to run", flush=True)
            print(json.dumps([], ensure_ascii=False, indent=2))
            return 0
        suite_t0 = time.perf_counter()
        for idx, task_id in enumerate(task_ids, start=1):
            print(
                f"[clawbench_v2] run-suite [{idx}/{total}] {task_id} (model={args.model}, mode={args.mode}) ...",
                flush=True,
            )
            t0 = time.perf_counter()
            try:
                result = run_task(app_cfg, tasks[task_id], args.model, model_cfg, args.mode, keep_workspace=not args.delete_sandbox)
            except KeyboardInterrupt:
                raise
            except Exception as exc:
                had_failures = True
                outputs.append(
                    {
                        "task_id": task_id,
                        "ok": False,
                        "error_type": type(exc).__name__,
                        "error": str(exc),
                        "traceback": traceback.format_exc(),
                    }
                )
                continue
            elapsed_sec = round(time.perf_counter() - t0, 3)
            ok = getattr(result.adapter_result, "ok", False)
            print(
                f"[clawbench_v2] run-suite [{idx}/{total}] {task_id} finished adapter_ok={ok} elapsed={elapsed_sec}s",
                flush=True,
            )
            outputs.append(
                {
                    "task_id": result.task_id,
                    "ok": True,
                    "elapsed_sec": elapsed_sec,
                    "usage_summary": result.usage_summary,
                    "oracle_result": result.oracle_result,
                    "scoring": result.scoring,
                    "process_result": result.process_result,
                    "combined_result": result.combined_result,
                    "sandbox": str(result.sandbox),
                }
            )
        suite_elapsed_sec = round(time.perf_counter() - suite_t0, 3)
        print(
            f"[clawbench_v2] run-suite finished {total} tasks wall_elapsed={suite_elapsed_sec}s",
            flush=True,
        )
        print(json.dumps(outputs, ensure_ascii=False, indent=2))
        return 1 if had_failures else 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
