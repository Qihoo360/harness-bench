from __future__ import annotations

import importlib.util
import json
import os
import re
import tomllib
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _load_module(path: Path, name: str) -> Any | None:
    if not path.is_file():
        return None
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

def _iter_hermes_state_db(db_path: Path, session_id: str = None) -> list[dict[str, Any]]:
    try:
        import sqlite3
    except ImportError:
        return []
    
    try:
        conn = sqlite3.connect(str(db_path))
        cursor = conn.cursor()
        
        # 查询Hermes的messages表
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='messages'")
        if not cursor.fetchone():
            # 如果没有messages表，尝试其他可能的表名
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND (name LIKE '%message%' OR name LIKE '%chat%')")
            tables = cursor.fetchall()
            if not tables:
                return []
            table_name = tables[0][0]
        else:
            table_name = "messages"
        
        # 查询消息 - 增加session_id过滤
        if session_id:
            # 只查询特定session_id的消息
            cursor.execute(f"SELECT * FROM {table_name} WHERE session_id = ? ORDER BY timestamp ASC", (session_id,))
        else:
            # 如果没有session_id，获取最新的会话ID
            cursor.execute("SELECT session_id FROM sessions ORDER BY started_at DESC LIMIT 1")
            latest_session = cursor.fetchone()
            if latest_session:
                latest_session_id = latest_session[0]
                cursor.execute(f"SELECT * FROM {table_name} WHERE session_id = ? ORDER BY timestamp ASC", (latest_session_id,))
            else:
                cursor.execute(f"SELECT * FROM {table_name} ORDER BY timestamp ASC")
        
        columns = [desc[0] for desc in cursor.description]
        rows = []
        for line_no, row in enumerate(cursor.fetchall(), 1):
            row_dict = dict(zip(columns, row))
            row_dict["_line"] = line_no
            rows.append(row_dict)
        
        conn.close()
        return rows
    except Exception:
        return []


def _iter_jsonl(path: Path) -> list[dict[str, Any]]:
    # 处理Hermes state.db::session_id格式
    if path.name == "state.db":
        # 检查路径是否包含session_id (path:session_id格式)
        path_str = str(path)
        if "::" in path_str:
            parts = path_str.split("::")
            db_path = Path(parts[0])
            session_id = parts[1] if len(parts) > 1 else None
            return _iter_hermes_state_db(db_path, session_id)
        else:
            # 没有session_id，读取整个数据库
            return _iter_hermes_state_db(path, None)
        
    rows: list[dict[str, Any]] = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            obj["_line"] = line_no
            rows.append(obj)
    return rows


def _text_snippet(text: str, max_len: int = 200) -> str:
    text = text.replace("\n", " ").strip()
    return text if len(text) <= max_len else text[: max_len - 3] + "..."


def _content_to_text(content: Any) -> str:
    if isinstance(content, str):
        return content.strip()
    if not isinstance(content, list):
        return ""
    out: list[str] = []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "text" and isinstance(block.get("text"), str):
            out.append(block["text"])
    return "\n".join(out).strip()


def _extract_first_user_text(transcript_path: Path) -> str:
    for row in _iter_jsonl(transcript_path):
        message = row.get("message") if isinstance(row.get("message"), dict) else row
        if message.get("role") == "user":
            text = _content_to_text(message.get("content"))
            if text:
                return text
    return ""


