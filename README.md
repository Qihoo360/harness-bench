<p align="center">
  <img src="docs/images/harness-bench.png" alt="Harness-Bench" width="100%">
</p>

<h3 align="center">Harness-Bench: Measuring Harness Effects across Models in Realistic Agent Workflows</h3>

<p align="center">
  <a href="https://arxiv.org/pdf/2605.27922"><img src="https://img.shields.io/badge/arXiv-2605.27922-b31b1b.svg" alt="arXiv"></a>
  <a href="https://www.harness-bench.ai/"><img src="https://img.shields.io/badge/Website-harness--bench.ai-f5b700.svg" alt="Website"></a>
  <img src="https://img.shields.io/badge/tasks-274%20general%20%2B%2026%20multimodal-2f6fde.svg" alt="Tasks">
  <img src="https://img.shields.io/badge/harnesses-7-5b3fd1.svg" alt="Harnesses">
  <img src="https://img.shields.io/badge/python-3.10%2B-3776ab.svg" alt="Python 3.10+">
</p>

<p align="center">
  <a href="https://arxiv.org/pdf/2605.27922"><b>Paper</b></a> ·
  <a href="https://www.harness-bench.ai/"><b>Website</b></a> ·
  <a href="https://github.com/Qihoo360/harness-bench"><b>Code</b></a> ·
  <a href="docs/TASK_TAXONOMY_V3.md"><b>Task taxonomy</b></a> ·
  <a href="#quick-start"><b>Quick start</b></a> ·
  <a href="#citation"><b>Citation</b></a>
</p>

<p align="center"><b>English</b> | <a href="README.zh-CN.md">简体中文</a></p>

---

LLM agents increasingly run as executable systems that use tools, modify
workspaces and produce concrete artifacts. Their performance depends not only on
the base model but also on the **harness**: the system layer that manages
context, tools, state, constraints, permissions, tracing and recovery.
**Harness-Bench** evaluates harness configurations across model backends under
shared task environments, budgets and evaluation protocols, while preserving
each harness's native execution behavior, so that agent capability can be
reported at the model-harness level.

## At a glance

| | |
|---|---|
| **Tasks** | 300 sandboxed offline workflow tasks: 274 general and 26 multimodal (browser-verified frontend) |
| **Taxonomy** | 6 task families, 17 subcategories ([details](docs/TASK_TAXONOMY_V3.md)) |
| **Harnesses** | OpenClaw, Antigravity, Claude Code, Codex, Hermes, Nanobot, Goose, OpenCode, each in its own Docker image |
| **Grading** | 1,408 scored work modules plus safety gates, checked by hidden task-specific oracles in an offline grader container |
| **Evidence** | Every trial keeps final artifacts, provider transactions, a normalized trajectory and a session summary |

| Task family | Tasks | Subcategories |
|---|---:|---|
| Software Engineering | 81 | Implementation and Repair · Repository Maintenance |
| Business Operations | 71 | Rule-Based Review · Transaction Processing · Coordination |
| Systems Engineering | 65 | Infrastructure Implementation · Operations and Deployment · Incident Recovery |
| Research and Analysis | 40 | Data Preparation · Evidence-Based Research · Quantitative Analysis |
| Web Development | 27 | Interactive Applications · Visual Tools · Layout and Accessibility |
| Content Production | 16 | Documents and Presentations · Document Processing · Media Processing |

Each task ships its instruction, initial workspace, environment and hidden
evaluators. See [Task authoring and acceptance](docs/TASK_AUTHORING_AND_ACCEPTANCE.md).

## How a task is scored

Each model-harness configuration receives identical task inputs in an isolated
workspace and keeps its own prompting, tools, context management and recovery
behavior. Oracles score only the final artifacts and workspace state. A task is
divided into weighted modules for its key work requirements; a module earns its
weight only when all of its checks pass, and safety gates (protected-input
integrity, required-artifact safety, mutation scope) multiply the result. For
example, a run of task `028` (cleaning a messy sales export) that passes three
of its four modules and every gate scores 0.34 + 0.25 + 0.20 = 0.79.

