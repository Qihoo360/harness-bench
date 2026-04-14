from __future__ import annotations

import os
import subprocess

from clawbench_v2.adapters.base import BaseAdapter
from clawbench_v2.models import AdapterRunContext, AdapterRunResult


class GenericCliAdapter(BaseAdapter):
    name = "generic_cli"

    def run(self, ctx: AdapterRunContext) -> AdapterRunResult:
        args = list(ctx.model_config.get("args") or [])
        if not args:
            raise ValueError("generic_cli adapter requires model_config.args")
        command = str(ctx.model_config.get("command") or "")
        if not command:
            raise ValueError("generic_cli adapter requires model_config.command")

        fmt = {
            "workspace": str(ctx.workspace),
            "sandbox": str(ctx.sandbox),
            "prompt_file": str(ctx.prompt_file),
            "session_id": ctx.session_id,
            "task_id": ctx.task.task_id,
            "model_id": ctx.model_id,
        }
        cmd = [command, *[str(x).format(**fmt) for x in args]]
        env = os.environ.copy()
        env.update(ctx.env)
        env["CLAWBENCH_TASK_ID"] = ctx.task.task_id
        env["CLAWBENCH_WORKSPACE"] = str(ctx.workspace)
        env["CLAWBENCH_SANDBOX"] = str(ctx.sandbox)
        env["CLAWBENCH_SESSION_ID"] = ctx.session_id
        env["CLAWBENCH_PROMPT_FILE"] = str(ctx.prompt_file)
        env["CLAWBENCH_MODEL_ID"] = ctx.model_id
        if ctx.env.get("CLAWBENCH_LLM_PROXY_URL"):
            env["CLAWBENCH_LLM_PROXY_URL"] = str(ctx.env["CLAWBENCH_LLM_PROXY_URL"])
        if ctx.env.get("CLAWBENCH_LLM_PROXY_ROUTES"):
            env["CLAWBENCH_LLM_PROXY_ROUTES"] = str(ctx.env["CLAWBENCH_LLM_PROXY_ROUTES"])
        completed = subprocess.run(
            cmd,
            cwd=str(ctx.workspace),
            text=True,
            capture_output=True,
            timeout=ctx.timeout_sec,
            env=env,
            check=False,
        )
        return AdapterRunResult(
            ok=completed.returncode == 0,
            command=cmd,
            stdout=completed.stdout,
            stderr=completed.stderr,
            metadata={"returncode": completed.returncode},
        )
