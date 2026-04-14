from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from clawbench_v2.adapters.base import BaseAdapter
from clawbench_v2.models import AdapterRunContext, AdapterRunResult
from clawbench_v2.usage_proxy import register_routes


def _project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _resolve_project_path(raw: str | Path) -> Path:
    path = Path(os.path.expanduser(str(raw)))
    if not path.is_absolute():
        path = _project_root() / path
    return path.resolve()


def _source_config_from_model_config(model_cfg: dict[str, object]) -> Path:
    raw = model_cfg.get("user_config")
    if raw:
        return _resolve_project_path(str(raw))
    env_raw = os.environ.get("HERMES_CONFIG_PATH") or os.environ.get("HERMES_CONFIG")
    if env_raw:
        return _resolve_project_path(env_raw)
    return _resolve_project_path("~/.hermes/config.yaml")


def _load_yaml(path: Path) -> dict[str, object]:
    try:
        import yaml  # type: ignore
    except ModuleNotFoundError:
        try:
            from ruamel import yaml  # type: ignore
        except ModuleNotFoundError as exc:
            raise RuntimeError("Hermes adapter requires PyYAML or ruamel.yaml to rewrite config") from exc

    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return dict(data or {}) if isinstance(data, dict) else {}


def _dump_yaml(path: Path, data: dict[str, object]) -> None:
    try:
        import yaml  # type: ignore
    except ModuleNotFoundError:
        try:
            from ruamel import yaml  # type: ignore
        except ModuleNotFoundError as exc:
            raise RuntimeError("Hermes adapter requires PyYAML or ruamel.yaml to rewrite config") from exc

    path.write_text(
        yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )


def _safe_name(raw: str, fallback: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in ("-", "_", ".") else "-" for ch in raw).strip("-._")
    return cleaned or fallback


def _register_route(
    routes: dict[str, dict[str, str]],
    *,
    proxy_base_url: str,
    upstream: str,
    route_name: str,
    provider: str,
) -> str:
    prefix = f"/hermes/{route_name}"
    routes[prefix] = {
        "framework": "hermes",
        "provider": provider,
        "upstream": upstream,
    }
    return f"{proxy_base_url}{prefix}"


def _matching_custom_provider(
    custom_providers: object,
    base_url: str,
    model_name: str,
) -> dict[str, object] | None:
    if not isinstance(custom_providers, list) or not base_url:
        return None
    candidates = [
        entry
        for entry in custom_providers
        if isinstance(entry, dict)
        and str(entry.get("base_url") or "").strip().rstrip("/") == base_url
        and str(entry.get("api_key") or "").strip()
    ]
    if not candidates:
        return None
    if model_name:
        for entry in candidates:
            if str(entry.get("model") or "").strip() == model_name:
                return entry
    return candidates[0]


def _merge_user_config(
    user_config: Path,
    out_path: Path,
    *,
    proxy_base_url: str = "",
    proxy_routes_file: Path | None = None,
) -> None:
    data = _load_yaml(user_config)
    routes: dict[str, dict[str, str]] = {}
    custom_providers = data.get("custom_providers")

    def rewrite_url(raw: object, route_name: str, provider: str) -> str | None:
        upstream = str(raw or "").strip().rstrip("/")
        if not upstream:
            return None
        if proxy_base_url and upstream.startswith(proxy_base_url.rstrip("/")):
            return upstream
        if proxy_base_url and proxy_routes_file is not None:
            return _register_route(
                routes,
                proxy_base_url=proxy_base_url,
                upstream=upstream,
                route_name=route_name,
                provider=provider,
            )
        return upstream

    model_cfg = data.get("model")
    if isinstance(model_cfg, dict):
        provider_name = str(model_cfg.get("provider") or "model").strip() or "model"
        original_base_url = str(model_cfg.get("base_url") or "").strip().rstrip("/")
        model_name = str(model_cfg.get("default") or model_cfg.get("model") or "").strip()
        matching_provider = _matching_custom_provider(custom_providers, original_base_url, model_name)
        if proxy_base_url and matching_provider:
            matching_name = str(matching_provider.get("name") or "custom").strip() or "custom"
            rewritten = _register_route(
                routes,
                proxy_base_url=proxy_base_url,
                upstream=original_base_url,
                route_name=f"custom-{_safe_name(matching_name, 'custom')}",
                provider=matching_name,
            )
        else:
            rewritten = rewrite_url(original_base_url, f"model-{_safe_name(provider_name, 'model')}", provider_name)
        if rewritten:
            if proxy_base_url and matching_provider and not str(model_cfg.get("api_mode") or "").strip():
                api_mode = str(matching_provider.get("api_mode") or "").strip()
                if api_mode:
                    model_cfg["api_mode"] = api_mode
            model_cfg["base_url"] = rewritten

    if isinstance(custom_providers, list):
        for index, entry in enumerate(custom_providers):
            if not isinstance(entry, dict):
                continue
            provider_name = str(entry.get("name") or f"custom-{index}").strip() or f"custom-{index}"
            rewritten = rewrite_url(entry.get("base_url"), f"custom-{_safe_name(provider_name, f'custom-{index}')}", provider_name)
            if rewritten:
                entry["base_url"] = rewritten

    auxiliary = data.get("auxiliary")
    if isinstance(auxiliary, dict):
        for key, entry in auxiliary.items():
            if not isinstance(entry, dict):
                continue
            rewritten = rewrite_url(entry.get("base_url"), f"aux-{_safe_name(str(key), 'aux')}", str(key))
            if rewritten:
                entry["base_url"] = rewritten

    if routes and proxy_routes_file is not None:
        register_routes(proxy_routes_file, routes)

    _dump_yaml(out_path, data)