def _build_graph(transcript_path: Path) -> dict[str, Any]:
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    last_id: str | None = None
    tool_call_nodes: dict[str, str] = {}
    idx = 0

    def add_node(kind: str, label: str, meta: dict[str, Any]) -> str:
        nonlocal idx, last_id
        nid = f"n{idx}"
        idx += 1
        nodes.append({"id": nid, "kind": kind, "label": label, "meta": meta})
        if last_id is not None:
            edges.append({"src": last_id, "dst": nid, "kind": "sequence"})
        last_id = nid
        return nid

    for raw in _iter_jsonl(transcript_path):
        row = raw.get("message") if isinstance(raw.get("message"), dict) else raw
        role = row.get("role")
        line = raw.get("_line")
        if role == "user":
            add_node("user", _text_snippet(_content_to_text(row.get("content")) or json.dumps(row.get("content"), ensure_ascii=False), 400), {"line": line})
            continue
        if role == "assistant":
            content = row.get("content")
            if isinstance(content, str):
                add_node("assistant", _text_snippet(content, 500), {"line": line})
            elif isinstance(content, list):
                tool_calls = row.get("tool_calls") or []
                visible = _content_to_text(content)
                if visible:
                    add_node("assistant", _text_snippet(visible, 500), {"line": line})
                for tc in tool_calls:
                    if not isinstance(tc, dict):
                        continue
                    tid = str(tc.get("id") or "")
                    fn = tc.get("function") or {}
                    name = str(tc.get("name") or fn.get("name") or "tool")
                    nid = add_node("tool_call", name, {"line": line, "toolCallId": tid})
                    if tid:
                        tool_call_nodes[tid] = nid
            else:
                add_node("assistant", _text_snippet(json.dumps(content, ensure_ascii=False), 300), {"line": line})
            continue
        if role in {"tool", "toolResult"}:
            tcid = str(row.get("tool_call_id") or row.get("toolCallId") or "")
            name = str(row.get("name") or row.get("toolName") or "tool")
            preview = row.get("content")
            if not isinstance(preview, str):
                preview = json.dumps(preview, ensure_ascii=False)
            nid = add_node("tool_result", name, {"line": line, "toolCallId": tcid, "preview": _text_snippet(preview, 300)})
            if tcid and tcid in tool_call_nodes:
                edges.append({"src": tool_call_nodes[tcid], "dst": nid, "kind": "tool_link"})
            continue
        if role:
            add_node(str(role), _text_snippet(json.dumps(row, ensure_ascii=False), 200), {"line": line})

    return {
        "nodes": nodes,
        "edges": edges,
        "stats": {
            "node_count": len(nodes),
            "edge_count": len(edges),
            "tool_calls": sum(1 for n in nodes if n["kind"] == "tool_call"),
            "tool_results": sum(1 for n in nodes if n["kind"] == "tool_result"),
        },
    }


def _build_rubric_context(transcript_path: Path) -> str:
    graph = _build_graph(transcript_path)
    lines = ["=== TASK (first user message) ===", _extract_first_user_text(transcript_path) or "(no user message found)", "", "=== GRAPH ===", json.dumps(graph.get("stats", {}), ensure_ascii=False, indent=2), "", "--- Nodes ---"]
    for n in graph["nodes"]:
        lines.append(f"{n['id']} | {n['kind']} | {n['label']}")
    lines.append("")
    lines.append("--- Edges ---")
    for e in graph["edges"]:
        lines.append(f"{e['src']} -> {e['dst']} | {e['kind']}")
    return "\n".join(lines)


def _load_default_rubric() -> tuple[str, str]:
    mod = _load_module(_project_root() / "grading" / "default_rubric.py", "clawbench_v2_default_rubric")
    if mod is None:
        raise RuntimeError("missing grading/default_rubric.py")
    return str(getattr(mod, "RUBRIC_SYSTEM")), str(getattr(mod, "USER_TEMPLATE"))


def _load_task_rubric(task_dir: Path, task_id: str, payload: str) -> tuple[str, str, str] | None:
    rubric_path = task_dir / "llm_rubric.py"
    if not rubric_path.is_file():
        return None
    default_system, default_template = _load_default_rubric()
    try:
        mod = _load_module(rubric_path, f"clawbench_v2_rubric_{task_id.replace('-', '_')}")
    except Exception:
        mod = None
    if mod is None:
        return None
    system = getattr(mod, "RUBRIC_SYSTEM", default_system)
    template = getattr(mod, "USER_TEMPLATE", default_template)
    if not isinstance(system, str) or not isinstance(template, str):
        return None
    try:
        user = template.format(task_name=task_id, payload=payload)
    except Exception:
        return None
    return system, user, str(rubric_path)


