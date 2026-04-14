from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any


def prepare_runtime(context: dict[str, Any]) -> dict[str, Any]:
    workspace = Path(context["workspace"])
    (workspace / "in").mkdir(parents=True, exist_ok=True)
    (workspace / "out").mkdir(parents=True, exist_ok=True)
    start_time = time.time()

    def inject_messages() -> None:
        events = [
            (
                6,
                "mail_async_100.json",
                {
                    "id": "m_async_100",
                    "from": "security@company.com",
                    "subject": "高危漏洞确认",
                    "content": "请值班同学检查 WAF 与登录网关日志。",
                    "priority": "high",
                    "thread_id": "sec-777",
                    "received_at": "2026-04-07T14:20:06+08:00",
                },
            ),
            (
                12,
                "mail_async_noise.json",
                {
                    "id": "m_async_noise",
                    "from": "newsletter@example.com",
                    "subject": "newsletter 周报",
                    "content": "低优先级产品更新。",
                    "priority": "low",
                    "thread_id": "news-1",
                    "received_at": "2026-04-07T14:20:12+08:00",
                },
            ),
            (
                18,
                "mail_async_101.json",
                {
                    "id": "m_async_101",
                    "from": "security@company.com",
                    "subject": "补充：漏洞已被外部扫描",
                    "content": "同一事件已有外部扫描迹象，请升级给值班负责人。",
                    "priority": "high",
                    "thread_id": "sec-777",
                    "received_at": "2026-04-07T14:20:18+08:00",
                },
            ),
        ]
        for delay, filename, payload in events:
            remaining = start_time + delay - time.time()
            if remaining > 0:
                time.sleep(remaining)
            (workspace / "in" / filename).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    thread = threading.Thread(target=inject_messages, daemon=True)
    thread.start()
    return {
        "HEARTBEAT_TEST_START": str(start_time),
        "ASYNC_INJECTION_SECONDS": "6,12,18",
    }


def after_round(context: dict[str, Any], runtime_state: dict[str, Any], adapter_result: Any) -> dict[str, Any]:
    return runtime_state


def cleanup_runtime(context: dict[str, Any], runtime_state: dict[str, Any]) -> None:
    pass
