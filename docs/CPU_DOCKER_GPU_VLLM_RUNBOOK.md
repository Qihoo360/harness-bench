# CPU Docker + GPU vLLM runbook

## Roles

- A coordinator machine edits the repository and assigns shards.
- CPU workers run the trusted controller, the Dockerized harnesses, workspace
  staging, and the offline grader.
- GPU jobs only serve frozen local-model vLLM endpoints. They never mount tasks,
  workspaces, tests, or oracle material. External API models need no GPU job and
  run from the same CPU controller/harness path.

Node availability and IPs are runtime facts. Never reuse a historical endpoint
list without rechecking it.

## CPU worker preparation

```bash
ssh "$HB_WORKER"

repo=/path/to/harnessbench           # a checkout readable by the worker
controller_env=/var/tmp/harnessbench-controller-$USER

python3 -m venv "$controller_env"
"$controller_env/bin/pip" install 'PyYAML>=6.0' 'tomli>=2.0.1'
export PYTHONPATH="$repo/src:$repo/tools:$repo/tools/task_maintenance"
cd "$repo"
```

Verify Docker, free disk, repository readability, and a worker-owned run root.
Build or verify current OpenClaw, grader, and stager images from current source.
Run the isolation validator and a Docker reference grade. Formal grader runtime
must remain `network=none`.

Harness images are source-hash pinned and rebuilt automatically when their
Dockerfile or entrypoint changes. If the worker needs a proxy only during image
builds, opt in explicitly with `HARNESSBENCH_DOCKER_BUILD_PROXY`; it is passed
as Docker build arguments and is not injected into evaluation containers.

For repeated model-by-harness matrices, build each harness image once, save it
to a shared evaluation cache with `docker save`, record its SHA-256, and load
that archive on each assigned worker. Rebuild only when the pinned harness
version, Dockerfile, entrypoint, or source hash changes. Task-specific image
layers remain per-task inputs and must not be replaced by a shared catch-all
image.

## Qualify and pool CPU workers

Do not treat a successful SSH login or `docker version` as worker readiness.
Before assigning a shard, record a fresh worker matrix containing:

- root-filesystem utilization and free bytes; exclude a worker at or above 85%;
- a disposable default-bridge container run, not just a Docker daemon query;
- a GET probe from the worker to the selected frozen model endpoint;
- controller-environment readability and current runtime/image identities; and
- active HarnessBench containers and unique run-root ownership.

On a shared worker, assign the pilot pool a dedicated grader tag and export
`HARNESSBENCH_GRADER_IMAGE` to that tag. This prevents another job from
rebuilding the mutable `harnessbench/grader:local` tag between controller
startup and grading; record the resolved image ID as usual.

An auxiliary HTTP proxy is not part of the benchmark identity and must not be
assumed available. Use only frozen model-service endpoints that pass the
worker-originated probe. Exclude a worker with a missing `docker0`, a failed
container probe, insufficient disk, or a stale/mismatched image rather than
silently retrying it as a model failure.

Keep one unique run root per worker and shard. Cap aggregate concurrency at the
level validated by the model-service probe, counting requests from every CPU
worker. Task authoring and deterministic local validation may proceed in
parallel with diagnostic model pilots, but write a task-set lock only after
pilot-driven edits settle.

## Freeze and probe GPU identity

Do not identify a service by IP alone. After vLLM is healthy, freeze its model,
config, chat-template, launcher, and runtime identity. Then run the health,
version, and two-turn tool-call probes from the same CPU worker that will run
Docker:

```bash
python tools/freeze_model_service.py \
  --submit-manifest "$SERVE_ROOT/submit_manifest.json" \
  --endpoints-dir "$SERVE_ROOT/endpoints" \
  --logs-dir "$SERVE_ROOT/logs" \
  --pod-path-prefix "$POD_ROOT" \
  --host-path-prefix "$HOST_ROOT" \
  --expected-replicas "$REPLICA_COUNT" \
  --expected-vllm-version 0.24.0 \
  --output "$SERVICE_DIR/model_service_manifest.json"

env NO_PROXY='*' no_proxy='*' python tools/probe_model_service.py \
  --manifest "$SERVICE_DIR/model_service_manifest.json" \
  --expected-version 0.24.0 \
  --timeout 600 probe \
  --per-rank-concurrency 1 \
  --output "$SERVICE_DIR/probe-cpu.json"
```

Only endpoints passing health, models, version, and tool protocol enter a new
pool manifest. Interrupted jobs and old manifests are offline history.

## Reasoning and thinking policy

Treat model-side reasoning and harness-side thinking as separate experimental
controls. Do not describe one as evidence for the other.

- Local vLLM services must freeze `enable_thinking` and the reasoning parser in
  the model-service manifest. The current Gemma 4 and Qwen 3.8 formal services
  use `enable_thinking=true` with their model-specific reasoning parsers.
- Formal HarnessBench runs use `--thinking high`, which is also the controller
  CLI default. OpenClaw receives it directly; Claude Code receives it as
  `CLAUDE_CODE_EFFORT_LEVEL`; Codex, Hermes, Nanobot, and OpenCode receive it
  as their reasoning-effort setting. Goose does not consume this field,
  although it remains part of the frozen run identity.
- The controller proxy enforces temperature and output/turn budgets but does
  not independently add a provider-specific reasoning field. A harness may
  still emit `reasoning_effort`, `thinking`, `reasoning`, or `output_config`
  according to its wire protocol; provider traces are the authority for what
  was actually requested.
- Historical `--thinking default` means that the historical harness/controller
  chose its native default. It must not be reinterpreted as `low`. Every formal
  launcher should still pass `--thinking high` explicitly.