def _resolve_api_key_ref(value: str) -> str | None:
    value = value.strip()
    if value.startswith("${") and value.endswith("}"):
        return os.environ.get(value[2:-1]) or None
    return value or None


def _load_chat_credentials_from_openclaw(path: Path) -> tuple[str | None, str | None, str | None]:
    try:
        cfg = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None, None, None
    defaults = cfg.get("agents", {}).get("defaults") or {}
    if not isinstance(defaults, dict):
        return None, None, None
    model_cfg = defaults.get("model") or {}
    primary_ref = model_cfg.get("primary") if isinstance(model_cfg, dict) else None
    ref = defaults.get("rubricModel") or primary_ref
    if not isinstance(ref, str) or "/" not in ref:
        return None, None, None
    provider_id, model_id = ref.split("/", 1)
    provider = ((cfg.get("models") or {}).get("providers") or {}).get(provider_id) or {}
    if not isinstance(provider, dict):
        return None, None, None
    api_key = _resolve_api_key_ref(str(provider.get("apiKey") or ""))
    base_url = str(provider.get("baseUrl") or "").rstrip("/") or None
    return api_key, base_url, model_id


def _load_chat_credentials_from_nanobot(path: Path) -> tuple[str | None, str | None, str | None]:
    try:
        cfg = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None, None, None
    defaults = ((cfg.get("agents") or {}).get("defaults") or {})
    if not isinstance(defaults, dict):
        return None, None, None
    model_id = str(defaults.get("model") or "").strip() or None
    provider_id = str(defaults.get("provider") or "").strip() or None
    if not model_id:
        return None, None, None
    providers = cfg.get("providers") or {}
    provider = providers.get(provider_id) if isinstance(providers, dict) and provider_id else None
    if not isinstance(provider, dict):
        return None, None, None
    api_key = _resolve_api_key_ref(str(provider.get("apiKey") or ""))
    base_url = str(provider.get("apiBase") or "").rstrip("/") or None
    return api_key, base_url, model_id


def _load_chat_credentials_from_picoclaw(path: Path) -> tuple[str | None, str | None, str | None]:
    try:
        cfg = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None, None, None
    defaults = ((cfg.get("agents") or {}).get("defaults") or {})
    if not isinstance(defaults, dict):
        return None, None, None
    model_name = str(defaults.get("model_name") or "").strip() or None
    if not model_name:
        return None, None, None
    model_list = cfg.get("model_list") or []
    if not isinstance(model_list, list):
        return None, None, None
    selected: dict[str, Any] | None = None
    for item in model_list:
        if not isinstance(item, dict):
            continue
        if str(item.get("model_name") or "").strip() == model_name:
            selected = item
            break
    if selected is None:
        return None, None, None
    api_key = _resolve_api_key_ref(str(selected.get("api_key") or ""))
    base_url = str(selected.get("api_base") or "").rstrip("/") or None
    model_id = str(selected.get("model") or model_name).strip() or None
    return api_key, base_url, model_id


def _load_chat_credentials_from_zeroclaw(path: Path) -> tuple[str | None, str | None, str | None]:
    try:
        cfg = tomllib.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None, None, None

    provider_id = str(cfg.get("default_provider") or "").strip() or None
    model_id = str(cfg.get("model") or cfg.get("default_model") or "").strip() or None
    api_key = _resolve_api_key_ref(str(cfg.get("api_key") or ""))
    base_url = str(cfg.get("base_url") or "").rstrip("/") or None

    providers = cfg.get("providers") or {}
    provider_cfg = providers.get(provider_id) if isinstance(providers, dict) and provider_id else None
    if isinstance(provider_cfg, dict):
        api_key = api_key or _resolve_api_key_ref(str(provider_cfg.get("api_key") or ""))
        base_url = base_url or (str(provider_cfg.get("base_url") or "").rstrip("/") or None)
        model_id = model_id or str(
            provider_cfg.get("model") or provider_cfg.get("default_model") or ""
        ).strip() or None

    return api_key, base_url, model_id

