from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

_TASK_DIR = Path(__file__).resolve().parent
_GT = _TASK_DIR / "ground_truth.json"


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def score_workspace(workspace: Path) -> dict[str, Any]:
    w = workspace.resolve()
    out = w / "out"
    gt = _load_json(_GT)
    weights = gt["scoring"]["weights"]
    checks: list[dict[str, Any]] = []

    def add(cid: str, label: str, ok: bool, weight: float, detail: Any = None) -> None:
        checks.append({"id": cid, "label": label, "pass": bool(ok), "weight": weight, "detail": detail})

    heartbeat_score = 0.0
    heartbeat_path = w / "HEARTBEAT.md"
    if heartbeat_path.is_file():
        text = heartbeat_path.read_text(encoding="utf-8", errors="replace")
        phrase_hits = sum(1 for phrase in gt["heartbeat_required_phrases"] if phrase.lower() in text.lower())
        has_dedupe = any(token in text.lower() for token in ["dedupe", "去重", "seen", "thread_id"])
        has_escalation = "[升级]" in text or "升级" in text or "escalation" in text.lower()
        heartbeat_score = 0.60 * (phrase_hits / len(gt["heartbeat_required_phrases"])) + 0.20 * has_dedupe + 0.20 * has_escalation
        add("heartbeat_config", "HEARTBEAT.md describes scan, dedupe, escalation, and sleep", heartbeat_score >= 0.70, weights["heartbeat_config"], {"score": round(heartbeat_score, 4), "phrase_hits": phrase_hits})
    else:
        add("heartbeat_missing", "HEARTBEAT.md exists", False, weights["heartbeat_config"], "missing")

    notification_score = 0.0
    notif_path = out / "notifications.txt"
    if notif_path.is_file():
        text = notif_path.read_text(encoding="utf-8", errors="replace")
        required_hits = sum(1 for line in gt["expected_notifications"] if line in text)
        forbidden_hits = sum(1 for frag in gt["forbidden_notification_fragments"] if frag in text)
        duplicate_penalty = 0
        for line in gt["expected_notifications"]:
            if text.count(line) > 1:
                duplicate_penalty += text.count(line) - 1
        notification_score = 0.85 * (required_hits / len(gt["expected_notifications"])) + 0.15 * (forbidden_hits == 0)
        notification_score = max(0.0, notification_score - 0.10 * duplicate_penalty)
        add("notifications", "notifications.txt includes urgent and escalation messages without noise", notification_score >= 0.70, weights["notifications"], {"score": round(notification_score, 4), "required_hits": required_hits, "forbidden_hits": forbidden_hits, "duplicate_penalty": duplicate_penalty})
    else:
        add("notifications_missing", "notifications.txt exists", False, weights["notifications"], "missing")

    state_score = 0.0
    state_path = out / "heartbeat_state.json"
    if state_path.is_file():
        try:
            state = _load_json(state_path)
            seen_ids = state.get("seen_ids", [])
            thread_counts = state.get("thread_counts", {})
            seen_text = json.dumps(seen_ids, ensure_ascii=False)
            expected_seen_hits = sum(1 for mid in gt["expected_seen_ids"] if mid in seen_text)
            thread_ok = isinstance(thread_counts, dict) and int(thread_counts.get(gt["expected_thread"], 0) or 0) >= 2
            last_scan_ok = bool(state.get("last_scan_at"))
            interval_ok = 0 < float(state.get("poll_interval_seconds", 0) or 0) <= 10
            state_score = 0.45 * (expected_seen_hits / len(gt["expected_seen_ids"])) + 0.25 * thread_ok + 0.15 * last_scan_ok + 0.15 * interval_ok
            add("state_file", "heartbeat_state.json records seen ids, thread counts, scan time, poll interval", state_score >= 0.70, weights["state_file"], {"score": round(state_score, 4), "expected_seen_hits": expected_seen_hits, "thread_ok": thread_ok})
        except Exception as exc:
            add("state_file_parse", "heartbeat_state.json parseable", False, weights["state_file"], str(exc))
    else:
        add("state_file_missing", "heartbeat_state.json exists", False, weights["state_file"], "missing")

    summary_score = 0.0
    summary_path = out / "escalation_summary.md"
    if summary_path.is_file():
        text = summary_path.read_text(encoding="utf-8", errors="replace")
        hits = sum(1 for token in ["m_async_100", "m_async_101", "sec-777", "去重", "升级"] if token.lower() in text.lower())
        summary_score = min(hits / 5, 1.0)
        add("summary", "escalation_summary.md explains urgent detection, dedupe, and escalation", summary_score >= 0.70, weights["summary"], {"score": round(summary_score, 4), "hits": hits})
    else:
        add("summary_missing", "escalation_summary.md exists", False, weights["summary"], "missing")

    timing_score = 0.0
    if notif_path.is_file():
        try:
            elapsed = notif_path.stat().st_mtime - w.stat().st_ctime
            timing_score = 1.0 if elapsed >= gt["min_runtime_seconds"] else 0.5 if elapsed >= 10 else 0.0
            add("timing", "notifications were produced after asynchronous injection window", timing_score >= 0.5, weights["timing"], {"elapsed_seconds": round(elapsed, 2), "score": timing_score})
        except OSError as exc:
            add("timing_error", "timing metadata readable", False, weights["timing"], str(exc))
    else:
        add("timing_missing", "notifications timing cannot be checked", False, weights["timing"], "notifications missing")

    total = (
        heartbeat_score * weights["heartbeat_config"]
        + notification_score * weights["notifications"]
        + state_score * weights["state_file"]
        + summary_score * weights["summary"]
        + timing_score * weights["timing"]
    )
    thresholds = gt["scoring"]["thresholds"]
    level = "excellent" if total >= thresholds["excellent"] else "good" if total >= thresholds["good"] else "pass" if total >= thresholds["pass"] else "fail"
    return {
        "task": "20-heartbeat-escalation",
        "workspace": str(w),
        "outcome_score": round(float(total), 4),
        "level": level,
        "checks": checks,
        "summary": {
            "heartbeat_config": round(float(heartbeat_score), 4),
            "notifications": round(float(notification_score), 4),
            "state_file": round(float(state_score), 4),
            "summary": round(float(summary_score), 4),
            "timing": round(float(timing_score), 4),
        },
    }
