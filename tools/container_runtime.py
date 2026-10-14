"""Small Docker runtime surface used by the canonical benchmark runner."""

from __future__ import annotations

import functools
import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DOCKER_DIR = ROOT / "docker"
STAGER_IMAGE = (
    "python:3.10-slim@sha256:"
    "c1e4e6c01eb489c422288b2de34b0761ca316f7a2d98e2c33f47659a73ed108a"
)
GRADER_BASE_IMAGE = (
    "python:3.10-bookworm@sha256:"
    "be77ec026cf35f6adf5f4f02015d50b7aadaac98659a02be1fc8ce18c396f652"
)
GRADER_IMAGE = (
    os.environ.get("HARNESSBENCH_GRADER_IMAGE", "").strip()
    or "harnessbench/grader:local"
)
GRADER_SOURCE_LABEL = "org.harnessbench.grader-source-sha256"
GRADER_SOURCE_FILES = (
    Path(".dockerignore"),
    Path("docker/grader/Dockerfile"),
    Path("docker/grader/grade_entrypoint.py"),
    Path("docker/grader/requirements.lock"),
    Path("src/harnessbench/__init__.py"),
    Path("src/harnessbench/grading/__init__.py"),
    Path("src/harnessbench/grading/deterministic_oracle.py"),
    Path("src/harnessbench/grading/legacy_case_adapter.py"),
    Path("src/harnessbench/grading/oracle_contract.py"),
    Path("src/harnessbench/grading/outcome_cases.py"),
    Path("src/harnessbench/grading/pure_function.py"),
    Path("src/harnessbench/grading/record_equivalence.py"),
    Path("src/harnessbench/grading/semantic_judge.py"),
    Path("src/harnessbench/grading/structured_io.py"),
    Path("src/harnessbench/grading/task_checks.py"),
)


class CommandError(RuntimeError):
    pass


def run(
    args: list[str],
    *,
    capture: bool = True,
    check: bool = True,
    timeout: int | None = None,
) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        args,
        text=True,
        capture_output=capture,
        check=False,
        timeout=timeout,
    )
    if check and completed.returncode != 0:
        raise CommandError(
            f"command failed ({completed.returncode}): {' '.join(args)}\n"
            f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
        )
    return completed


def docker(*args: str, **kwargs: Any) -> subprocess.CompletedProcess[str]:
    return run(["docker", *args], **kwargs)


def docker_build_proxy_args() -> list[str]:
    """Return explicit, opt-in proxy args for networked Docker build stages."""
    proxy = os.environ.get("HARNESSBENCH_DOCKER_BUILD_PROXY", "").strip()
    if not proxy:
        return []
    no_proxy = os.environ.get(
        "HARNESSBENCH_DOCKER_BUILD_NO_PROXY",
        "localhost,127.0.0.1,::1,10.0.0.0/8,172.16.0.0/12",
    ).strip()
    values = {
        "HTTP_PROXY": proxy,
        "HTTPS_PROXY": proxy,
        "http_proxy": proxy,
        "https_proxy": proxy,
        "NO_PROXY": no_proxy,
        "no_proxy": no_proxy,
    }
    args: list[str] = []
    for name, value in values.items():
        args.extend(["--build-arg", f"{name}={value}"])
    return args


@functools.lru_cache(maxsize=1)
def buildkit_available() -> bool:
    """True when the Docker CLI can run BuildKit builds.

    ``--provenance`` is a BuildKit-only flag that the legacy builder rejects
    outright, so a host without the buildx component must omit it rather than
    fail every image build.
    """
    if os.environ.get("DOCKER_BUILDKIT", "").strip() == "0":
        return False
    probe = docker("buildx", "version", check=False, timeout=30)
    return probe.returncode == 0


def docker_build_provenance_args() -> list[str]:
    """Attestation flags for image builds, empty under the legacy builder."""
    return ["--provenance=false"] if buildkit_available() else []


def require_docker() -> None:
    if shutil.which("docker") is None:
        raise SystemExit(
            "docker CLI was not found; install Docker Engine or Docker Desktop"
        )
    docker("version", "--format", "{{.Server.Version}}", timeout=15)


