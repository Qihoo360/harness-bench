from __future__ import annotations

import json
import shutil
import tempfile
import uuid
from pathlib import Path
from typing import Any

from clawbench_v2.extract_proxy_trace import extract_proxy_trace
from clawbench_v2.grading.process_grade import compute_scoring
from clawbench_v2.models import AdapterRunContext, AppConfig, TaskRunResult, TaskSpec
from clawbench_v2.process_grading import run_process_rubric
from clawbench_v2.registry import build_adapter
from clawbench_v2.tasks import load_hooks, run_oracle
from clawbench_v2.usage_proxy import UsageProxy


def render_prompt_file(task: TaskSpec, prompt_name: str, workspace: Path, runtime_env: dict[str, str]) -> str:
    assert task.task_dir is not None
    prompt_template = (task.task_dir / prompt_name).read_text(encoding="utf-8")
    rendered = prompt_template.replace("$WORKSPACE", str(workspace))
    for key, value in runtime_env.items():
        rendered = rendered.replace(f"${key}", str(value))
    return rendered


def render_prompt(task: TaskSpec, workspace: Path, runtime_env: dict[str, str]) -> str:
    return render_prompt_file(task, task.prompt_file, workspace, runtime_env)


def _copy_fixtures(task: TaskSpec, workspace: Path) -> None:
    assert task.task_dir is not None
    fixtures = task.task_dir / task.fixtures_dir
    workspace.mkdir(parents=True, exist_ok=True)
    # Keep benchmark workspaces structurally consistent so adapters can rely on
    # `workspace/in` and `workspace/out` even when a task has no fixtures yet.
    (workspace / "in").mkdir(parents=True, exist_ok=True)
    (workspace / "out").mkdir(parents=True, exist_ok=True)
    if fixtures.is_dir():
        for child in fixtures.iterdir():
            dest = workspace / child.name
            if child.is_dir():
                shutil.copytree(child, dest, dirs_exist_ok=True)
            else:
                shutil.copy2(child, dest)


def _new_session_id(model_cfg: dict[str, Any], task_id: str) -> str:
    prefix = str(model_cfg.get("session_prefix", "clawbenchv2"))
    return f"{prefix}-{task_id}-{uuid.uuid4().hex[:8]}"


def _sandbox_prefix(task_id: str) -> str:
    safe = "".join(ch if ch.isalnum() or ch in ("-", "_") else "-" for ch in task_id).strip("-_")
    safe = safe or "task"
    return f"oc-bench-v2-{safe}-"

def _collect_hermes_usage_summary(db_file: Path, session_id: str, session_root: Path) -> dict[str, Any]:
    """从 Hermes SQLite 数据库收集 usage 信息"""
    try:
        import sqlite3
    except ImportError:
        return {
            "available": False,
            "reason": "sqlite3 module not available",
            "session_id": session_id,
            "usage_root": str(session_root),
            "database_file": str(db_file),
        }
    
    try:
        conn = sqlite3.connect(db_file)
        cursor = conn.cursor()
        
        # 查询最近创建的会话
        cursor.execute(
            "SELECT id, title, started_at, model, billing_provider, input_tokens, output_tokens, message_count FROM sessions ORDER BY started_at DESC LIMIT 1"
        )
        latest_session = cursor.fetchone()
        
        if not latest_session:
            return {
                "available": False,
                "reason": "Hermes database has no sessions",
                "session_id": session_id,
                "usage_root": str(session_root),
                "database_file": str(db_file),
            }
        
        # 使用数据库中的实际会话ID
        session_id_in_db = latest_session[0]  # 数据库中的ID
        
        # 从 sessions 表获取 token 统计
        input_tokens = latest_session[5] or 0  # input_tokens
        output_tokens = latest_session[6] or 0  # output_tokens
        total_tokens = input_tokens + output_tokens
        
        # 查询该会话的所有消息
        cursor.execute(
            "SELECT role, content, token_count FROM messages WHERE session_id = ? ORDER BY timestamp",
            (session_id_in_db,)
        )
        messages = cursor.fetchall()
        
        # 如果 sessions 表没有 token 统计，尝试从 messages 表计算
        if input_tokens == 0 and output_tokens == 0:
            for role, content, token_count in messages:
                if token_count is not None:
                    total_tokens += int(token_count)
                    if role == "user":
                        input_tokens += int(token_count)
                    elif role == "assistant":
                        output_tokens += int(token_count)
        
        # 收集 usage 信息
        summary: dict[str, Any] = {
            "available": True,
            "source": "hermes_sqlite",
            "session_id": session_id_in_db,  # 数据库中的实际ID
            "original_session_id": session_id,  # ClawBenchv2生成的ID
            "usage_root": str(session_root),
            "database_file": str(db_file),
            "message_count": len(messages),
            "usage_message_count": len(messages),
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": total_tokens,
            "providers": [latest_session[4] or "hermes"],  # billing_provider
            "models": [latest_session[3] or "unknown"],    # model
        }
        
        conn.close()
        return summary
        
    except sqlite3.Error as e:
        return {
            "available": False,
            "reason": f"Hermes database error: {str(e)}",
            "session_id": session_id,
            "usage_root": str(session_root),
            "database_file": str(db_file),
        }

