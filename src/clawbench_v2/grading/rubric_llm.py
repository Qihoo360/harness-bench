"""OpenAI-compatible Chat Completions for rubric. Env: RUBRIC_API_KEY, RUBRIC_BASE_URL, RUBRIC_MODEL; optional OPENCLAW_USER_CONFIG."""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


def _resolve_api_key_ref(value: str) -> str | None:
    v = value.strip()
    if v.startswith("${") and v.endswith("}"):
        return os.environ.get(v[2:-1]) or None
    return v or None


def load_openclaw_chat_credentials(path: Path) -> tuple[str | None, str | None, str | None]:
    try:
        raw = path.read_text(encoding="utf-8")
        cfg = json.loads(raw)
    except (OSError, json.JSONDecodeError):
        return None, None, None

    defaults = cfg.get("agents", {}).get("defaults")
    if not isinstance(defaults, dict):
        defaults = {}

    rubric_ref = defaults.get("rubricModel")
    primary = (
        defaults.get("model", {}).get("primary")
        if isinstance(defaults.get("model"), dict)
        else None
    )
    ref = rubric_ref if isinstance(rubric_ref, str) and "/" in rubric_ref else primary
    if not isinstance(ref, str) or "/" not in ref:
        return None, None, None

    prov_id, model_id = ref.split("/", 1)
    providers = cfg.get("models", {}).get("providers") or {}
    prov = providers.get(prov_id)
    if not isinstance(prov, dict):
        return None, None, None

    bu = prov.get("baseUrl")
    base = bu.rstrip("/") if isinstance(bu, str) else None
    ak = prov.get("apiKey")
    key = _resolve_api_key_ref(ak) if isinstance(ak, str) else None
    return key, base, model_id


def _default_openclaw_config_path() -> Path | None:
    p = os.environ.get("OPENCLAW_USER_CONFIG", "").strip()
    if p:
        pp = Path(p).expanduser()
        if pp.is_file():
            return pp
    home = Path(os.environ.get("HOME", str(Path.home())))
    oc = home / ".openclaw" / "openclaw.json"
    return oc if oc.is_file() else None


def _parse_json_object(text: str) -> dict[str, Any] | None:
    text = text.strip()
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
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
    except (ValueError, json.JSONDecodeError):
        pass
    return None


def run_llm_rubric(
    *,
    system: str,
    user: str,
    api_key: str | None = None,
    base_url: str | None = None,
    model: str | None = None,
    openclaw_config: Path | None = None,
    timeout_sec: int = 120,
) -> dict[str, Any]:
    ok: str | None = None
    ob: str | None = None
    om: str | None = None
    cfg_path = openclaw_config or _default_openclaw_config_path()
    if cfg_path is not None and cfg_path.is_file():
        ok, ob, om = load_openclaw_chat_credentials(cfg_path)

    key = (
        api_key
        or os.environ.get("RUBRIC_API_KEY")
        or os.environ.get("OPENAI_API_KEY")
        or ok
    )
    if not key:
        return {
            "skipped": True,
            "reason": "No RUBRIC_API_KEY, OPENAI_API_KEY, or openclaw.json apiKey",
            "scores": {},
            "total": None,
            "notes": "",
        }

    base = (
        base_url
        or os.environ.get("RUBRIC_BASE_URL")
        or ob
        or "https://api.openai.com/v1"
    ).rstrip("/")
    mdl = model or os.environ.get("RUBRIC_MODEL") or om or "gpt-4o-mini"

    payload = {
        "model": mdl,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": 0.2,
    }
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{base}/chat/completions",
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {key}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
        return {
            "skipped": True,
            "reason": str(e),
            "scores": {},
            "total": None,
            "notes": "",
        }

    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        return {
            "skipped": True,
            "reason": "unexpected API response shape",
            "raw": data,
            "scores": {},
            "total": None,
            "notes": "",
        }

    parsed = _parse_json_object(content) if isinstance(content, str) else None
    if not parsed:
        return {
            "skipped": False,
            "parse_error": True,
            "raw_content": content[:2000] if isinstance(content, str) else "",
            "scores": {},
            "total": None,
            "notes": "Failed to parse JSON from model output",
        }

    scores = parsed.get("scores") if isinstance(parsed.get("scores"), dict) else {}
    total = parsed.get("total")
    notes = str(parsed.get("notes", ""))
    out: dict[str, object] = {
        "skipped": False,
        "parse_error": False,
        "scores": scores,
        "total": float(total) if total is not None else None,
        "notes": notes,
        "raw_content": content[:1500] if isinstance(content, str) else "",
    }
    vb = parsed.get("vision_breakdown")
    if isinstance(vb, dict):
        out["vision_breakdown"] = vb
    return out
