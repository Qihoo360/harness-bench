from __future__ import annotations

import random
import subprocess
import time
from pathlib import Path
from typing import Any


def prepare_runtime(runtime: dict[str, Any]) -> dict[str, Any]:
    workspace = Path(runtime["workspace"])
    port = 32000 + random.randint(0, 2000)
    www = workspace / "www"
    proc = subprocess.Popen(
        ["python3", "-m", "http.server", str(port), "--directory", str(www)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    time.sleep(0.3)
    return {
        "HTTP_PORT": str(port),
        "MOCK_PAGE": f"http://127.0.0.1:{port}/",
        "server_pid": proc.pid,
    }


def cleanup_runtime(runtime: dict[str, Any], state: dict[str, Any]) -> None:
    pid = int(state.get("server_pid", 0) or 0)
    if pid <= 0:
        return
    try:
        import os

        os.kill(pid, 15)
    except OSError:
        pass