def _collect_usage_summary(adapter_result: Any, session_id: str) -> dict[str, Any]:
    metadata = getattr(adapter_result, "metadata", {}) or {}
    usage_root_raw = str(
        metadata.get("state_dir")
        or metadata.get("openclaw_home")
        or metadata.get("nanobot_home")
        or metadata.get("zeroclaw_home")
        or metadata.get("picoclaw_workspace")
        or metadata.get("hermes_home")
        or ""
    ).strip()
    if not usage_root_raw:
        return {
            "available": False,
            "reason": "adapter metadata has no usage root",
            "session_id": session_id,
        }

    session_root = Path(usage_root_raw)

    # 检查是否是 Hermes SQLite 数据库
    db_file = session_root / "state.db"
    if db_file.exists():
        # Hermes 使用 SQLite，调用专门的函数处理
        return _collect_hermes_usage_summary(db_file, session_id, session_root)
    
    candidates = [
        session_root / "agents" / "main" / "sessions" / f"{session_id}.jsonl",
        session_root / "agent" / "sessions" / f"{session_id}.jsonl",
        session_root / "sessions" / f"{session_id}.jsonl",
        session_root / "workspace" / "sessions" / f"{session_id}.jsonl",
    ]
    session_file = next((path for path in candidates if path.is_file()), None)
    if session_file is None:
        exact = list(session_root.rglob(f"{session_id}.jsonl"))
        if exact:
            session_file = exact[0]
    if session_file is None:
        all_jsonl = sorted(
            session_root.rglob("*.jsonl"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        if all_jsonl:
            session_file = all_jsonl[0]
    if session_file is None:
        return {
            "available": False,
            "reason": "session jsonl not found",
            "session_id": session_id,
            "usage_root": usage_root_raw,
            "session_file": str(candidates[0]),
        }

    summary: dict[str, Any] = {
        "available": True,
        "session_id": session_id,
        "usage_root": usage_root_raw,
        "session_file": str(session_file),
        "message_count": 0,
        "usage_message_count": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
        "total_tokens": 0,
        "cost_input": 0,
        "cost_output": 0,
        "cost_cache_read": 0,
        "cost_cache_write": 0,
        "cost_total": 0,
        "providers": [],
        "models": [],
    }
    providers: set[str] = set()
    models: set[str] = set()

    for line in session_file.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        message = row.get("message") or {}
        if message:
            summary["message_count"] += 1
        usage = message.get("usage") or {}
        if usage:
            summary["usage_message_count"] += 1
            summary["input_tokens"] += int(usage.get("input", 0) or 0)
            summary["output_tokens"] += int(usage.get("output", 0) or 0)
            summary["cache_read_tokens"] += int(usage.get("cacheRead", 0) or 0)
            summary["cache_write_tokens"] += int(usage.get("cacheWrite", 0) or 0)
            summary["total_tokens"] += int(usage.get("totalTokens", 0) or 0)
            cost = usage.get("cost") or {}
            summary["cost_input"] += float(cost.get("input", 0) or 0)
            summary["cost_output"] += float(cost.get("output", 0) or 0)
            summary["cost_cache_read"] += float(cost.get("cacheRead", 0) or 0)
            summary["cost_cache_write"] += float(cost.get("cacheWrite", 0) or 0)
            summary["cost_total"] += float(cost.get("total", 0) or 0)
        provider = str(message.get("provider", "")).strip()
        model = str(message.get("model", "")).strip()
        if provider:
            providers.add(provider)
        if model:
            models.add(model)

    summary["providers"] = sorted(providers)
    summary["models"] = sorted(models)
    return summary


def _collect_proxy_usage_summary(log_file: Path, session_id: str) -> dict[str, Any]:
    if not log_file.is_file():
        return {
            "available": False,
            "reason": "usage proxy log not found",
            "session_id": session_id,
            "log_file": str(log_file),
        }

    summary: dict[str, Any] = {
        "available": True,
        "source": "proxy",
        "session_id": session_id,
        "log_file": str(log_file),
        "request_count": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
        "total_tokens": 0,
        "providers": [],
        "models": [],
    }
    providers: set[str] = set()
    models: set[str] = set()

    for line in log_file.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        summary["request_count"] += 1
        summary["input_tokens"] += int(row.get("input_tokens", 0) or 0)
        summary["output_tokens"] += int(row.get("output_tokens", 0) or 0)
        summary["cache_read_tokens"] += int(row.get("cache_read_tokens", 0) or 0)
        summary["cache_write_tokens"] += int(row.get("cache_write_tokens", 0) or 0)
        summary["total_tokens"] += int(row.get("total_tokens", 0) or 0)
        provider = str(row.get("provider", "")).strip()
        model = str(row.get("response_model", "")).strip()
        if provider:
            providers.add(provider)
        if model:
            models.add(model)

    if summary["request_count"] == 0:
        return {
            "available": False,
            "reason": "usage proxy saw no requests",
            "session_id": session_id,
            "log_file": str(log_file),
        }

    summary["providers"] = sorted(providers)
    summary["models"] = sorted(models)
    return summary


def run_task(app: AppConfig, task: TaskSpec, model_id: str, model_cfg: dict[str, Any], mode: str, keep_workspace: bool = True) -> TaskRunResult:
    sandbox = Path(tempfile.mkdtemp(prefix=_sandbox_prefix(task.task_id), dir=str(app.work_root)))
    workspace = sandbox / "workspace"
    _copy_fixtures(task, workspace)

    runtime_env: dict[str, str] = {}
    hooks = load_hooks(task)
    runtime_state: dict[str, Any] = {}
    if hooks and callable(getattr(hooks, "prepare_runtime", None)):
        state = hooks.prepare_runtime({"task": task, "sandbox": sandbox, "workspace": workspace})
        if isinstance(state, dict):
            runtime_state.update(state)
            for key, value in state.items():
                if isinstance(value, str):
                    runtime_env[key] = value

    adapter_name = str(model_cfg.get("adapter") or "demo")
    adapter = build_adapter(adapter_name)
    session_id = _new_session_id(model_cfg, task.task_id)
    prompt_names = list(task.prompt_files or []) or [task.prompt_file]
    adapter_results = []
    prompt_file = sandbox / "prompt.txt"
    adapter_result = None
    proxy_dir = sandbox / "usage-proxy"
    proxy_routes = proxy_dir / "routes.json"
    proxy_log = proxy_dir / "requests.jsonl"
    proxy_raw_dir = proxy_dir / "responses"
    with UsageProxy(proxy_routes, proxy_log, proxy_raw_dir, task.task_id, session_id, model_id) as proxy:
        runtime_env["CLAWBENCH_LLM_PROXY_URL"] = proxy.base_url
        runtime_env["CLAWBENCH_LLM_PROXY_ROUTES"] = str(proxy_routes)
        for round_index, prompt_name in enumerate(prompt_names):
            prompt = render_prompt_file(task, prompt_name, workspace, runtime_env)
            prompt_file = sandbox / f"prompt-round{round_index + 1}.txt"
            prompt_file.write_text(prompt, encoding="utf-8")
            ctx = AdapterRunContext(
                task=task,
                workspace=workspace,
                sandbox=sandbox,
                prompt=prompt,
                prompt_file=prompt_file,
                session_id=session_id,
                timeout_sec=int(model_cfg.get("timeout_sec", task.timeout_sec or app.default_timeout_sec)),
                env=runtime_env,
                model_id=model_id,
                model_config=model_cfg,
                mode=mode,
            )
            adapter_result = adapter.run(ctx)
            adapter_results.append(adapter_result)
            if hooks and callable(getattr(hooks, "after_round", None)):
                after_state = hooks.after_round(
                    {
                        "task": task,
                        "sandbox": sandbox,
                        "workspace": workspace,
                        "session_id": session_id,
                        "round_index": round_index,
                        "prompt_file": prompt_file,
                        "prompt_name": prompt_name,
                    },
                    runtime_state,
                    adapter_result,
                )
                if isinstance(after_state, dict):
                    runtime_state.update(after_state)
                    for key, value in after_state.items():
                        if isinstance(value, str):
                            runtime_env[key] = value
            if not adapter_result.ok:
                break

    assert adapter_result is not None
    usage_summary = _collect_proxy_usage_summary(proxy_log, session_id)
    if not usage_summary.get("available"):
        usage_summary = _collect_usage_summary(adapter_result, session_id)
    oracle_result = run_oracle(task, workspace)
    process_result = (
        run_process_rubric(task.task_dir, task.task_id, adapter_result.metadata or {}, session_id)
        if task.task_dir is not None
        else {"available": False, "skipped": True, "reason": "missing task_dir"}
    )
    combined_result: dict[str, Any] = {"available": False, "combined_score": None}
    outcome_score = oracle_result.get("outcome_score")
    process_score = process_result.get("total")
    if isinstance(outcome_score, (int, float)) and isinstance(process_score, (int, float)):
        combined_result = {
            "available": True,
            "blend": "multiply",
            "outcome_score": float(outcome_score),
            "process_score": float(process_score),
            "combined_score": round(float(outcome_score) * float(process_score), 4),
        }
    elif isinstance(outcome_score, (int, float)):
        combined_result = {
            "available": True,
            "blend": "outcome_only",
            "outcome_score": float(outcome_score),
            "process_score": None,
            "combined_score": round(float(outcome_score), 4),
        }
    elif isinstance(process_score, (int, float)):
        combined_result = {
            "available": True,
            "blend": "process_only",
            "outcome_score": None,
            "process_score": float(process_score),
            "combined_score": round(float(process_score), 4),
        }

    try:
        scoring = compute_scoring(task, sandbox, oracle_result)
    except Exception as exc:
        scoring = {
            "error": str(exc),
            "combined_score": None,
            "notes": "compute_scoring failed",
        }

    try:
        scoring = compute_scoring(task, sandbox, oracle_result)
    except Exception as exc:
        scoring = {
            "error": str(exc),
            "combined_score": None,
            "notes": "compute_scoring failed",
        }

    result = TaskRunResult(
        task_id=task.task_id,
        model_id=model_id,
        mode=mode,
        sandbox=sandbox,
        workspace=workspace,
        session_id=session_id,
        prompt_file=prompt_file,
        adapter_result=adapter_result,
        adapter_results=adapter_results,
        oracle_result=oracle_result,
        workspace_kept=keep_workspace,
        process_result=process_result,
        combined_result=combined_result,
        usage_summary=usage_summary,
        runtime_state={k: v for k, v in runtime_state.items() if isinstance(v, (str, int, float, bool))},
        scoring=scoring,
    )

    result_dir = app.results_dir / model_id
    result_dir.mkdir(parents=True, exist_ok=True)
    out_file = result_dir / f"{task.task_id}.json"
    proxy_dir = sandbox / "usage-proxy"
    trace_for_stdout = extract_proxy_trace(proxy_dir, all_rounds=False)
    adapter_stdout_saved = json.dumps(trace_for_stdout, ensure_ascii=False, indent=2)
    out_file.write_text(
        json.dumps(
            {
                "task_id": result.task_id,
                "model_id": result.model_id,
                "mode": result.mode,
                "sandbox": str(result.sandbox),
                "workspace": str(result.workspace),
                "session_id": result.session_id,
                "prompt_file": str(result.prompt_file),
                "adapter_result": {
                    "ok": result.adapter_result.ok,
                    "command": result.adapter_result.command,
                    "stdout": adapter_stdout_saved,
                    "stderr": result.adapter_result.stderr,
                    "metadata": result.adapter_result.metadata,
                },
                "adapter_results": [
                    {
                        "ok": item.ok,
                        "command": item.command,
                        "stdout": item.stdout,
                        "stderr": item.stderr,
                        "metadata": item.metadata,
                    }
                    for item in result.adapter_results
                ],
                "usage_summary": result.usage_summary,
                "oracle_result": result.oracle_result,
                "scoring": scoring,
                "process_result": result.process_result,
                "combined_result": result.combined_result,
                "runtime_state": result.runtime_state,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    if hooks and callable(getattr(hooks, "cleanup_runtime", None)):
        hooks.cleanup_runtime({"task": task, "sandbox": sandbox, "workspace": workspace}, runtime_state)

    if not keep_workspace:
        shutil.rmtree(sandbox, ignore_errors=True)
        result.workspace_kept = False
    return result
