"""Compute final benchmark report: Completion (outcome, all tasks),
Process & Combined (only tasks with valid rubric numbers), and token totals.

Usage: python report_final.py <results_dir>
  e.g. python report_final.py data/parallel/20260613_xxxxxx/round_01/results/ga-local
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def main(results_dir: str) -> int:
    rd = Path(results_dir)
    files = sorted(f for f in os.listdir(rd) if f.endswith(".json"))

    rows = []
    for f in files:
        d = json.loads((rd / f).read_text(encoding="utf-8"))
        tid = f.replace(".json", "")
        oracle = d.get("oracle_result", {})
        cr = d.get("combined_result", {})
        pr = d.get("process_result", {})
        usage = d.get("usage_summary", {})

        completion = oracle.get("outcome_score")
        if completion is None:
            completion = oracle.get("score")
        process = cr.get("process_score")
        combined = cr.get("combined_score")
        # process reason if N/A
        proc_reason = ""
        if process is None:
            proc_reason = pr.get("reason", "") or ("non-standard rubric output" if pr.get("available") else "rubric unavailable")

        rows.append({
            "task": tid,
            "completion": float(completion) if completion is not None else None,
            "process": float(process) if process is not None else None,
            "combined": float(combined) if combined is not None else None,
            "proc_reason": proc_reason,
            "input": usage.get("input_tokens", 0) or 0,
            "output": usage.get("output_tokens", 0) or 0,
            "total": usage.get("total_tokens", 0) or 0,
        })

    print("=" * 92)
    print(f"{'Task':<40} {'Completion':>10} {'Process':>9} {'Combined':>9} {'Input':>7} {'Output':>7}")
    print("=" * 92)
    for r in rows:
        c = f"{r['completion']:.4f}" if r["completion"] is not None else "  N/A "
        p = f"{r['process']:.4f}" if r["process"] is not None else "  N/A "
        cb = f"{r['combined']:.4f}" if r["combined"] is not None else "  N/A "
        print(f"{r['task']:<40} {c:>10} {p:>9} {cb:>9} {r['input']:>7} {r['output']:>7}")
    print("=" * 92)

    # Completion: all tasks (always present)
    comps = [r["completion"] for r in rows if r["completion"] is not None]
    # Process & Combined: only tasks that have a valid process number (rubric ran)
    procs = [r["process"] for r in rows if r["process"] is not None]
    combs = [r["combined"] for r in rows if r["process"] is not None and r["combined"] is not None]
    inputs = [r["input"] for r in rows]
    outputs = [r["output"] for r in rows]
    totals = [r["total"] for r in rows]

    print()
    print(f"COMPLETION (outcome, all {len(comps)} tasks):   {sum(comps)/len(comps)*100:.2f}%")
    if procs:
        print(f"PROCESS    (only {len(procs)} tasks w/ valid rubric): {sum(procs)/len(procs)*100:.2f}%")
    else:
        print(f"PROCESS:    no valid rubric scores")
    if combs:
        print(f"COMBINED   (only {len(combs)} tasks w/ valid rubric): {sum(combs)/len(combs)*100:.2f}%")
    else:
        print(f"COMBINED:   no valid rubric scores")
    print()
    print(f"TOKENS (avg per task):  input={sum(inputs)/len(inputs):.0f}  output={sum(outputs)/len(outputs):.0f}  total={sum(totals)/len(totals):.0f}")
    print(f"TOKENS (sum, {len(rows)} tasks): input={sum(inputs):,}  output={sum(outputs):,}  total={sum(totals):,}")
    print()

    # Tasks with N/A process (official rubric bugs)
    na = [r for r in rows if r["process"] is None]
    if na:
        print(f"Tasks with N/A Process ({len(na)}) — official rubric bug, not scored:")
        for r in na:
            print(f"  {r['task']:<40} {r['proc_reason'][:50]}")

    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: python report_final.py <results_dir>")
        raise SystemExit(1)
    raise SystemExit(main(sys.argv[1]))
