from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

from clawbench_v2.adapters.base import BaseAdapter
from clawbench_v2.models import AdapterRunContext, AdapterRunResult


def _resolve_project_relative(raw: str | Path, root: Path) -> Path:
    p = Path(os.path.expanduser(str(raw)))
    if not p.is_absolute():
        p = root / p
    return p.resolve()


def _project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _parse_nanoclaw_stdout(stdout_text: str) -> dict[str, Any] | None:
    text = (stdout_text or "").strip()
    if not text:
        return None

    start_marker = "---NANOCLAW_OUTPUT_START---"
    end_marker = "---NANOCLAW_OUTPUT_END---"
    if start_marker in text and end_marker in text:
        start_idx = text.index(start_marker) + len(start_marker)
        end_idx = text.index(end_marker)
        payload = text[start_idx:end_idx].strip()
        try:
            return json.loads(payload)
        except json.JSONDecodeError:
            return None

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    for line in reversed([line.strip() for line in text.splitlines() if line.strip()]):
        if line.startswith("{") and line.endswith("}"):
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                continue
    return None


def _prepare_state_dir(raw: Any) -> Path | None:
    if not raw:
        return None
    p = Path(os.path.expanduser(str(raw)))
    if not p.is_absolute():
        p = _project_root() / p
    p = p.resolve()
    p.mkdir(parents=True, exist_ok=True)
    return p


class NanoClawAdapter(BaseAdapter):
    name = "nanoclaw"

    def run(self, ctx: AdapterRunContext) -> AdapterRunResult:
        command = str(ctx.model_config.get("command") or "nanoclaw")
        args = list(ctx.model_config.get("args") or ["agent"])
        cmd = [command, *[str(x) for x in args], "--message", ctx.prompt]

        env = os.environ.copy()
        env.update(ctx.env)
        state_dir = _prepare_state_dir(ctx.model_config.get("state_dir"))
        workspace_mount_path = str(ctx.model_config.get("workspace_mount_path") or "/workspace")
        env["CLAWBENCH_TASK_ID"] = ctx.task.task_id
        env["CLAWBENCH_WORKSPACE"] = str(ctx.workspace)
        env["CLAWBENCH_SANDBOX"] = str(ctx.sandbox)
        env["CLAWBENCH_SESSION_ID"] = ctx.session_id
        env["CLAWBENCH_PROMPT_FILE"] = str(ctx.prompt_file)
        env["NANOCLAW_WORKSPACE_DIR"] = str(ctx.workspace)
        env["NANOCLAW_WORKSPACE_MOUNT_PATH"] = workspace_mount_path
        if state_dir is not None:
            env["NANOCLAW_STATE_DIR"] = str(state_dir)

        workdir_raw = ctx.model_config.get("workdir")
        cwd = str(_resolve_project_relative(workdir_raw, _project_root())) if workdir_raw else str(ctx.workspace)

        completed = subprocess.run(
            cmd,
            cwd=cwd,
            text=True,
            capture_output=True,
            timeout=ctx.timeout_sec,
            env=env,
            check=False,
        )

        payload = _parse_nanoclaw_stdout(completed.stdout)
        metadata: dict[str, Any] = {
            "returncode": completed.returncode,
            "workspace": str(ctx.workspace),
            "cwd": cwd,
            "state_dir": str(state_dir) if state_dir is not None else "",
            "workspace_mount_path": workspace_mount_path,
        }
        if payload is not None:
            metadata["nanoclaw_raw"] = payload

        return AdapterRunResult(
            ok=completed.returncode == 0,
            command=cmd,
            stdout=completed.stdout,
            stderr=completed.stderr,
            metadata=metadata,
        )