Provider defaults are metadata, not a portable benchmark setting. As checked
on 2026-09-11, the official DeepSeek V4 documentation says thinking is enabled
by default and its default effort is `high`. The official Zhipu documentation
for GLM 5.2 and later says thinking is enabled by default and the default effort
is `max`. The official OpenAI model pages say GPT-5.6 Luna and GPT-5.6 Terra
default to `medium`. These statements describe the official endpoints:

- <https://api-docs.deepseek.com/guides/thinking_mode/>
- <https://docs.bigmodel.cn/cn/guide/capabilities/thinking>
- <https://developers.openai.com/api/docs/models/gpt-5.6-luna>
- <https://developers.openai.com/api/docs/models/gpt-5.6-terra>

A third-party gateway may route or normalize requests across different upstream
providers and may not publish a gateway-level default effort. Therefore record
three separate fields for every formal API experiment:

1. the harness setting, currently `high`;
2. the exact reasoning-related fields observed in the provider request; and
3. the returned model/provider identity and any observable reasoning-token or
   reasoning-content metadata.

If no explicit field is sent, record `provider default (unverified through
gateway)` rather than guessing `low`, `high`, or `max`. If `high` is sent but
the gateway returns no reasoning metadata, record `high requested; effective
upstream level not observable`. Never infer the actual effort merely from
latency, answer length, or benchmark score.

## External OpenAI-compatible API

Use the provider's API root as `--base-url`, not the full chat-completions URL
(for example `https://api.example.com/v1`); the wrapper selects
`/chat/completions`, `/responses`, or `/messages` according to the harness
protocol. Before a full matrix, verify all three protocol shapes with a tool-call
canary and then run task `003-git-pr-merge` once through every harness.

Keep credentials outside the repository and evaluation root in a mode-0600
file. Mount that file read-only into the trusted controller and pass only its
container path:

```bash
python tools/run_benchmark.py \
  --base-url "$HB_API_BASE_URL" \
  --model "$HB_API_MODEL" \
  --model-service-id "$HB_API_SERVICE_ID" \
  --upstream-api-key-file /run/secrets/provider.key \
  --harness "$HB_HARNESS" \
  --workers 2 \
  --thinking high \
  --temperature 1 \
  --context-window 200000 \
  --max-agent-turns 150 \
  --max-tokens 65536 \
  --task-set standard274 \
  --run-root "$HB_RUN_ROOT"
```

Never put the credential value in a launcher, environment dump, service
manifest, provider trace, diagnostic log, or Git. The controller replaces the
harness placeholder authorization headers immediately before forwarding a
request; recorded traces remain credential-free. API runs use CPU workers only
and should reuse the same frozen controller, runtime, and harness archives as
local-model runs.

## Docker MTU check

Some clusters run host interfaces at MTU 1450 while the Docker bridge uses
1500. Small health calls can pass while larger responses hang. Confirm the
actual bridge subnet and the GPU subnet first. If the mismatch exists, narrowly
clamp only Docker-source traffic to the GPU subnet:

```bash
docker_source=172.17.0.0/16   # inspect each host; some differ
gpu_subnet="$HB_GPU_SUBNET"    # the subnet that serves the vLLM endpoints
comment=harnessbench-vllm-mtu1450

sudo -n iptables -t mangle -C FORWARD \
  -s "$docker_source" -d "$gpu_subnet" -o eth0 -p tcp \
  --tcp-flags SYN,RST SYN -m comment --comment "$comment" \
  -j TCPMSS --set-mss 1410 2>/dev/null || \
sudo -n iptables -t mangle -I FORWARD 1 \
  -s "$docker_source" -d "$gpu_subnet" -o eth0 -p tcp \
  --tcp-flags SYN,RST SYN -m comment --comment "$comment" \
  -j TCPMSS --set-mss 1410
```

This rule is not persistent. Recheck after reboot or firewall maintenance and
rerun the restricted-container probe. Do not broaden it to unrelated traffic.

## Launch the harness comparison

Run every harness against the same frozen model service, task set, and budget
(`--context-window 200000 --temperature 1 --max-agent-turns 150`). Start with a
one-task canary per harness (for example `--tasks 003-git-pr-merge`) and
proceed to the full task set only when every canary completes without
infrastructure errors. Use a distinct run root per harness and shard.

## Launch shards

Use one controller per endpoint/rank and four workers initially. For seven
single-rank endpoints, use `--shard-count 7` and indices `0..6`, with distinct
run roots:

```bash
python tools/run_benchmark.py \
  --base-url "$HB_MODEL_URL" \
  --model gemma-4-31b-it \
  --model-service-id "$HB_MODEL_SERVICE_ID" \
  --harness openclaw \
  --workers 4 \
  --shard-count 7 \
  --shard-index "$HB_SHARD_INDEX" \
  --thinking high \
  --temperature 1 \
  --context-window 200000 \
  --max-agent-turns 150 \
  --max-tokens 65536 \
  --run-root "$HB_RUN_ROOT"
```

For DP=2 behind one service, launch two controllers with ranks `0` and `1`,
disjoint shards, and distinct roots. Workers are independent HTTP requests;
vLLM continuous batching handles decode concurrency.

The 26 multimodal tasks use the same procedure with
`--suite-root suites/multimodal26`.

Before merging, validate the frozen run plan and confirm every shard has
identical suite/controller/runtime/image/grader/model-service identities.
Include terminal workspace scores for timeout/protocol/model-error rows. Clean
old containers and unused images only after resolving exact targets; never
delete the shared repository or active run roots to recover disk.
