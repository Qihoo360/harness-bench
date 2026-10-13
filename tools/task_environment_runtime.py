"""Content-addressed task environment resolution for benchmark trials.

The module deliberately does not infer requirements from task titles. A task
must declare either a shared harness floor or a reviewed overlay. Overlay build
contexts are restricted to the task's environment directory and are verified
before an evaluated container can start.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Callable

from container_runtime import docker, docker_build_provenance_args


BUILDER_ID = "harnessbench-task-environment-v1"
SUPPORTED_ABI = "harnessbench-debian-bookworm-v1"
_IMAGE_DIGEST = re.compile(r"^[^\s]+@sha256:[0-9a-f]{64}$")
_SHA256_ID = re.compile(r"^sha256:[0-9a-f]{64}$")
_ALLOWED_MODES = {"shared", "overlay"}
_ALLOWED_KEYS = {
    "schema_version",
    "mode",
    "abi",
    "dockerfile",
    "verifier",
    "prepare",
    "required_tools",
    "agent",
    "grader",
    "inputs",
    "services",
}
_VARIANT_KEYS = {"requirements_lock", "wheelhouse", "apt_packages_lock", "verifier"}
_FORBIDDEN_PARTS = {
    "tests",
    "test",
    "reference",
    "references",
    "ground_truth",
    "answers",
    "answer",
    "oracle",
}
_FORBIDDEN_DOCKER_INSTRUCTIONS = {
    "ENTRYPOINT",
    "CMD",
    "HEALTHCHECK",
    "SHELL",
    "STOPSIGNAL",
    "VOLUME",
}
_PROTECTED_ENV_PREFIXES = (
    "OPENAI_",
    "ANTHROPIC_",
    "GOOGLE_",
    "GEMINI_",
    "MODEL_",
    "HARNESSBENCH_",
)
_LABEL_ENV_SHA = "org.harnessbench.task-environment-sha256"
_LABEL_BUILDER = "org.harnessbench.environment-builder-id"
_LABEL_BASE_ID = "org.harnessbench.environment-base-image-id"
_LABEL_ABI = "org.harnessbench.environment-abi"
_Docker = Callable[..., Any]


class TaskEnvironmentError(RuntimeError):
    """A task environment cannot be safely resolved or verified."""


@dataclass(frozen=True)
class ServiceSpec:
    name: str
    image: str | None
    script: str | None
    port: int
    health_path: str
    state_file: str
    environment: tuple[tuple[str, str], ...]
    health_timeout_sec: int
    init_dir: str | None


@dataclass(frozen=True)
class TaskEnvironmentSpec:
    schema_version: int
    mode: str
    abi: str
    environment_dir: Path
    dockerfile: str | None
    verifier: str | None
    prepare: str | None
    required_tools: tuple[tuple[str, str], ...]
    agent: tuple[tuple[str, str], ...]
    grader: tuple[tuple[str, str], ...]
    inputs: tuple[str, ...]
    services: tuple[ServiceSpec, ...]
    sha256: str
    source_files: tuple[str, ...]

    @property
    def has_overlay(self) -> bool:
        return self.mode == "overlay"


@dataclass(frozen=True)
class ResolvedTaskEnvironment:
    task_environment_sha256: str
    environment_builder_id: str
    abi: str
    mode: str
    harness_image_id: str
    combined_image: str
    combined_image_id: str
    task_grader_image: str
    task_grader_image_id: str
    verifier: dict[str, Any]
    service_image_digests: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "task_environment_sha256": self.task_environment_sha256,
            "environment_builder_id": self.environment_builder_id,
            "task_environment_abi": self.abi,
            "task_environment_mode": self.mode,
            "harness_image_id": self.harness_image_id,
            "combined_image": self.combined_image,
            "combined_image_id": self.combined_image_id,
            "task_grader_image": self.task_grader_image,
            "task_grader_image_id": self.task_grader_image_id,
            "environment_verifier": self.verifier,
            "service_image_digests": list(self.service_image_digests),
        }


def _safe_relative(value: object, *, field: str) -> str:
    text = str(value or "").strip().replace("\\", "/")
    path = PurePosixPath(text)
    if path.parts[:1] == ("environment",):
        if len(path.parts) == 1:
            raise TaskEnvironmentError(f"{field} names the environment directory")
        path = PurePosixPath(*path.parts[1:])
        text = path.as_posix()
    if (
        not text
        or path.is_absolute()
        or "." in path.parts
        or ".." in path.parts
        or any(part.casefold() in _FORBIDDEN_PARTS for part in path.parts)
    ):
        raise TaskEnvironmentError(f"{field} must be one safe environment-relative path")
    return path.as_posix()


def _variant(raw: object, *, field: str) -> tuple[tuple[str, str], ...]:
    if raw is None:
        return ()
    if not isinstance(raw, dict):
        raise TaskEnvironmentError(f"environment.{field} must be a mapping")
    unknown = set(raw) - _VARIANT_KEYS
    if unknown:
        raise TaskEnvironmentError(
            f"environment.{field} contains unsupported keys: {sorted(unknown)}"
        )
    return tuple(
        (key, _safe_relative(raw[key], field=f"environment.{field}.{key}"))
        for key in sorted(raw)
    )


def _services(raw: object) -> tuple[ServiceSpec, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise TaskEnvironmentError("environment.services must be a list")
    services: list[ServiceSpec] = []
    seen: set[str] = set()
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise TaskEnvironmentError(f"environment.services[{index}] must be a mapping")
        unknown = set(item) - {
            "name",
            "image",
            "script",
            "port",
            "health_path",
            "state_file",
            "environment",
            "health_timeout_sec",
            "init_dir",
        }
        if unknown:
            raise TaskEnvironmentError(
                f"environment.services[{index}] contains unsupported keys: {sorted(unknown)}"
            )
        name = str(item.get("name") or "").strip()
        if not re.fullmatch(r"[a-z][a-z0-9-]{0,31}", name) or name in seen:
            raise TaskEnvironmentError(f"invalid or duplicate service name: {name!r}")
        seen.add(name)
        image = str(item.get("image") or "").strip()
        script = (
            _safe_relative(item.get("script"), field=f"service {name} script")
            if item.get("script") is not None
            else None
        )
        if bool(image) == bool(script):
            raise TaskEnvironmentError(
                f"service {name} must declare exactly one of image or script"
            )
        if image and not _IMAGE_DIGEST.fullmatch(image):
            raise TaskEnvironmentError(
                f"service {name} image must be pinned by sha256 digest"
            )
        port = int(item.get("port") or 0)
        if not 1 <= port <= 65535:
            raise TaskEnvironmentError(f"service {name} port must be in [1, 65535]")
        health_path = str(item.get("health_path") or "").strip()
        if (
            not health_path.startswith("/")
            or ".." in PurePosixPath(health_path).parts
            or "?" in health_path
            or "#" in health_path
        ):
            raise TaskEnvironmentError(f"service {name} health_path is invalid")
        state_file = _safe_relative(
            item.get("state_file"), field=f"service {name} state_file"
        )
        raw_environment = item.get("environment") or {}
        if not isinstance(raw_environment, dict):
            raise TaskEnvironmentError(f"service {name} environment must be a mapping")
        environment: list[tuple[str, str]] = []
        for key, value in sorted(raw_environment.items()):
            key = str(key)
            if (
                not re.fullmatch(r"[A-Z][A-Z0-9_]*", key)
                or key.startswith(_PROTECTED_ENV_PREFIXES)
            ):
                raise TaskEnvironmentError(
                    f"service {name} has forbidden environment key: {key!r}"
                )
            environment.append((key, str(value)))
        health_timeout = int(item.get("health_timeout_sec") or 60)
        if not 1 <= health_timeout <= 300:
            raise TaskEnvironmentError(
                f"service {name} health_timeout_sec must be in [1, 300]"
            )
        init_dir = item.get("init_dir")
        services.append(
            ServiceSpec(
                name=name,
                image=image or None,
                script=script,
                port=port,
                health_path=health_path,
                state_file=state_file,
                environment=tuple(environment),
                health_timeout_sec=health_timeout,
                init_dir=(
                    _safe_relative(init_dir, field=f"service {name} init_dir")
                    if init_dir is not None
                    else None
                ),
            )
        )
    return tuple(services)


def _environment_files(environment_dir: Path) -> dict[str, bytes]:
    if not environment_dir.is_dir():
        raise TaskEnvironmentError(f"missing environment directory: {environment_dir}")
    files: dict[str, bytes] = {}
    for path in sorted(environment_dir.rglob("*")):
        if path.is_symlink():
            raise TaskEnvironmentError(f"environment inputs must not be symlinks: {path}")
        if not path.is_file() or "__pycache__" in path.parts or path.suffix == ".pyc":
            continue
        relative = path.relative_to(environment_dir)
        if any(part.casefold() in _FORBIDDEN_PARTS for part in relative.parts):
            raise TaskEnvironmentError(
                f"forbidden hidden-evaluation path in environment context: {relative}"
            )
        files[relative.as_posix()] = path.read_bytes()
    return files


def _hash_spec(normalized: dict[str, Any], files: dict[str, bytes]) -> str:
    digest = hashlib.sha256()
    digest.update(
        json.dumps(
            normalized,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    )
    digest.update(b"\0")
    for relative, payload in sorted(files.items()):
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(len(payload).to_bytes(8, "big"))
        digest.update(payload)
        digest.update(b"\0")
    return digest.hexdigest()


def load_task_environment(task_dir: Path, metadata: dict[str, Any]) -> TaskEnvironmentSpec:
    raw = metadata.get("environment")
    if not isinstance(raw, dict):
        raise TaskEnvironmentError(
            "experimental task environments require an explicit environment mapping"
        )
    unknown = set(raw) - _ALLOWED_KEYS
    if unknown:
        raise TaskEnvironmentError(
            f"environment contains unsupported keys: {sorted(unknown)}"
        )
    schema_version = int(raw.get("schema_version") or 1)
    if schema_version != 1:
        raise TaskEnvironmentError(
            f"unsupported environment schema_version: {schema_version}"
        )
    mode = str(raw.get("mode") or "").strip()
    if mode not in _ALLOWED_MODES:
        raise TaskEnvironmentError(
            f"environment.mode must be one of {sorted(_ALLOWED_MODES)}"
        )
    abi = str(raw.get("abi") or "").strip()
    if abi != SUPPORTED_ABI:
        raise TaskEnvironmentError(f"environment.abi must be {SUPPORTED_ABI!r}")
    environment_dir = (task_dir / "environment").resolve()
    try:
        environment_dir.relative_to(task_dir.resolve())
    except ValueError as exc:
        raise TaskEnvironmentError("environment directory escaped task root") from exc

    dockerfile = (
        _safe_relative(raw.get("dockerfile"), field="environment.dockerfile")
        if raw.get("dockerfile") is not None
        else None
    )
    verifier = (
        _safe_relative(raw.get("verifier"), field="environment.verifier")
        if raw.get("verifier") is not None
        else None
    )
    prepare = (
        _safe_relative(raw.get("prepare"), field="environment.prepare")
        if raw.get("prepare") is not None
        else None
    )
    if mode == "overlay" and (dockerfile is None or verifier is None):
        raise TaskEnvironmentError("overlay mode requires dockerfile and verifier")
    if mode == "shared" and dockerfile is not None:
        raise TaskEnvironmentError("shared mode must not declare a dockerfile")
    if mode == "shared" and (environment_dir / "Dockerfile").is_file():
        raise TaskEnvironmentError(
            "shared mode must not contain an undeclared environment/Dockerfile"
        )

    required_tools_raw = raw.get("required_tools") or {}
    if not isinstance(required_tools_raw, dict):
        raise TaskEnvironmentError("environment.required_tools must be a mapping")
    required_tools = tuple(
        sorted((str(key), str(value)) for key, value in required_tools_raw.items())
    )
    for name, version in required_tools:
        if not re.fullmatch(r"[A-Za-z0-9_.+-]+", name) or not version.strip():
            raise TaskEnvironmentError(f"invalid required tool contract: {name!r}")

    agent = _variant(raw.get("agent"), field="agent")
    grader = _variant(raw.get("grader"), field="grader")
    services = _services(raw.get("services"))
    inputs_raw = raw.get("inputs") or []
    if not isinstance(inputs_raw, list):
        raise TaskEnvironmentError("environment.inputs must be a list")
    inputs = tuple(
        _safe_relative(value, field="environment.inputs") for value in inputs_raw
    )
    if len(set(inputs)) != len(inputs):
        raise TaskEnvironmentError("environment.inputs contains duplicates")

    declared = {
        value
        for value in (
            dockerfile,
            verifier,
            prepare,
            *(path for _, path in agent),
            *(path for _, path in grader),
            *(service.script for service in services),
            *(service.init_dir for service in services),
            *inputs,
        )
        if value is not None
    }
    if environment_dir.is_dir():
        files = _environment_files(environment_dir)
    elif declared:
        raise TaskEnvironmentError(
            f"missing environment directory: {environment_dir}"
        )
    else:
        files = {}
    for relative in declared:
        path = environment_dir / relative
        if not path.exists():
            raise TaskEnvironmentError(f"declared environment input is missing: {relative}")
    if verifier is not None and not (environment_dir / verifier).is_file():
        raise TaskEnvironmentError(f"environment verifier is not a file: {verifier}")
    if dockerfile is not None and not (environment_dir / dockerfile).is_file():
        raise TaskEnvironmentError(f"environment dockerfile is not a file: {dockerfile}")
    if prepare is not None and not (environment_dir / prepare).is_file():
        raise TaskEnvironmentError(f"environment prepare script is not a file: {prepare}")

    if mode == "shared":
        included = set(declared)
        hooks_name = str(metadata.get("hooks_module") or "")
        if hooks_name:
            hook_path = PurePosixPath(hooks_name.replace("\\", "/"))
            if hook_path.parts[:1] == ("environment",):
                included.add(PurePosixPath(*hook_path.parts[1:]).as_posix())
        hash_files = {
            relative: payload
            for relative, payload in files.items()
            if relative in included
            or any(relative.startswith(f"{prefix.rstrip('/')}/") for prefix in included)
        }
    else:
        hash_files = files

    normalized = {
        "schema_version": schema_version,
        "mode": mode,
        "abi": abi,
        "dockerfile": dockerfile,
        "verifier": verifier,
        "prepare": prepare,
        "required_tools": dict(required_tools),
        "agent": dict(agent),
        "grader": dict(grader),
        "inputs": list(inputs),
        "services": [
            {
                "name": service.name,
                "image": service.image,
                "script": service.script,
                "port": service.port,
                "health_path": service.health_path,
                "state_file": service.state_file,
                "environment": dict(service.environment),
                "health_timeout_sec": service.health_timeout_sec,
                "init_dir": service.init_dir,
            }
            for service in services
        ],
    }
    return TaskEnvironmentSpec(
        schema_version=schema_version,
        mode=mode,
        abi=abi,
        environment_dir=environment_dir,
        dockerfile=dockerfile,
        verifier=verifier,
        prepare=prepare,
        required_tools=required_tools,
        agent=agent,
        grader=grader,
        inputs=inputs,
        services=services,
        sha256=_hash_spec(normalized, hash_files),
        source_files=tuple(sorted(hash_files)),
    )


def validate_overlay_dockerfile(spec: TaskEnvironmentSpec) -> None:
    if not spec.has_overlay or spec.dockerfile is None:
        return
    text = (spec.environment_dir / spec.dockerfile).read_text(
        encoding="utf-8",
        errors="strict",
    )
    instructions = [
        match.group(1).upper()
        for match in re.finditer(r"(?m)^\s*([A-Za-z]+)\b", text)
    ]
    forbidden = sorted(set(instructions) & _FORBIDDEN_DOCKER_INSTRUCTIONS)
    if forbidden:
        raise TaskEnvironmentError(
            f"overlay Dockerfile changes inherited runtime contract: {forbidden}"
        )
    from_lines = re.findall(r"(?im)^\s*FROM\s+([^\s]+)", text)
    if from_lines != ["${HARNESS_BASE}"]:
        raise TaskEnvironmentError(
            "overlay Dockerfile must contain exactly 'FROM ${HARNESS_BASE}'"
        )
    if not re.search(r"(?im)^\s*ARG\s+HARNESS_BASE\b", text):
        raise TaskEnvironmentError("overlay Dockerfile must declare ARG HARNESS_BASE")
    if spec.agent != spec.grader and not re.search(
        r"(?im)^\s*ARG\s+TASK_ENV_VARIANT\b", text
    ):
        raise TaskEnvironmentError("variant-specific locks require ARG TASK_ENV_VARIANT")
    protected = "|".join(re.escape(prefix) for prefix in _PROTECTED_ENV_PREFIXES)
    if re.search(rf"(?im)^\s*ENV\s+(?:{protected})", text):
        raise TaskEnvironmentError(
            "overlay Dockerfile must not change provider or harness environment"
        )
    if re.search(r"(?im)^\s*(?:ADD|COPY)\s+[^\n]*(?:\.\./|https?://)", text):
        raise TaskEnvironmentError("overlay Dockerfile contains an unsafe ADD/COPY source")


def _image_config(image: str, docker_fn: _Docker) -> dict[str, Any]:
    completed = docker_fn("image", "inspect", image, "--format", "{{json .}}")
    try:
        value = json.loads(completed.stdout)
    except (AttributeError, json.JSONDecodeError) as exc:
        raise TaskEnvironmentError(f"cannot inspect image {image!r}") from exc
    if not isinstance(value, dict):
        raise TaskEnvironmentError(f"invalid image inspection for {image!r}")
    return value


def _image_id(config: dict[str, Any]) -> str:
    value = str(config.get("Id") or "")
    if not _SHA256_ID.fullmatch(value):
        raise TaskEnvironmentError(f"image has invalid immutable ID: {value!r}")
    return value


def _cache_tag(kind: str, spec: TaskEnvironmentSpec, base_id: str) -> str:
    key = hashlib.sha256(
        (
            BUILDER_ID
            + "\0"
            + kind
            + "\0"
            + spec.abi
            + "\0"
            + spec.sha256
            + "\0"
            + base_id
        ).encode("utf-8")
    ).hexdigest()
    return f"harnessbench/task-environment-{kind}:{key[:32]}"


def _labels(spec: TaskEnvironmentSpec, base_id: str) -> dict[str, str]:
    return {
        _LABEL_ENV_SHA: spec.sha256,
        _LABEL_BUILDER: BUILDER_ID,
        _LABEL_BASE_ID: base_id,
        _LABEL_ABI: spec.abi,
    }


def _validate_combined_image(
    *,
    base: dict[str, Any],
    final: dict[str, Any],
    expected_labels: dict[str, str],
) -> None:
    base_config = base.get("Config") or {}
    final_config = final.get("Config") or {}
    for field in ("Entrypoint", "Cmd"):
        if final_config.get(field) != base_config.get(field):
            raise TaskEnvironmentError(
                f"combined image changed inherited harness {field}"
            )
    if str(final_config.get("User") or "") not in {"1000", "1000:1000"}:
        raise TaskEnvironmentError("combined image must run as UID/GID 1000")
    if str(final_config.get("WorkingDir") or "") != "/workspace":
        raise TaskEnvironmentError("combined image must use /workspace")
    labels = final_config.get("Labels") or {}
    for key, value in expected_labels.items():
        if labels.get(key) != value:
            raise TaskEnvironmentError(f"combined image missing provenance label {key}")


def _run_verifier(
    *,
    spec: TaskEnvironmentSpec,
    image: str,
    docker_fn: _Docker,
    verifier_relative: str | None,
    required_tools: dict[str, str],
    role: str,
) -> dict[str, Any]:
    if verifier_relative is None:
        return {
            "status": "not_required",
            "role": role,
            "required_tools": required_tools,
        }
    verifier = (spec.environment_dir / verifier_relative).resolve()
    started = time.monotonic()
    completed = docker_fn(
        "run",
        "--rm",
        "--network",
        "none",
        "--read-only",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges:true",
        "--pids-limit",
        "64",
        "--memory",
        "256m",
        "--cpus",
        "1",
        "--tmpfs",
        "/tmp:rw,noexec,nosuid,size=32m",
        "--user",
        "1000:1000",
        "--mount",
        f"type=bind,src={verifier},dst=/opt/harnessbench/verify_environment.py,readonly",
        "--entrypoint",
        "python3",
        image,
        "/opt/harnessbench/verify_environment.py",
        check=False,
        timeout=120,
    )
    return {
        "status": "pass" if completed.returncode == 0 else "fail",
        "role": role,
        "returncode": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
        "elapsed_sec": round(time.monotonic() - started, 3),
        "required_tools": required_tools,
    }


def run_task_environment_prepare(
    *,
    spec: TaskEnvironmentSpec,
    resolved: ResolvedTaskEnvironment,
    workspace: Path,
    runtime: Path,
    labels: tuple[str, ...] | list[str] = (),
    docker_fn: _Docker = docker,
) -> dict[str, Any]:
    """Run task workspace preparation inside the resolved agent image."""
    if spec.prepare is None:
        return {"status": "not_declared"}
    script = (spec.environment_dir / spec.prepare).resolve()
    started = time.monotonic()
    completed = docker_fn(
        "run",
        "--rm",
        *labels,
        "--network",
        "none",
        "--read-only",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges:true",
        "--pids-limit",
        "128",
        "--memory",
        "512m",
        "--cpus",
        "1",
        "--tmpfs",
        "/tmp:rw,nosuid,size=64m",
        "--user",
        "1000:1000",
        "--env",
        "HOME=/tmp",
        "--env",
        "HB_WORKSPACE=/workspace",
        "--env",
        "HB_RUNTIME=/runtime",
        "--env",
        "GIT_CONFIG_COUNT=1",
        "--env",
        "GIT_CONFIG_KEY_0=safe.directory",
        "--env",
        "GIT_CONFIG_VALUE_0=*",
        "--mount",
        f"type=bind,src={workspace.resolve()},dst=/workspace",
        "--mount",
        f"type=bind,src={runtime.resolve()},dst=/runtime",
        "--mount",
        (
            f"type=bind,src={script},"
            "dst=/opt/harnessbench/prepare_environment.py,readonly"
        ),
        "--workdir",
        "/workspace",
        "--entrypoint",
        "python3",
        resolved.combined_image,
        "/opt/harnessbench/prepare_environment.py",
        check=False,
        timeout=300,
    )
    report = {
        "status": "pass" if completed.returncode == 0 else "fail",
        "returncode": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
        "elapsed_sec": round(time.monotonic() - started, 3),
        "image": resolved.combined_image,
        "image_id": resolved.combined_image_id,
        "script": spec.prepare,
    }
    if completed.returncode != 0:
        raise TaskEnvironmentError(
            "task environment prepare failed: "
            f"{completed.stderr or completed.stdout}"
        )
    return report


class TaskEnvironmentBuilder:
    def __init__(self, *, docker_fn: _Docker = docker) -> None:
        self._docker = docker_fn

    def _resolve_variant(
        self,
        *,
        kind: str,
        spec: TaskEnvironmentSpec,
        base_image: str,
    ) -> tuple[str, str]:
        base = _image_config(base_image, self._docker)
        base_id = _image_id(base)
        if not spec.has_overlay:
            return base_image, base_id
        validate_overlay_dockerfile(spec)
        assert spec.dockerfile is not None
        tag = _cache_tag(kind, spec, base_id)
        expected_labels = _labels(spec, base_id)
        existing = self._docker(
            "image",
            "inspect",
            tag,
            "--format",
            "{{json .}}",
            check=False,
        )
        cached: dict[str, Any] | None = None
        if existing.returncode == 0:
            try:
                value = json.loads(existing.stdout)
                cached = value if isinstance(value, dict) else None
            except json.JSONDecodeError:
                cached = None
        if cached is None:
            command = [
                "build",
                "--pull=false",
                *docker_build_provenance_args(),
                "--network",
                "none",
                "--build-arg",
                f"HARNESS_BASE={base_id}",
                "--build-arg",
                f"TASK_ENV_VARIANT={kind}",
            ]
            for key, value in expected_labels.items():
                command.extend(("--label", f"{key}={value}"))
            command.extend(
                (
                    "--tag",
                    tag,
                    "--file",
                    str(spec.environment_dir / spec.dockerfile),
                    str(spec.environment_dir),
                )
            )
            self._docker(*command, capture=False)
            cached = _image_config(tag, self._docker)
        _validate_combined_image(
            base=base,
            final=cached,
            expected_labels=expected_labels,
        )
        return tag, _image_id(cached)

    def resolve(
        self,
        *,
        spec: TaskEnvironmentSpec,
        harness_image: str,
        grader_image: str,
    ) -> ResolvedTaskEnvironment:
        harness_config = _image_config(harness_image, self._docker)
        harness_id = _image_id(harness_config)
        combined_image, combined_id = self._resolve_variant(
            kind="agent",
            spec=spec,
            base_image=harness_image,
        )
        grader_variant, grader_id = self._resolve_variant(
            kind="grader",
            spec=spec,
            base_image=grader_image,
        )
        grader_contract = dict(spec.grader)
        verifier = {
            "agent": _run_verifier(
                spec=spec,
                image=combined_image,
                docker_fn=self._docker,
                verifier_relative=spec.verifier,
                required_tools=dict(spec.required_tools),
                role="agent",
            ),
            "grader": _run_verifier(
                spec=spec,
                image=grader_variant,
                docker_fn=self._docker,
                verifier_relative=grader_contract.get("verifier"),
                required_tools={},
                role="grader",
            ),
        }
        for role, report in verifier.items():
            if report["status"] != "fail":
                continue
            raise TaskEnvironmentError(
                f"{role} environment verifier failed for {spec.sha256}: "
                f"{report.get('stderr') or report.get('stdout')}"
            )
        service_digests = tuple(
            (
                service.image.rsplit("@", 1)[-1]
                if service.image is not None
                else combined_id
            )
            for service in spec.services
        )
        return ResolvedTaskEnvironment(
            task_environment_sha256=spec.sha256,
            environment_builder_id=BUILDER_ID,
            abi=spec.abi,
            mode=spec.mode,
            harness_image_id=harness_id,
            combined_image=combined_image,
            combined_image_id=combined_id,
            task_grader_image=grader_variant,
            task_grader_image_id=grader_id,
            verifier=verifier,
            service_image_digests=service_digests,
        )
