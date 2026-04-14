from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any


def _run(cmd: list[str], *, cwd: Path | None = None) -> None:
    completed = subprocess.run(cmd, cwd=str(cwd) if cwd else None, capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        raise RuntimeError(f"command failed: {' '.join(cmd)}\n{completed.stderr.strip()}")


def prepare_runtime(runtime: dict[str, Any]) -> dict[str, Any]:
    sandbox = Path(runtime["sandbox"])
    workspace = Path(runtime["workspace"])
    init_repo = sandbox / "init-repo"
    remote = sandbox / "remote.git"
    init_repo.mkdir(parents=True, exist_ok=True)

    git_name = "Bench User"
    git_email = "bench-pr@local"

    _run(["git", "init", "--bare", str(remote)])
    _run(["git", "init", "-b", "main"], cwd=init_repo)
    _run(["git", "config", "user.name", git_name], cwd=init_repo)
    _run(["git", "config", "user.email", git_email], cwd=init_repo)
    (init_repo / "README.md").write_text("# Bench project\n", encoding="utf-8")
    _run(["git", "add", "README.md"], cwd=init_repo)
    _run(["git", "commit", "-m", "chore: init"], cwd=init_repo)
    _run(["git", "remote", "add", "origin", str(remote.resolve())], cwd=init_repo)
    _run(["git", "push", "-u", "origin", "main"], cwd=init_repo)
    _run(["git", "--git-dir", str(remote), "symbolic-ref", "HEAD", "refs/heads/main"])

    _run(["git", "checkout", "-b", "feature/pr-add-doc"], cwd=init_repo)
    (init_repo / "CONTRIBUTING.md").write_text("# Contributing\n\nBENCH_PR_OK follow project rules.\n", encoding="utf-8")
    _run(["git", "add", "CONTRIBUTING.md"], cwd=init_repo)
    _run(["git", "commit", "-m", "docs: add contributing guidelines"], cwd=init_repo)
    _run(["git", "push", "-u", "origin", "feature/pr-add-doc"], cwd=init_repo)

    _run(["git", "clone", str(remote.resolve()), str(workspace)])
    _run(["git", "config", "user.name", git_name], cwd=workspace)
    _run(["git", "config", "user.email", git_email], cwd=workspace)
    return {"REMOTE_PATH": str(remote.resolve())}
