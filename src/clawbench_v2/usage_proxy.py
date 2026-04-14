from __future__ import annotations

import gzip
import json
import threading
import zlib
from contextlib import AbstractContextManager
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def register_routes(routes_file: Path, routes: dict[str, dict[str, Any]]) -> None:
    existing = _read_json(routes_file)
    existing.update(routes)
    routes_file.parent.mkdir(parents=True, exist_ok=True)
    routes_file.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")


def _normalize_usage(payload: dict[str, Any]) -> dict[str, int]:
    usage = payload.get("usage") or {}
    prompt_details = usage.get("prompt_tokens_details") or {}
    input_details = usage.get("input_token_details") or {}
    cache_read_tokens = int(
        usage.get("cache_read_input_tokens")
        or usage.get("cacheRead")
        or prompt_details.get("cached_tokens")
        or input_details.get("cache_read")
        or input_details.get("cached_tokens")
        or 0
    )
    raw_input_tokens = int(
        usage.get("prompt_tokens")
        or usage.get("input_tokens")
        or usage.get("input")
        or 0
    )
    output_tokens = int(
        usage.get("completion_tokens")
        or usage.get("output_tokens")
        or usage.get("output")
        or 0
    )
    cache_write_tokens = int(
        usage.get("cache_creation_input_tokens")
        or usage.get("cacheWrite")
        or input_details.get("cache_creation")
        or input_details.get("cache_write")
        or 0
    )
    input_tokens = raw_input_tokens - cache_read_tokens
    if input_tokens < 0:
        input_tokens = raw_input_tokens
    total_tokens = int(
        usage.get("total_tokens")
        or usage.get("totalTokens")
        or (raw_input_tokens + output_tokens)
        or 0
    )
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_read_tokens": cache_read_tokens,
        "cache_write_tokens": cache_write_tokens,
        "total_tokens": total_tokens,
    }


def _decode_response_body(raw: bytes, response_headers: list[tuple[str, str]]) -> str:
    headers = {k.lower(): v for k, v in response_headers}
    encoding = headers.get("content-encoding", "").lower()
    body = raw
    try:
        if "gzip" in encoding:
            body = gzip.decompress(raw)
        elif "deflate" in encoding:
            body = zlib.decompress(raw)
        elif "br" in encoding:
            import brotli  # type: ignore

            body = brotli.decompress(raw)
    except Exception:
        body = raw
    charset = "utf-8"
    content_type = headers.get("content-type", "")
    if "charset=" in content_type:
        charset = content_type.split("charset=", 1)[1].split(";", 1)[0].strip() or "utf-8"
    return body.decode(charset, errors="replace")


def _extract_payload(decoded_text: str, response_headers: list[tuple[str, str]]) -> tuple[dict[str, Any] | None, str | None]:
    try:
        payload = json.loads(decoded_text)
        return payload if isinstance(payload, dict) else None, None
    except Exception as exc:
        parse_error = str(exc)

    headers = {k.lower(): v for k, v in response_headers}
    content_type = headers.get("content-type", "").lower()
    if "text/event-stream" not in content_type:
        return None, parse_error

    last_obj: dict[str, Any] | None = None
    for line in decoded_text.splitlines():
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if not data or data == "[DONE]":
            continue
        try:
            event = json.loads(data)
        except Exception:
            continue
        if not isinstance(event, dict):
            continue
        if isinstance(event.get("usage"), dict):
            last_obj = event
    return last_obj, parse_error


def _lookup_route(routes_file: Path, path: str) -> tuple[str, dict[str, Any], str] | None:
    routes = _read_json(routes_file)
    best_prefix = ""
    best_meta: dict[str, Any] | None = None
    for prefix, meta in routes.items():
        if path == prefix or path.startswith(prefix + "/"):
            if len(prefix) > len(best_prefix):
                best_prefix = prefix
                best_meta = dict(meta or {})
    if not best_meta:
        return None
    remainder = path[len(best_prefix):] or "/"
    return best_prefix, best_meta, remainder


@dataclass
class UsageProxyConfig:
    routes_file: Path
    log_file: Path
    raw_dir: Path
    task_id: str
    session_id: str
    model_id: str


