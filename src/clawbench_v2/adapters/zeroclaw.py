from __future__ import annotations

import os
import re
import shutil
import subprocess
import tomllib
from pathlib import Path

from clawbench_v2.adapters.base import BaseAdapter
from clawbench_v2.models import AdapterRunContext, AdapterRunResult
from clawbench_v2.usage_proxy import register_routes


def _project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _resolve_project_path(raw: str | Path) -> Path:
    p = Path(os.path.expanduser(str(raw)))
    if not p.is_absolute():
        p = _project_root() / p
    return p.resolve()


def _copy_tree_contents(src: Path, dst: Path) -> None:
    if not src.is_dir():
        return
    for child in src.iterdir():
        target = dst / child.name
        if child.is_dir():
            shutil.copytree(child, target, dirs_exist_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(child, target)


def _copy_if_exists(src: Path, dst: Path) -> None:
    if not src.exists():
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    if src.is_dir():
        shutil.copytree(src, dst, dirs_exist_ok=True)
    else:
        shutil.copy2(src, dst)


def _render_toml_string_array(values: list[str]) -> str:
    rendered_lines = ["["]
    rendered_lines.extend(f'    "{item}",' for item in values)
    rendered_lines.append("]")
    return "\n".join(rendered_lines)


def _upsert_autonomy_value(block: str, key: str, rendered_value: str) -> str:
    pattern = re.compile(rf"(?ms)^\s*{re.escape(key)}\s*=\s*(?:\[(?:.*?\n)*?\s*\]|[^\n]+)")
    replacement = f"{key} = {rendered_value}"
    if pattern.search(block):
        return pattern.sub(replacement, block, count=1)
    return block.rstrip() + f"\n{replacement}\n"


def _upsert_top_level_string(raw: str, key: str, value: str) -> str:
    pattern = re.compile(rf'(?m)^{re.escape(key)}\s*=\s*"[^"\n]*"')
    replacement = f'{key} = "{value}"'
    if pattern.search(raw):
        return pattern.sub(replacement, raw, count=1)
    return replacement + "\n" + raw


def _merge_user_config(
    user_config: Path,
    out_path: Path,
    *,
    workspace: Path,
    sandbox: Path,
    zeroclaw_home: Path,
    source_home: Path | None,
    proxy_base_url: str = "",
    proxy_routes_file: Path | None = None,
) -> None:
    raw = user_config.read_text(encoding="utf-8")
    data = tomllib.loads(raw)
    text = raw

    default_provider = str(data.get("default_provider") or "").strip()
    if proxy_base_url and proxy_routes_file is not None:
        provider_prefix = ""
        upstream = ""
        if default_provider.startswith("custom:"):
            provider_prefix = "custom"
            upstream = default_provider[len("custom:"):].strip()
        elif default_provider.startswith("anthropic-custom:"):
            provider_prefix = "anthropic-custom"
            upstream = default_provider[len("anthropic-custom:"):].strip()

        if upstream:
            route_prefix = "/zeroclaw/default"
            register_routes(
                proxy_routes_file,
                {
                    route_prefix: {
                        "framework": "zeroclaw",
                        "provider": "default",
                        "upstream": upstream,
                    }
                },
            )
            text = _upsert_top_level_string(
                text,
                "default_provider",
                f"{provider_prefix}:{proxy_base_url}{route_prefix}",
            )

    autonomy = data.setdefault("autonomy", {})
    auto_approve = autonomy.get("auto_approve")
    entries = list(auto_approve) if isinstance(auto_approve, list) else []
    values = [str(item) for item in entries]
    for tool_name in ("shell", "file_write", "file_edit"):
        if tool_name not in values:
            values.append(tool_name)
    auto_approve_value = _render_toml_string_array(values)

    allowed_roots = autonomy.get("allowed_roots")
    root_entries = list(allowed_roots) if isinstance(allowed_roots, list) else []
    root_values = [str(item) for item in root_entries]
    dynamic_roots = [
        str(path.resolve())
        for path in (workspace, sandbox, zeroclaw_home)
    ]
    if source_home is not None:
        dynamic_roots.append(str(source_home.resolve()))
    for root in dynamic_roots:
        if root not in root_values:
            root_values.append(root)
    allowed_roots_value = _render_toml_string_array(root_values)

    autonomy_section = re.search(r"(?ms)^\[autonomy\]\n(.*?)(?=^\[|\Z)", text)
    if autonomy_section:
        block = autonomy_section.group(0)
        updated_block = _upsert_autonomy_value(block, "workspace_only", "false")
        updated_block = _upsert_autonomy_value(updated_block, "allowed_roots", allowed_roots_value)
        updated_block = _upsert_autonomy_value(updated_block, "auto_approve", auto_approve_value)
        text = text[: autonomy_section.start()] + updated_block + text[autonomy_section.end() :]
    else:
        text = (
            text.rstrip()
            + "\n\n[autonomy]\n"
            + "workspace_only = false\n"
            + f"allowed_roots = {allowed_roots_value}\n"
            + f"auto_approve = {auto_approve_value}\n"
        )
    out_path.write_text(text, encoding="utf-8")


class ZeroClawAdapter(BaseAdapter):
    name = "zeroclaw"

    def run(self, ctx: AdapterRunContext) -> AdapterRunResult:
        command = str(ctx.model_config.get("command") or "zeroclaw")
        user_config_raw = str(ctx.model_config.get("user_config") or "config/zeroclaw.toml")
        user_config = _resolve_project_path(user_config_raw)
        if not user_config.is_file():
            return AdapterRunResult(ok=False, stderr=f"missing ZeroClaw source config: {user_config}")

        isolated_home = ctx.sandbox
        zeroclaw_home = isolated_home / ".zeroclaw"
        zeroclaw_home.mkdir(parents=True, exist_ok=True)

        source_home_raw = str(
            ctx.model_config.get("source_home")
            or ctx.env.get("ZEROCLAW_SOURCE_HOME")
            or ""
        ).strip()
        if not source_home_raw:
            source_home_raw = str(Path.home() / ".zeroclaw")
        source_home: Path | None = None
        if source_home_raw:
            source_home = _resolve_project_path(source_home_raw)
            if source_home.is_dir():
                _copy_tree_contents(source_home, zeroclaw_home)

        # Encrypted `enc2:` secrets are keyed by `<config_dir>/.secret_key`.
        # When we run inside an isolated sandbox, we need to bring over the
        # source key (and minimal state) into the sandbox config dir.
        config_source_dir = user_config.parent
        key_candidates: list[Path] = [config_source_dir / ".secret_key"]
        if source_home is not None:
            key_candidates.append(source_home / ".secret_key")
        for key_path in key_candidates:
            if key_path.is_file():
                _copy_if_exists(key_path, zeroclaw_home / ".secret_key")
                break

        zeroclaw_workspace = zeroclaw_home / "workspace"
        zeroclaw_workspace.mkdir(parents=True, exist_ok=True)

        sandbox_user_config = zeroclaw_home / "config.src.toml"
        shutil.copy2(user_config, sandbox_user_config)
        merged_cfg = zeroclaw_home / "config.toml"
        _merge_user_config(
            sandbox_user_config,
            merged_cfg,
            workspace=ctx.workspace,
            sandbox=ctx.sandbox,
            zeroclaw_home=zeroclaw_home,
            source_home=source_home,
            proxy_base_url=str(ctx.env.get("CLAWBENCH_LLM_PROXY_URL") or ""),
            proxy_routes_file=Path(ctx.env["CLAWBENCH_LLM_PROXY_ROUTES"]) if ctx.env.get("CLAWBENCH_LLM_PROXY_ROUTES") else None,
        )

        session_state_dir = zeroclaw_home / "sessions"
        session_state_dir.mkdir(parents=True, exist_ok=True)
        session_state_file = session_state_dir / f"{ctx.session_id}.json"

        cmd = [
            command,
            "--config-dir",
            str(zeroclaw_home),
            "agent",
            "--session-state-file",
            str(session_state_file),
            "-m",
            ctx.prompt,
        ]
        provider = str(ctx.model_config.get("provider") or "").strip()
        if provider:
            cmd.extend(["--provider", provider])
        extra_args = [str(x) for x in (ctx.model_config.get("extra_args") or [])]
        if extra_args:
            cmd.extend(extra_args)

        env = os.environ.copy()
        env.update(ctx.env)
        env["HOME"] = str(isolated_home)
        env["WORKSPACE"] = str(ctx.workspace)
        env["ZEROCLAW_CONFIG_DIR"] = str(zeroclaw_home)
        env["ZEROCLAW_WORKSPACE"] = str(zeroclaw_workspace)
        env["ZEROCLAW_HOME"] = str(zeroclaw_home)
        env["ZEROCLAW_SESSION_ID"] = ctx.session_id

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
                "zeroclaw_config_path": str(merged_cfg),
                "source_user_config_path": str(user_config),
                "sandbox_user_config_path": str(sandbox_user_config),
                "isolated_home": str(isolated_home),
                "zeroclaw_home": str(zeroclaw_home),
                "zeroclaw_workspace": str(zeroclaw_workspace),
                "session_state_file": str(session_state_file),
                "workspace": str(ctx.workspace),
            },
        )
