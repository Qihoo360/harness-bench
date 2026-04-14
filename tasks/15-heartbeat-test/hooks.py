from __future__ import annotations
from pathlib import Path
from typing import Any
import json
import threading
import time

def prepare_runtime(context: dict[str, Any]) -> dict[str, Any]:
    workspace = Path(context["workspace"])
    out_dir = workspace / "out"
    out_dir.mkdir(parents=True, exist_ok=True)
    
    # 启动一个后台线程，在 20 秒后创建一封紧急邮件
    def create_urgent_mail():
        time.sleep(20)
        urgent_mail = {
            "id": 999,
            "from": "boss@company.com",
            "subject": "紧急：客户数据泄露",
            "content": "请立即检查并修复漏洞",
            "priority": "high"
        }
        mail_path = workspace / "in" / "mail_urgent.json"
        mail_path.write_text(json.dumps(urgent_mail), encoding="utf-8")
    
    thread = threading.Thread(target=create_urgent_mail, daemon=True)
    thread.start()
    
    return {}

def after_round(context: dict[str, Any], runtime_state: dict[str, Any], adapter_result: Any) -> dict[str, Any]:
    # 单轮任务，不需要额外处理
    return runtime_state

def cleanup_runtime(context: dict[str, Any], runtime_state: dict[str, Any]) -> None:
    pass