def _build_command(ctx: AdapterRunContext, command: str, args: list[str]) -> list[str]:
    fmt = {
        "workspace": str(ctx.workspace),
        "sandbox": str(ctx.sandbox),
        "prompt_file": str(ctx.prompt_file),
        "session_id": ctx.session_id,
        "task_id": ctx.task.task_id,
        "model_id": ctx.model_id,
    }
    cmd = [command, *[str(arg).format(**fmt) for arg in args]]

    if "-q" in cmd or "--query" in cmd:
        for index, arg in enumerate(cmd):
            if arg in {"-q", "--query"}:
                cmd.insert(index + 1, ctx.prompt)
                break
        return cmd

    cmd.extend(["-q", ctx.prompt])
    return cmd


class HermesAgentAdapter(BaseAdapter):
    name = "hermes_agent"

    def run(self, ctx: AdapterRunContext) -> AdapterRunResult:
        command = str(ctx.model_config.get("command") or "hermes")
        args = [str(arg) for arg in (ctx.model_config.get("args") or ["chat"])]
        use_usage_proxy = bool(ctx.model_config.get("use_usage_proxy", False))

        source_config = _source_config_from_model_config(ctx.model_config)
        if not source_config.is_file():
            return AdapterRunResult(ok=False, stderr=f"missing Hermes source config: {source_config}")

        isolated_home = ctx.sandbox
        hermes_home = isolated_home / ".hermes"
        hermes_home.mkdir(parents=True, exist_ok=True)
        hermes_workspace = hermes_home / "workspace"
        hermes_workspace.mkdir(parents=True, exist_ok=True)

        sandbox_user_config = hermes_home / "config.src.yaml"
        shutil.copy2(source_config, sandbox_user_config)
        merged_cfg = hermes_home / "config.yaml"
        _merge_user_config(
            sandbox_user_config,
            merged_cfg,
            proxy_base_url=str(ctx.env.get("CLAWBENCH_LLM_PROXY_URL") or "") if use_usage_proxy else "",
            proxy_routes_file=(
                Path(ctx.env["CLAWBENCH_LLM_PROXY_ROUTES"])
                if use_usage_proxy and ctx.env.get("CLAWBENCH_LLM_PROXY_ROUTES")
                else None
            ),
        )

        cmd = _build_command(ctx, command, args)

        env = os.environ.copy()
        env["HOME"] = str(isolated_home)
        env["HERMES_HOME"] = str(hermes_home)
        env["HERMES_CONFIG"] = str(merged_cfg)
        env["HERMES_CONFIG_PATH"] = str(merged_cfg)

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
            metadata={
                "returncode": completed.returncode,
                "source_user_config_path": str(source_config),
                "sandbox_user_config_path": str(sandbox_user_config),
                "hermes_config_path": str(merged_cfg),
                "isolated_home": str(isolated_home),
                "hermes_home": str(hermes_home),
                "hermes_workspace": str(hermes_workspace),
                "workspace": str(ctx.workspace),
                "usage_proxy_enabled": use_usage_proxy,
            },
        )