def _load_chat_credentials_from_hermes(path: Path) -> tuple[str | None, str | None, str | None]:
    try:
        import yaml
    except ImportError:
        try:
            import ruamel.yaml as yaml
        except ImportError:
            return None, None, None
    
    try:
        with open(path, 'r', encoding='utf-8') as f:
            cfg = yaml.safe_load(f)
    except Exception:
        return None, None, None
    
    # 从custom_providers中获取配置
    custom_providers = cfg.get('custom_providers') or []
    if isinstance(custom_providers, list) and len(custom_providers) > 0:
        provider = custom_providers[0]  # 取第一个provider
        if isinstance(provider, dict):
            api_key = str(provider.get('api_key') or '')
            base_url = str(provider.get('base_url') or '').rstrip('/') or None
            model = str(provider.get('model') or '')
            return api_key or None, base_url, model or None
    
    # 如果没有custom_providers，尝试从model配置中获取
    model_cfg = cfg.get('model') or {}
    if isinstance(model_cfg, dict):
        model = str(model_cfg.get('default') or '')
        base_url = str(model_cfg.get('base_url') or '').rstrip('/') or None
        # 尝试从providers中获取api_key
        providers = cfg.get('providers') or {}
        if isinstance(providers, dict) and providers:
            # 获取第一个provider的api_key
            for provider in providers.values():
                if isinstance(provider, dict):
                    api_key = str(provider.get('api_key') or '')
                    if api_key:
                        return api_key, base_url, model or None
    
    return None, None, None

def _parse_json_object(text: str) -> dict[str, Any] | None:
    text = text.strip()
    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass
    try:
        start = text.index("{")
        depth = 0
        for i, ch in enumerate(text[start:], start):
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return json.loads(text[start : i + 1])
    except Exception:
        return None
    return None


