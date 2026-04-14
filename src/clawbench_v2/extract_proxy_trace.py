"""从 usage-proxy 目录抽取过程分素材：去掉 system，保留 user/assistant 与 tool_calls。Token 以 requests.jsonl 为准。"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


def _normalize_content(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if isinstance(part, dict):
                if part.get("type") == "text" and "text" in part:
                    parts.append(str(part["text"]))
                elif "text" in part:
                    parts.append(str(part["text"]))
            elif isinstance(part, str):
                parts.append(part)
        return "\n".join(parts)
    return str(content)


def extract_request_messages_no_system(request_body: str) -> list[dict[str, str]]:
    """解析 chat completions body，丢弃 system，仅保留 user/assistant（及 tool 若存在可跳过，通常在上游消息里）。"""
    try:
        data = json.loads(request_body)
    except json.JSONDecodeError:
        return []
    messages = data.get("messages")
    if not isinstance(messages, list):
        return []
    out: list[dict[str, str]] = []
    for m in messages:
        if not isinstance(m, dict):
            continue
        role = str(m.get("role", "")).strip()
        if role == "system":
            continue
        if role not in ("user", "assistant", "tool"):
            continue
        text = _normalize_content(m.get("content"))
        item: dict[str, str] = {"role": role, "content": text}
        if role == "tool" and m.get("tool_call_id"):
            item["tool_call_id"] = str(m.get("tool_call_id", ""))
        out.append(item)
    return out


def parse_sse_response(response_text: str) -> tuple[str, list[dict[str, Any]]]:
    """
    解析 OpenAI 兼容的 SSE：拼接 assistant 文本，合并 tool_calls（按 index）。
    返回 (assistant_text, tool_calls)，其中 tool_calls 每项含 name、arguments（解析为 object 失败则为原字符串）。
    """
    buffers: dict[int, dict[str, Any]] = {}
    assistant_parts: list[str] = []

    for line in response_text.splitlines():
        if not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if not payload or payload == "[DONE]":
            continue
        try:
            event = json.loads(payload)
        except json.JSONDecodeError:
            continue
        choices = event.get("choices")
        if not isinstance(choices, list) or not choices:
            continue
        ch0 = choices[0] if isinstance(choices[0], dict) else {}
        delta = ch0.get("delta")
        if not isinstance(delta, dict):
            continue
        c = delta.get("content")
        if isinstance(c, str) and c:
            assistant_parts.append(c)

        raw_tcs = delta.get("tool_calls")
        if not isinstance(raw_tcs, list):
            continue
        for tc in raw_tcs:
            if not isinstance(tc, dict):
                continue
            idx = int(tc.get("index", 0))
            buf = buffers.setdefault(idx, {"name": "", "arguments": ""})
            fn = tc.get("function")
            if isinstance(fn, dict):
                if fn.get("name"):
                    buf["name"] = str(fn["name"])
                if fn.get("arguments"):
                    buf["arguments"] = str(buf["arguments"]) + str(fn["arguments"])

    merged_text = "".join(assistant_parts)

    tool_calls: list[dict[str, Any]] = []
    for idx in sorted(buffers.keys()):
        b = buffers[idx]
        name = str(b.get("name", "") or "").strip()
        args_raw = str(b.get("arguments", "") or "").strip()
        args_parsed: Any = args_raw
        if args_raw:
            try:
                args_parsed = json.loads(args_raw)
            except json.JSONDecodeError:
                pass
        if name or args_raw:
            tool_calls.append({"name": name, "arguments": args_parsed})

    return merged_text, tool_calls


def _parse_non_stream_response(response_json: dict[str, Any]) -> tuple[str, list[dict[str, Any]]]:
    choices = response_json.get("choices")
    if not isinstance(choices, list) or not choices:
        return "", []
    ch0 = choices[0] if isinstance(choices[0], dict) else {}
    msg = ch0.get("message")
    if not isinstance(msg, dict):
        return "", []
    content = _normalize_content(msg.get("content"))
    tool_calls: list[dict[str, Any]] = []
    for tc in msg.get("tool_calls") or []:
        if not isinstance(tc, dict):
            continue
        fn = tc.get("function")
        if not isinstance(fn, dict):
            continue
        name = str(fn.get("name", "") or "")
        args_raw = str(fn.get("arguments", "") or "")
        args_parsed: Any = args_raw
        if args_raw:
            try:
                args_parsed = json.loads(args_raw)
            except json.JSONDecodeError:
                pass
        tool_calls.append({"name": name, "arguments": args_parsed})
    return content, tool_calls


def parse_response_record(raw: dict[str, Any]) -> tuple[str, list[dict[str, Any]]]:
    """单条 proxy 落盘记录：优先 SSE，否则用 choices[0].message。"""
    rt = raw.get("response_text")
    if isinstance(rt, str) and "data:" in rt:
        text, tools = parse_sse_response(rt)
        if text.strip() or tools:
            return text, tools
    rj = raw.get("response_json")
    if isinstance(rj, dict) and rj.get("choices"):
        return _parse_non_stream_response(rj)
    if isinstance(rt, str) and rt.strip().startswith("{"):
        try:
            one = json.loads(rt)
            if isinstance(one, dict) and one.get("choices"):
                return _parse_non_stream_response(one)
        except json.JSONDecodeError:
            pass
    return "", []


def _load_requests_jsonl_index(log_path: Path) -> dict[str, dict[str, Any]]:
    """basename(raw_response_file) -> 该行 JSON（用量等）。"""
    index: dict[str, dict[str, Any]] = {}
    if not log_path.is_file():
        return index
    for line in log_path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        raw_f = row.get("raw_response_file")
        if isinstance(raw_f, str):
            index[Path(raw_f).name] = row
    return index


def _sum_session_tokens_from_jsonl(log_path: Path) -> dict[str, int]:
    """整次会话：对 requests.jsonl 全部行累加 token。"""
    totals = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "llm_rounds": 0}
    if not log_path.is_file():
        return totals
    for line in log_path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        totals["llm_rounds"] += 1
        totals["input_tokens"] += int(row.get("input_tokens", 0) or 0)
        totals["output_tokens"] += int(row.get("output_tokens", 0) or 0)
        totals["total_tokens"] += int(row.get("total_tokens", 0) or 0)
    return totals


def _last_user_content(messages: list[dict[str, str]]) -> str:
    for m in reversed(messages):
        if m.get("role") == "user":
            return str(m.get("content", ""))
    return ""


def extract_round_from_response_file(path: Path, usage_row: dict[str, Any] | None = None) -> dict[str, Any]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    request_body = raw.get("request_body")
    if not isinstance(request_body, str):
        request_body = ""
    messages = extract_request_messages_no_system(request_body)
    assistant_text, tool_calls = parse_response_record(raw)

    out: dict[str, Any] = {
        "response_file": path.name,
        "task_id": raw.get("task_id", ""),
        "session_id": raw.get("session_id", ""),
        "model_id": raw.get("model_id", ""),
        "framework": raw.get("framework", ""),
        "provider": raw.get("provider", ""),
        "request_messages": messages,
        "last_user_content": _last_user_content(messages),
        "assistant_text": assistant_text,
        "tool_calls": tool_calls,
    }
    if usage_row:
        out["usage"] = {
            "input_tokens": usage_row.get("input_tokens", 0),
            "output_tokens": usage_row.get("output_tokens", 0),
            "cache_read_tokens": usage_row.get("cache_read_tokens", 0),
            "cache_write_tokens": usage_row.get("cache_write_tokens", 0),
            "total_tokens": usage_row.get("total_tokens", 0),
            "response_model": usage_row.get("response_model", ""),
        }
    return out


def extract_proxy_trace(proxy_dir: Path, *, all_rounds: bool = False) -> dict[str, Any]:
    """
    读取 ``usage-proxy`` 目录：``responses/*.json`` + 可选 ``requests.jsonl``。
    默认只抽取 **按文件名排序后的最后一个** ``responses/*.json``（含完整累计 ``request_messages``）；
    传 ``all_rounds=True`` 时与旧行为一致，逐文件一轮一条。
    ``totals`` 始终按 **整份** ``requests.jsonl`` 汇总会话级 token（与抽取几条 response 无关）。
    """
    proxy_dir = proxy_dir.resolve()
    responses_dir = proxy_dir / "responses"
    log_path = proxy_dir / "requests.jsonl"
    usage_by_file = _load_requests_jsonl_index(log_path)
    session_totals = _sum_session_tokens_from_jsonl(log_path)

    if not responses_dir.is_dir():
        return {"proxy_dir": str(proxy_dir), "rounds": [], "totals": {}, "error": "missing responses/"}

    files = sorted(responses_dir.glob("*.json"), key=lambda p: p.name)
    if not files:
        return {"proxy_dir": str(proxy_dir), "rounds": [], "totals": {}, "error": "empty responses/"}

    to_read = files if all_rounds else [files[-1]]
    rounds: list[dict[str, Any]] = []
    for fp in to_read:
        usage_row = usage_by_file.get(fp.name)
        rounds.append(extract_round_from_response_file(fp, usage_row))

    out: dict[str, Any] = {
        "proxy_dir": str(proxy_dir),
        "extract_mode": "all_rounds" if all_rounds else "last_response_only",
        "rounds": rounds,
        "totals": {
            "llm_rounds": session_totals["llm_rounds"],
            "input_tokens": session_totals["input_tokens"],
            "output_tokens": session_totals["output_tokens"],
            "total_tokens": session_totals["total_tokens"],
        },
    }
    if not all_rounds:
        out["source_response_file"] = files[-1].name
    return out


def main() -> int:
    import argparse

    p = argparse.ArgumentParser(description="从 usage-proxy 抽取 user/assistant/tool（无 system），token 来自 jsonl")
    p.add_argument(
        "proxy_dir",
        type=Path,
        help="usage-proxy 目录，或包含 usage-proxy 的 sandbox 目录",
    )
    p.add_argument(
        "--all-rounds",
        action="store_true",
        help="抽取全部 responses/*.json（默认只抽最后一个，含完整上下文）",
    )
    args = p.parse_args()
    root = args.proxy_dir.resolve()
    proxy = root / "usage-proxy" if (root / "usage-proxy").is_dir() else root
    trace = extract_proxy_trace(proxy, all_rounds=args.all_rounds)
    print(json.dumps(trace, ensure_ascii=False, indent=2))
    return 0 if "error" not in trace else 1


if __name__ == "__main__":
    raise SystemExit(main())