## Repository map

```text
tasks/                   274 general task packages
multimodal-tasks/        26 multimodal task packages
suites/multimodal26/     runner suite root linking the 26 multimodal tasks
catalog/                 runner manifests, taxonomy, content locks and generated metadata
src/harnessbench/        grading and task-set contracts
tools/                   execution, tracing, evaluation and validation tools
docker/                  harness, OpenClaw and grader images
docs/                    maintained operational documentation
```

## Quick start

```bash
uv sync --extra dev
```

### Run an evaluation

The runner selects the 274 general tasks through `catalog/release.yaml` by default:

```bash
python tools/run_benchmark.py   --base-url "$HB_MODEL_URL"   --model gemma-4-31b-it   --model-service-id "$HB_MODEL_SERVICE_ID"   --harness openclaw   --workers 4   --temperature 1   --run-root artifacts/evaluations/standard274-shard-0
```

The 26 multimodal tasks run through the
[`suites/multimodal26`](suites/multimodal26/README.md) suite root; the harness
and grader images already contain the required frontend and browser toolchain:

```bash
python tools/run_benchmark.py   --suite-root suites/multimodal26   --base-url "$HB_MODEL_URL"   --model gemma-4-31b-it   --model-service-id "$HB_MODEL_SERVICE_ID"   --harness openclaw   --workers 4   --temperature 1   --run-root artifacts/evaluations/multimodal26-shard-0
```

Select another harness with `--harness` (`openclaw`, `antigravity`, `claude-code`,
`codex`, `hermes`, `nanobot`, `goose`, `opencode`). For production sharding and
model-service freezing, follow the
[CPU Docker + GPU vLLM runbook](docs/CPU_DOCKER_GPU_VLLM_RUNBOOK.md).

### Validate the task suites

The two suites are validated independently. General tasks:

```bash
python tools/task_maintenance/validate_archive_layout.py
python tools/task_maintenance/validate_task_suite.py   --expected-count 274 --task-set standard274
python tools/task_maintenance/verify_task_set_lock.py   --task-set standard274 --lock catalog/standard274.release-v1.lock.json
python tools/task_maintenance/validate_oracle_quality.py --require-reference

pytest -q
```

Multimodal tasks (the reference check drives Chromium through Playwright):

```bash
python tools/task_maintenance/verify_task_set_lock.py   --suite-root suites/multimodal26 --task-set multimodal26 --lock catalog/multimodal26.lock.json
pip install playwright==1.48.0 && python -m playwright install --with-deps chromium
python tools/task_maintenance/validate_multimodal_references.py --require-reference
```

## Trace and scoring contracts

The provider wrapper is the common accounting boundary for OpenClaw and the
other supported harnesses. Every trial retains `transactions.jsonl`, a
normalized `trajectory.jsonl`, and `session-summary.json`; `result.json`
references all three. See [Provider trace](docs/PROVIDER_TRACE.md).

The general suite uses deterministic outcome cases, with a bounded semantic
fallback only where a task explicitly declares one. Timeout and model/protocol
terminal failures remain in the denominator. See
[Evaluation and scoring](docs/EVALUATION_AND_SCORING.md) and
[General task suite](docs/STANDARD274.md).

## Citation

If you use Harness-Bench, please cite:

```bibtex
@article{yao2026harnessbench,
  title   = {Harness-Bench: Measuring Harness Effects across Models in Realistic Agent Workflows},
  author  = {Yao, Yilun and Tan, Xinyu and Liu, Chao-Hsuan and Li, Yaoming and Wang, Zhengyang and Yu, Wenhan and Tan, Zhewen and Tian, Yuxuan and Zhao, Guangxiang and Sun, Lin and Zhang, Xiangzheng and Yang, Tong},
  journal = {arXiv preprint arXiv:2605.27922},
  year    = {2026},
  url     = {https://arxiv.org/abs/2605.27922}
}
```
