# Harness-Bench

English | [简体中文](./README.zh-CN.md)

Harness-Bench is a real-workspace benchmark for evaluating agent / claw-style frameworks under executable task conditions. Instead of grading only final text answers, it measures whether an agent can operate inside a sandboxed workspace, produce the required artifacts, follow task constraints, and leave enough traces for process and usage analysis.

The benchmark is currently supports multiple adapters, task hooks, oracle-based grading, process grading, and usage accounting. It is designed for side-by-side evaluation of agent harnesses rather than for a single model or a single product.

## What this project does

Harness-Bench provides:

- Real workspace execution in per-task sandboxes.
- Pluggable adapters for multiple agent frameworks.
- Task-local fixtures, prompts, hooks, and oracle graders.
- Process grading in addition to outcome grading.
- Usage tracking through a benchmark-managed proxy and session logs.
- A unified CLI for single-task and full-suite runs.

In practice, this means you can ask different agent frameworks to solve the exact same task, under the exact same workspace layout, and compare:

- Whether they completed the task correctly.
- Whether they used tools coherently.
- Whether they respected task constraints and safety boundaries.
- How much model usage or cost they incurred.

## Current coverage

The repository currently contains 28 tasks spanning a broad set of agent capabilities, including:

- File operations
- Shell execution
- Browser / local HTTP interaction
- Meeting summarization and email triage
- Session memory and multi-round workflows
- Vision and image-related tasks
- Git / PR workflows
- Office document processing
- Code debugging and repair
- Multi-document synthesis
- Planning and task decomposition
- Heartbeat / long-running monitoring
- Security and prompt-injection defense
- Provider failover and routing analysis
- Incident analysis and runbook synthesis

## Supported adapters

The current registry supports the following adapters:

- `demo`
- `openclaw`
- `picoclaw`
- `nanobot`
- `nanoclaw`
- `nullclaw`
- `moltis`
- `zeroclaw`
- `hermes_agent`
- `generic_cli`

Example model entries live in [config/models.example.yaml].

## Repository layout

```text
Harness-Bench/
├── config/               # App config and model config examples
├── grading/              # Shared grading prompts / helpers
├── scripts/              # Wrapper scripts for selected frameworks
├── src/clawbench_v2/     # CLI, runner, adapters, config loading, grading pipeline
└── tasks/                # Task definitions, prompts, fixtures, hooks, oracles
```

Typical task folders contain:

- `task.yaml`
- `prompt.txt` or `prompt_files`
- `fixtures/`
- `oracle_grade.py`
- optional `hooks.py`
- optional rubric-related files

## Installation

### Requirements

- Python 3.10+
- `PyYAML>=6.0`
- Framework-specific CLIs or wrappers for the adapters you want to run

Install the Python package locally:

```bash
cd Harness-Bench
python3 -m pip install -e .
```

If you prefer not to install the package, you can still run with `PYTHONPATH=src`.

## Configuration

Harness-Bench uses two top-level config files:

- [config/app.yaml]
- [config/models.example.yaml]

### App config

`config/app.yaml` defines project-level paths and defaults, such as:

- `tasks_dir`
- `results_dir`
- `work_root`
- `default_timeout_sec`

Important note: the example app config in this repo is already customized for a local environment. You should review and adjust `results_dir` and `work_root` before large benchmark runs.

You can override the app config path with:

```bash
export CLAWBENCHV2_APP_CONFIG=/absolute/path/to/app.yaml
```

### Model config

`config/models.example.yaml` defines runnable model / framework entries. Each model entry typically includes:

- `adapter`
- `command`
- `user_config`
- `session_prefix`
- `timeout_sec`
- adapter-specific extra fields

You can point the benchmark to another model config file with:

```bash
export CLAWBENCHV2_MODELS_CONFIG=/absolute/path/to/models.yaml
```

### Framework-specific local configs

Some adapters expect local framework config files such as:

- `config/openclaw.json`
- `config/picoclaw.json`
- `config/nullclaw.json`
- `config/zeroclaw.toml`

These files are often private, machine-specific, or API-key-bearing, so they are not guaranteed to be committed in a shared repo. Create them locally as needed for your framework.

## Quick start

List all tasks:

```bash
PYTHONPATH=src python3 -m clawbench_v2.cli tasks
```

Run a single demo task:

```bash
PYTHONPATH=src python3 -m clawbench_v2.cli run-task \
  --task 01-file \
  --model demo-local \
  --mode demo
```

Run a single live task with one of your configured frameworks:

```bash
PYTHONPATH=src python3 -m clawbench_v2.cli run-task \
  --task 01-file \
  --model openclaw-local \
  --mode live
```

Run a full suite:

```bash
PYTHONPATH=src python3 -m clawbench_v2.cli run-suite \
  --model openclaw-local \
  --mode live
```

Resume a suite from a specific task ID:

```bash
PYTHONPATH=src python3 -m clawbench_v2.cli run-suite \
  --model moltis-local \
  --mode live \
  --from-task 07-session-memory
```

Delete the sandbox after a run:

```bash
PYTHONPATH=src python3 -m clawbench_v2.cli run-task \
  --task 01-file \
  --model demo-local \
  --mode demo \
  --delete-sandbox
```

## CLI overview

The main CLI entrypoint is [src/clawbench_v2/cli.py]

Available commands:

- `tasks`
- `run-task`
- `run-suite`

Main arguments:

- `--task`
- `--model`
- `--mode`
- `--delete-sandbox`
- `--from-task` for suite resume

Both `run-task` and `run-suite` print progress lines and elapsed time in seconds. Output JSON includes `elapsed_sec`.

## How execution works

The main runtime logic lives in [src/clawbench_v2/runner.py]

For each run, the benchmark:

1. Creates a fresh sandbox under the configured `work_root`.
2. Creates a real task workspace inside that sandbox.
3. Copies task fixtures into the workspace.
4. Renders prompts using workspace and runtime variables.
5. Runs optional task hooks.
6. Invokes the selected adapter.
7. Runs the oracle grader on the workspace outputs.
8. Extracts usage information from the proxy and/or framework session logs.
9. Runs a process rubric when configured.
10. Writes a result JSON to the configured results directory.

## Output layout

The actual output location depends on [config/app.yaml] especially:

- `results_dir`
- `work_root`

Typical layout after a run:

```text
<work_root>/
└── oc-bench-v2-.../       # sandbox for one task run
    ├── workspace/         # real task workspace
    ├── usage-proxy/       # proxy logs and captured responses
    └── prompt-round1.txt  # rendered prompt snapshot

<results_dir>/
└── <model_id>/
    └── <task_id>.json
```

## Result JSON

Each task result JSON typically contains:

- `task_id`
- `model_id`
- `mode`
- `sandbox`
- `workspace`
- `session_id`
- `usage_summary`
- `oracle_result`
- `process_result`
- `combined_result`
- `scoring`

The benchmark is intentionally designed to preserve both outcome-level and process-level evidence.

## Scoring model

Harness-Bench can combine multiple perspectives:

- Oracle outcome score
- Process score from rubric-based trace evaluation
- Combined score

In the current setup:

- Outcome grading usually comes from each task's `oracle_grade.py`.
- Process grading is derived from proxy traces and rubric logic.
- `combined_score` is typically built from outcome and process signals.

Some multimodal or rubric-primary tasks may emphasize rubric-based grading more heavily than pure artifact checks.

## Usage accounting

When possible, the benchmark captures usage data through a managed usage proxy or framework session logs. Depending on the adapter and trace availability, `usage_summary` may include:

- `input_tokens`
- `output_tokens`
- `cache_read_tokens`
- `cache_write_tokens`
- `total_tokens`
- provider or model metadata

This makes the benchmark useful not only for capability comparison, but also for cost and efficiency analysis.

## Adding a new adapter

The adapter layer lives in [src/clawbench_v2/adapters].

To add a new framework:

1. Implement a new adapter class in `src/clawbench_v2/adapters/`.
2. Export it in `src/clawbench_v2/adapters/__init__.py`.
3. Register it in [src/clawbench_v2/registry.py].
4. Add a model entry to `config/models.example.yaml`.
5. Provide any wrapper scripts or local config files required by that framework.

## Adding a new task

To add a new benchmark task:

1. Create a new folder under `tasks/`.
2. Add `task.yaml`.
3. Add prompt file(s).
4. Add fixtures if the task needs input files.
5. Implement `oracle_grade.py`.
6. Optionally implement `hooks.py`.
7. Optionally add rubric files if process grading needs task-specific logic.

Good tasks in this benchmark generally:

- Operate on a real workspace
- Require concrete artifacts
- Are auditable by code
- Distinguish between frameworks in meaningful ways
- Reflect realistic agent workflows

## Why this benchmark is useful

Harness-Bench is especially useful when you want to compare agent frameworks under realistic operating conditions rather than isolated prompting conditions.

Compared with purely answer-based benchmarks, it gives you a better view of:

- Tool-use reliability
- Workspace discipline
- Long-horizon execution behavior
- Safety behavior under adversarial or constrained tasks
- Engineering usefulness, not just reasoning quality

## Known local assumptions

This repository contains local-environment assumptions in a few places:

- Some model entries point to local config paths.
- `config/app.yaml` may already be tuned for a specific local output directory.
- Some framework CLIs must already be installed on the machine.

Before sharing or open-sourcing the repo broadly, it is a good idea to review:

- local absolute paths
- machine-specific config files
- private API-bearing config files
- output directories under `data/`

## License and release notes

Add your preferred license and release policy here before external publication.

