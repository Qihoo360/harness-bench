"""Build and verify the task-capable OpenClaw runtime image."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

from container_runtime import (
    CommandError,
    docker,
    docker_build_provenance_args,
    docker_build_proxy_args,
)


ROOT = Path(__file__).resolve().parents[1]
OPENCLAW_RUNTIME_IMAGE = "harnessbench/openclaw:2026.5.26-tasktools"
OPENCLAW_SOURCE_LABEL = "org.harnessbench.openclaw-source-sha256"
OPENCLAW_SOURCE_FILES = (
    Path(".dockerignore"),
    Path("docker/openclaw/Dockerfile"),
    Path("docker/openclaw/requirements.lock"),
    Path("docker/openclaw/verify_toolchain.py"),
    Path("docker/harnesses/multimodal-npm"),
)


def openclaw_source_sha256() -> str:
    digest = hashlib.sha256()
    for relative in sorted(OPENCLAW_SOURCE_FILES):
        payload = (ROOT / relative).read_bytes()
        digest.update(relative.as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(len(payload).to_bytes(8, "big"))
        digest.update(payload)
    return digest.hexdigest()


def _installed_source() -> str | None:
    inspected = docker(
        "image",
        "inspect",
        OPENCLAW_RUNTIME_IMAGE,
        "--format",
        f'{{{{ index .Config.Labels "{OPENCLAW_SOURCE_LABEL}" }}}}',
        check=False,
    )
    if inspected.returncode != 0:
        return None
    value = inspected.stdout.strip()
    return value if value and value != "<no value>" else None


def verify_openclaw_image() -> str:
    expected = openclaw_source_sha256()
    if _installed_source() != expected:
        raise CommandError(f"OpenClaw runtime source label does not match {expected}")
    docker(
        "run",
        "--rm",
        "--network",
        "none",
        "--read-only",
        "--tmpfs",
        "/tmp:rw,noexec,nosuid,nodev,size=64m",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges:true",
        "--entrypoint",
        "python",
        OPENCLAW_RUNTIME_IMAGE,
        "/opt/harnessbench/verify_toolchain.py",
    )
    return docker(
        "image",
        "inspect",
        OPENCLAW_RUNTIME_IMAGE,
        "--format",
        "{{.Id}}",
    ).stdout.strip()


def build_openclaw_image() -> str:
    expected = openclaw_source_sha256()
    if _installed_source() != expected:
        build_args: list[str] = []
        build_network = os.environ.get(
            "HARNESSBENCH_DOCKER_BUILD_NETWORK",
            "",
        ).strip()
        if build_network:
            build_args.extend(["--network", build_network])
        for source_name, build_name in (
            ("HARNESSBENCH_PYPI_INDEX_URL", "PIP_INDEX_URL"),
            ("HARNESSBENCH_PYPI_EXTRA_INDEX_URL", "PIP_EXTRA_INDEX_URL"),
            ("HARNESSBENCH_PYPI_TRUSTED_HOST", "PIP_TRUSTED_HOST"),
        ):
            value = os.environ.get(source_name, "").strip()
            if value:
                build_args.extend(["--build-arg", f"{build_name}={value}"])
        docker(
            "build",
            "--pull=false",
            *docker_build_provenance_args(),
            *docker_build_proxy_args(),
            *build_args,
            "--build-arg",
            f"HARNESSBENCH_OPENCLAW_SOURCE_SHA256={expected}",
            "--tag",
            OPENCLAW_RUNTIME_IMAGE,
            "--file",
            str(ROOT / "docker" / "openclaw" / "Dockerfile"),
            str(ROOT),
            capture=False,
        )
    return verify_openclaw_image()


def main() -> None:
    print(build_openclaw_image())


if __name__ == "__main__":
    main()
