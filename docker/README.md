# Docker evaluation contract

The release has one supported execution path:

```text
tools/run_benchmark.py
```

The Python process is a trusted controller. Evaluated harnesses and the oracle
never run directly on the host.

The host-side controller is installed from `pyproject.toml` and must satisfy
`PyYAML>=6.0`; Python <3.11 also requires `tomli`. OpenClaw and every other
evaluated harness remain OCI-image-only and must not be installed or executed
on the host.

## Components

```text
docker/
├── grader/
│   ├── Dockerfile
│   └── grade_entrypoint.py
├── openclaw/
│   ├── Dockerfile
│   ├── requirements.lock
│   └── verify_toolchain.py
├── harnesses/
│   ├── antigravity/
│   ├── claude-code/
│   ├── codex/
│   ├── goose/
│   ├── hermes/
│   ├── nanobot/
│   └── opencode/
├── isolation.example.yaml
└── README.md

tools/
├── container_runtime.py
├── harness_runtime.py
├── openclaw_runtime.py
├── task_maintenance/
│   └── validate_docker_grader.py
└── run_benchmark.py
```

OpenClaw uses the derived image defined in `docker/openclaw/`. Its first runtime
layer is the official release pinned in `tools/openclaw_runtime.py`; the only
addition is the version-locked, offline task toolchain. The source hash and
runtime file-generation probe are enforced by `tools/openclaw_image.py`.
Additional harnesses implement the shared one-shot OCI contract in
`tools/harness_runtime.py` and have version-pinned build contexts under
`docker/harnesses/`. The local grader is independent of every harness and is
rebuilt from `docker/grader/Dockerfile`.

The controller resolves the active release through `catalog/release.yaml`,
currently the frozen Standard200 manifest with 200 packages under `tasks/`, and
records the declared grading mode. Harness traces are retained for diagnosis.
Deterministic checks own structured state, calculations, evidence, integrity,
and safety; a bounded semantic judge may score only an explicitly eligible prose
block and cannot rescue any structured check or gate. Optional broader
LLM-judge alignment studies are non-leaderboard research artifacts and cannot
affect admission, `outcome_score`, or ranking.
Each summary also records `controller_runtime` and a content-addressed
`controller_runtime_id` for the host interpreter, required Python distribution
trees and `uv.lock`. Resume and strict shard merge fail closed on a changed
runtime; a formal run plan freezes the same ID.

Formal task packages also contain `environment/Dockerfile`. The current runner
hashes and statically validates that file, including digest-pinned `FROM` lines
and a final non-root `USER`, but does not yet build or compose it with the
selected harness image. The mount graph below therefore describes the common
harness runtime actually used today, not a per-task execution cell.

## Harness OCI contract

Every non-OpenClaw image has one entrypoint and receives the same mounts and
environment:

| Path or variable | Access | Meaning |
|---|---|---|
| `/workspace` | read/write | task seed, in-place work, and deliverables |
| `/runtime` | read/write | task-owned runtime state |
| `/state` | read/write volume | fresh harness home/config/session state |
| `/run/harness` | read-only | rendered prompt and harness config |
| `/trace` | read/write | native event stream, stdout and stderr |
| `HARNESSBENCH_PROMPT` | read-only path | prompt for the current round |
| `HARNESSBENCH_CONFIG` | read-only path | generated config |

The wrapper must execute exactly one agent turn, forward the process exit code
and never install packages or contact an alternate model endpoint at runtime.
Each task receives a fresh state volume. User profiles, memory, MCP servers,
plugins, skills, subagents and public web tools are disabled in the controlled
core profile.

If a task's declared `workspace_dir` is absent, it denotes an empty seed and
the controller still creates a fresh `/workspace`. A present seed must be a
real directory containing only real directories and regular files. The agent
therefore sees the same unified writable root whether it starts empty, edits
seeded files in place, or creates new deliverables.

## Per-task mount graph

An OpenClaw trial receives:

| Container path | Access | Contents |
|---|---|---|
| `/workspace` | read/write | one unified task seed and model-generated state |
| `/runtime` | read/write | task runtime state, such as a local Git remote |
| `/home/node/.openclaw` | read/write volume | per-run OpenClaw config and session |
| `/tmp` | tmpfs | temporary files |

The agent does not receive the repository, oracle, ground truth, reference
solution, other workspaces, Docker socket or host home.

After the agent exits, the grader receives:

| Container path | Access | Contents |
|---|---|---|
| `/workspace` | read-only | frozen agent workspace |
| `/runtime` | read-only | frozen task runtime |
| `/grader-control` | read-only | trusted workspace-mutation report |
| `/hidden` | read-only | task oracle and hidden task bundle |
| `/tmp` | tmpfs | grader temporary files |

The grader uses `network=none`, a read-only root filesystem, no Linux
capabilities and `no-new-privileges`. It verifies that a workspace write probe
fails before executing the oracle.

## Trial sequence

```text
load immutable task definition
  -> create fresh workspace/runtime/state
  -> render prompt with container paths
  -> hash the trusted pre-round workspace baseline
  -> run selected harness container
  -> stop agent, quiesce environment writers, and hash final workspace
  -> enforce the exact per-task mutation contract
  -> collect and validate the native trace
  -> mount frozen workspace into grader
  -> recompute and verify the final workspace digest and entry count
  -> execute deterministic oracle
  -> write result.json, summary.json, and file:// dashboard summary.js
  -> remove temporary OpenClaw state volume
```

Each task has an independent timeout and container lifecycle. A worker pool
controls concurrency; workers never share workspace or OpenClaw state.

## Image policy

Release images must use immutable digests. The current runner pins:

- `harnessbench/openclaw:2026.5.26-tasktools`, derived from the pinned
  OpenClaw `2026.5.26-slim` digest and a pinned Python 3.12 Bookworm toolchain
  plus checksum-pinned `amtool` 0.32.1;
- exact released versions of Antigravity, Hermes, nanobot, goose, OpenCode,
  Claude Code and Codex CLI listed in `tools/harness_runtime.py`;
- `python:3.10-slim` for trusted staging;
- a locally built `harnessbench/grader:local` image based on pinned
  `python:3.10-bookworm`, including Git 2.39.5 for the Git-state
  oracle and checksum-pinned Alertmanager `amtool` 0.32.1 for real configuration
  validation. The grader verifies and records its Python/Git runtime, and its
  image ID is written into the run summary.

Changing any harness image, task definition, model setting or generation budget
creates a different experiment. Shards with incompatible settings are rejected
by the merge tool.

Controller Python environments are host-local runtime inputs too. On a
multi-CPU deployment, create a separate locked virtual environment on local
storage on every host; do not share `.venv` through Ceph or NFS, where absolute
interpreter symlinks can resolve differently. All hosts must emit the same
`controller_runtime_id`, and the formal run plan must carry that ID.

The derived OpenClaw image is built once and transferred with
`docker save`/`docker load` for multi-host or air-gapped runs.
`tools/openclaw_image.py` accepts the loaded tag only when its source label
matches every Docker build input and its offline runtime probe passes. See the
[CPU worker preparation](../docs/CPU_DOCKER_GPU_VLLM_RUNBOOK.md#cpu-worker-preparation).

## Current boundary and target boundary

Implemented now:

- all supported harnesses are container-only;
- agent and grader are separate containers;
- agent cannot mount hidden grading data;
- grader is offline and sees the workspace read-only;
- containers are non-privileged, capability-free and resource-bounded;
- each run records task and suite hashes, controller code/runtime identities,
  and image IDs.

Not implemented as a strong untrusted-submission boundary:

- per-task `environment/Dockerfile` is not yet consumed by an environment
  provider;
- lifecycle hooks are still trusted controller code;
- local task services are not all sidecars;
- the agent reaches a no-secret model endpoint over a Docker bridge;
- rootless Docker/Podman and gVisor are not enforced by the runner.

The production target is represented by
[`isolation.example.yaml`](isolation.example.yaml). Do not
claim that the current local profile safely executes arbitrary third-party
images or task hooks.