def docker_security_identity() -> dict[str, Any]:
    """Return merge-critical isolation facts for the Docker worker."""

    template = (
        '{"security_options":{{json .SecurityOptions}},'
        '"default_runtime":{{json .DefaultRuntime}},'
        '"cgroup_driver":{{json .CgroupDriver}},'
        '"server_version":{{json .ServerVersion}}}'
    )
    completed = docker("info", "--format", template, timeout=15)
    try:
        raw = json.loads(completed.stdout)
    except (TypeError, json.JSONDecodeError) as exc:
        raise CommandError(f"cannot parse Docker security identity: {exc}") from exc
    if not isinstance(raw, dict):
        raise CommandError("Docker security identity is not a JSON object")
    options = sorted(
        str(value) for value in raw.get("security_options", []) if str(value)
    )
    rootless = any("rootless" in value.casefold() for value in options)
    default_runtime = str(raw.get("default_runtime") or "unknown")
    has_seccomp = any(value.startswith("name=seccomp") for value in options)
    has_mac = any(
        value.startswith(("name=apparmor", "name=selinux")) for value in options
    )
    has_cgroup_namespace = any(value.startswith("name=cgroupns") for value in options)
    sandboxed_runtime = default_runtime in {"runsc", "kata", "kata-runtime"}
    base_l1 = rootless and has_seccomp and has_mac and has_cgroup_namespace
    if base_l1 and sandboxed_runtime:
        level = "L2"
    elif base_l1:
        level = "L1"
    else:
        level = "transitional"
    return {
        "schema_version": 1,
        "level": level,
        "engine": "docker",
        "server_version": str(raw.get("server_version") or "unknown"),
        "rootless": rootless,
        "default_runtime": default_runtime,
        "cgroup_driver": str(raw.get("cgroup_driver") or "unknown"),
        "security_options": options,
        "network_policy": "direct-no-secret-model-endpoint-over-bridge",
    }


def grader_source_sha256() -> str:
    """Identify exactly the files copied into the trusted grader image."""
    digest = hashlib.sha256()
    for relative in sorted(GRADER_SOURCE_FILES):
        path = ROOT / relative
        payload = path.read_bytes()
        digest.update(relative.as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(len(payload).to_bytes(8, "big"))
        digest.update(payload)
    return digest.hexdigest()


def _grader_image_source() -> str | None:
    inspected = docker(
        "image",
        "inspect",
        GRADER_IMAGE,
        "--format",
        f'{{{{ index .Config.Labels "{GRADER_SOURCE_LABEL}" }}}}',
        check=False,
    )
    if inspected.returncode != 0:
        return None
    value = inspected.stdout.strip()
    return value if value and value != "<no value>" else None


def _grader_build_args() -> list[str]:
    """Forward optional package-index settings only to the trusted image build."""

    args: list[str] = docker_build_proxy_args()
    build_network = os.environ.get(
        "HARNESSBENCH_DOCKER_BUILD_NETWORK",
        "",
    ).strip()
    if build_network:
        if build_network not in {"default", "host"}:
            raise ValueError(
                "HARNESSBENCH_DOCKER_BUILD_NETWORK must be 'default' or 'host'"
            )
        args.extend(("--network", build_network))
    for name in (
        "HARNESSBENCH_PYPI_INDEX_URL",
        "HARNESSBENCH_PYPI_TRUSTED_HOST",
    ):
        value = os.environ.get(name, "").strip()
        if value:
            args.extend(("--build-arg", f"{name}={value}"))
    return args


def build_images() -> dict[str, str]:
    source_sha256 = grader_source_sha256()
    if _grader_image_source() != source_sha256:
        docker(
            "build",
            "--pull=false",
            *docker_build_provenance_args(),
            *_grader_build_args(),
            "--build-arg",
            f"HARNESSBENCH_GRADER_SOURCE_SHA256={source_sha256}",
            "--tag",
            GRADER_IMAGE,
            "--file",
            str(DOCKER_DIR / "grader" / "Dockerfile"),
            str(ROOT),
            capture=False,
        )
    if _grader_image_source() != source_sha256:
        raise CommandError(
            "trusted grader image source label does not match the current "
            f"build inputs ({source_sha256})"
        )
    return {
        GRADER_IMAGE: docker(
            "image", "inspect", GRADER_IMAGE, "--format", "{{.Id}}"
        ).stdout.strip(),
        STAGER_IMAGE: docker(
            "image", "inspect", STAGER_IMAGE, "--format", "{{.Id}}"
        ).stdout.strip(),
        # The grader Dockerfile pins this exact immutable digest. BuildKit may
        # retain the parent only in its content store, where a subsequent
        # classic `docker image inspect <repo>@<digest>` cannot resolve it.
        # Requiring that extra local tag made a successful, reproducible build
        # fail on otherwise valid workers. Record the pinned parent identity
        # directly; the built grader image and its source label are still
        # inspected above.
        GRADER_BASE_IMAGE: GRADER_BASE_IMAGE.rsplit("@", 1)[-1],
    }


def constrained_run_args() -> list[str]:
    """Restrictions shared by trusted helper and grader containers."""
    return [
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
        "/tmp:rw,noexec,nosuid,size=64m",
    ]