class _UsageProxyHandler(BaseHTTPRequestHandler):
    server: "_UsageProxyServer"

    def log_message(self, format: str, *args: Any) -> None:
        return

    def do_POST(self) -> None:
        self._forward()

    def do_GET(self) -> None:
        self._forward()

    def handle(self) -> None:
        try:
            super().handle()
        except (BrokenPipeError, ConnectionResetError):
            # Benchmark clients sometimes cancel/close the HTTP stream after the
            # upstream response is already available. Treat that as a benign
            # disconnect instead of printing a full server traceback.
            return

    def _forward(self) -> None:
        route = _lookup_route(self.server.config.routes_file, urlsplit(self.path).path)
        if route is None:
            self.send_error(502, "no upstream route registered")
            return

        _, route_meta, remainder = route
        upstream_base = str(route_meta.get("upstream") or "").rstrip("/")
        if not upstream_base:
            self.send_error(502, "route upstream missing")
            return

        suffix = remainder
        if "?" in self.path:
            suffix = f"{remainder}?{self.path.split('?', 1)[1]}"
        target = f"{upstream_base}{suffix}"

        body = b""
        content_length = self.headers.get("Content-Length")
        if content_length:
            body = self.rfile.read(int(content_length))

        headers = {
            k: v
            for k, v in self.headers.items()
            if k.lower() not in {"host", "accept-encoding"}
        }
        req = Request(target, data=body if self.command in {"POST", "PUT", "PATCH"} else None, headers=headers, method=self.command)

        response_body = b""
        status = 500
        response_headers: list[tuple[str, str]] = []
        try:
            with urlopen(req, timeout=300) as resp:
                status = resp.status
                response_body = resp.read()
                response_headers = list(resp.headers.items())
        except HTTPError as exc:
            status = exc.code
            response_body = exc.read()
            response_headers = list(exc.headers.items())
        except URLError as exc:
            self.send_error(502, f"proxy upstream error: {exc.reason}")
            return

        try:
            self.send_response(status)
            for key, value in response_headers:
                lower = key.lower()
                if lower in {"content-length", "transfer-encoding", "connection", "content-encoding"}:
                    continue
                self.send_header(key, value)
            self.send_header("Content-Length", str(len(response_body)))
            self.end_headers()
            self.wfile.write(response_body)
        except (BrokenPipeError, ConnectionResetError):
            return

        log_entry = {
            "task_id": self.server.config.task_id,
            "session_id": self.server.config.session_id,
            "model_id": self.server.config.model_id,
            "framework": route_meta.get("framework", ""),
            "provider": route_meta.get("provider", ""),
            "upstream": upstream_base,
            "path": remainder,
            "method": self.command,
            "status": status,
        }
        decoded_text = _decode_response_body(response_body, response_headers)
        payload, parse_error = _extract_payload(decoded_text, response_headers)
        content_type = next((value for key, value in response_headers if key.lower() == "content-type"), "")
        if content_type:
            log_entry["content_type"] = content_type
        raw_record = {
            "task_id": self.server.config.task_id,
            "session_id": self.server.config.session_id,
            "model_id": self.server.config.model_id,
            "framework": route_meta.get("framework", ""),
            "provider": route_meta.get("provider", ""),
            "upstream": upstream_base,
            "path": remainder,
            "method": self.command,
            "status": status,
            "request_headers": dict(self.headers.items()),
            "request_body": body.decode("utf-8", errors="replace"),
            "response_headers": dict(response_headers),
            "response_text": decoded_text,
        }
        if isinstance(payload, dict):
            log_entry["response_model"] = payload.get("model", "")
            log_entry.update(_normalize_usage(payload))
            raw_record["response_json"] = payload
        else:
            if parse_error:
                log_entry["parse_error"] = parse_error
                raw_record["parse_error"] = parse_error
            preview = decoded_text[:200].strip()
            if preview:
                log_entry["response_preview"] = preview
            log_entry.update(
                {
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "cache_read_tokens": 0,
                    "cache_write_tokens": 0,
                    "total_tokens": 0,
                }
            )
        raw_path = self.server.next_raw_path()
        self.server.config.raw_dir.mkdir(parents=True, exist_ok=True)
        raw_path.write_text(json.dumps(raw_record, ensure_ascii=False, indent=2), encoding="utf-8")
        log_entry["raw_response_file"] = str(raw_path)
        self.server.config.log_file.parent.mkdir(parents=True, exist_ok=True)
        with self.server.config.log_file.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(log_entry, ensure_ascii=False) + "\n")


class _UsageProxyServer(ThreadingHTTPServer):
    def __init__(self, server_address: tuple[str, int], config: UsageProxyConfig):
        super().__init__(server_address, _UsageProxyHandler)
        self.config = config
        self._seq = 0
        self._seq_lock = threading.Lock()

    def next_raw_path(self) -> Path:
        with self._seq_lock:
            self._seq += 1
            seq = self._seq
        return self.config.raw_dir / f"{seq:04d}.json"


class UsageProxy(AbstractContextManager["UsageProxy"]):
    def __init__(self, routes_file: Path, log_file: Path, raw_dir: Path, task_id: str, session_id: str, model_id: str):
        self.routes_file = routes_file
        self.log_file = log_file
        self.server = _UsageProxyServer(
            ("127.0.0.1", 0),
            UsageProxyConfig(
                routes_file=routes_file,
                log_file=log_file,
                raw_dir=raw_dir,
                task_id=task_id,
                session_id=session_id,
                model_id=model_id,
            ),
        )
        self._thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def base_url(self) -> str:
        host, port = self.server.server_address
        return f"http://{host}:{port}"

    def __enter__(self) -> "UsageProxy":
        self._thread.start()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.server.shutdown()
        self.server.server_close()
        self._thread.join(timeout=5)
        return None