def _run_llm_rubric(system: str, user: str, openclaw_config: Path | None = None, timeout_sec: int = 120) -> dict[str, Any]:
    api_key = os.environ.get("RUBRIC_API_KEY") or os.environ.get("OPENAI_API_KEY")
    base_url = os.environ.get("RUBRIC_BASE_URL")
    model = os.environ.get("RUBRIC_MODEL")
    if openclaw_config is not None and (not api_key or not base_url or not model):
        loaders = (
            _load_chat_credentials_from_openclaw,
            _load_chat_credentials_from_nanobot,
            _load_chat_credentials_from_picoclaw,
            _load_chat_credentials_from_zeroclaw,
            _load_chat_credentials_from_hermes
        )
        for loader in loaders:
            try:
                ok, ob, om = loader(openclaw_config)
            except Exception:
                continue
            api_key = api_key or ok
            base_url = base_url or ob
            model = model or om
            if api_key and base_url and model:
                break
    if not api_key:
        return {"available": False, "skipped": True, "reason": "missing rubric api key"}
    base = (base_url or "https://api.openai.com/v1").rstrip("/")
    mdl = model or "gpt-4o-mini"
    payload = {
        "model": mdl,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": 0.2,
    }
    req = urllib.request.Request(
        f"{base}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
            raw = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        return {"available": False, "skipped": True, "reason": str(exc)}
    try:
        content = raw["choices"][0]["message"]["content"]
    except Exception:
        return {"available": False, "skipped": True, "reason": "unexpected rubric response shape", "raw": raw}
    parsed = _parse_json_object(content) if isinstance(content, str) else None
    if not parsed:
        return {
            "available": True,
            "skipped": False,
            "parse_error": True,
            "raw_content": str(content)[:2000],
            "scores": {},
            "total": None,
            "notes": "Failed to parse JSON from model output",
        }
    return {
        "available": True,
        "skipped": False,
        "parse_error": False,
        "scores": parsed.get("scores") if isinstance(parsed.get("scores"), dict) else {},
        "total": float(parsed.get("total")) if isinstance(parsed.get("total"), (int, float)) else None,
        "notes": str(parsed.get("notes", "")),
        "raw_content": str(content)[:1500],
        "vision_breakdown": parsed.get("vision_breakdown") if isinstance(parsed.get("vision_breakdown"), dict) else None,
    }


def resolve_transcript_path(adapter_metadata: dict[str, Any], session_id: str) -> Path | None:
    def _search_session(root: Path) -> Path | None:
        candidates = [
            root / "agents" / "main" / "sessions" / f"{session_id}.jsonl",
            root / "agent" / "sessions" / f"{session_id}.jsonl",
            root / "sessions" / f"{session_id}.jsonl",
            root / "workspace" / "sessions" / f"{session_id}.jsonl",
        ]
        for path in candidates:
            if path.is_file():
                return path
        exact = list(root.rglob(f"{session_id}.jsonl"))
        if exact:
            return exact[0]
        all_jsonl = sorted(root.rglob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
        if all_jsonl:
            return all_jsonl[0]
        return None
    
    # 添加Hermes支持
    hermes_home = str(adapter_metadata.get("hermes_home") or "").strip()
    if hermes_home:
        hermes_home_path = Path(hermes_home)
        # 检查是否有state.db
        state_db_path = hermes_home_path / "state.db"
        if state_db_path.is_file():
            # Hermes使用数据库，需要特殊处理
            # 返回state.db路径，但需要修改读取逻辑
            return state_db_path
        
        # 如果找不到state.db，尝试找jsonl文件
        path = _search_session(hermes_home_path)
        if path is not None:
            return path

    openclaw_home = str(adapter_metadata.get("openclaw_home") or "").strip()
    if openclaw_home:
        path = _search_session(Path(openclaw_home))
        if path is not None:
            return path
    nanobot_workspace = str(adapter_metadata.get("nanobot_workspace") or "").strip()
    if nanobot_workspace:
        path = _search_session(Path(nanobot_workspace))
        if path is not None:
            return path
    picoclaw_workspace = str(adapter_metadata.get("picoclaw_workspace") or "").strip()
    if picoclaw_workspace:
        path = _search_session(Path(picoclaw_workspace))
        if path is not None:
            return path
    zeroclaw_home = str(adapter_metadata.get("zeroclaw_home") or "").strip()
    if zeroclaw_home:
        path = _search_session(Path(zeroclaw_home))
        if path is not None:
            return path
    return None


def run_process_rubric(task_dir: Path, task_id: str, adapter_metadata: dict[str, Any], session_id: str, timeout_sec: int = 120) -> dict[str, Any]:
    rubric_path = task_dir / "llm_rubric.py"
    if not rubric_path.is_file():
        return {"available": False, "skipped": True, "reason": "missing llm_rubric.py"}
    transcript_path = resolve_transcript_path(adapter_metadata, session_id)
    if transcript_path is None:
        return {"available": False, "skipped": True, "reason": "missing transcript/session file"}
    payload = _build_rubric_context(transcript_path)
    loaded = _load_task_rubric(task_dir, task_id, payload)
    if loaded is None:
        return {"available": False, "skipped": True, "reason": "failed to load llm_rubric.py", "rubric_file": str(rubric_path)}
    system, user, source = loaded
    openclaw_cfg = None
    cfg_path = str(adapter_metadata.get("source_user_config_path") or "").strip()
    if cfg_path:
        p = Path(cfg_path)
        if p.is_file():
            openclaw_cfg = p
    result = _run_llm_rubric(system, user, openclaw_config=openclaw_cfg, timeout_sec=timeout_sec)
    result["rubric_file"] = source
    result["transcript_file"] = str(transcript_path)
    result["rubric_context_chars"] = len(payload)
    return result
