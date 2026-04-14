# Harness-Bench

[English](./README.md) | 简体中文

Harness-Bench 是一套面向 agent / claw 类框架的真实工作区 benchmark。它并不只看模型最后输出了一段什么文本，而是更关注：Agent 是否真的在沙盒工作区中完成了任务、是否产出了正确文件、是否遵守了约束条件，以及整个执行过程是否可追踪、可判分、可做 usage 分析。

这个仓库目前已经具备多框架 adapter、任务 hooks、oracle 判分、过程评分和 usage 统计等能力，适合做不同 agent harness 的横向对比，而不是只服务于某一个模型或某一个产品。

## 项目核心能力

Harness-Bench 提供了下面这几类核心能力：

- 在每道题独立 sandbox 中执行真实工作区任务
- 用统一 adapter 接入多个 agent / claw 框架
- 为每个任务提供本地 fixtures、prompt、hooks 和 oracle
- 同时评估结果正确性与执行过程质量
- 通过 benchmark 自带的 usage proxy 和 session 日志统计 usage
- 用统一 CLI 跑单题或整套 benchmark

它最有价值的地方在于：你可以让不同框架在相同工作区结构、相同 prompt、相同输入文件、相同评分标准下完成同一题目，再横向比较：

- 最终有没有做对
- 工具使用是否合理
- 是否遵守限制和安全边界
- token / 成本表现如何

## 当前覆盖范围

当前仓库包含 28 个任务，覆盖的能力类型比较广，包括：

- 文件操作
- Shell 命令执行
- 浏览器 / 本地 HTTP 页面访问
- 会议纪要和邮件分流
- Session memory 和多轮任务
- 图像与视觉任务
- Git / PR 工作流
- Office 文档处理
- 代码调试与修复
- 多文档综合分析
- 任务分解与规划
- Heartbeat / 长时监控
- 安全与 Prompt Injection 防御
- Provider 路由与故障切换分析
- 事故排查与 Runbook 合成

## 当前支持的适配器

当前注册表支持这些 adapter：

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

示例模型配置位于 [config/models.example.yaml]

## 仓库结构

```text
Harness-Bench/
├── config/               # 应用配置与模型配置示例
├── grading/              # 通用评分提示词与辅助逻辑
├── scripts/              # 某些框架的包装脚本
├── src/clawbench_v2/     # CLI、runner、adapter、配置加载、评分链路
└── tasks/                # 任务定义、prompt、fixtures、hooks、oracle
```

每个任务目录通常包含：

- `task.yaml`
- `prompt.txt` 或 `prompt_files`
- `fixtures/`
- `oracle_grade.py`
- 可选 `hooks.py`
- 可选 rubric 相关文件

## 安装方式

### 环境要求

- Python 3.10+
- `PyYAML>=6.0`
- 你想运行的 adapter 所依赖的对应框架 CLI 或包装脚本

推荐在本地安装这个包：

```bash
cd Harness-Bench
python3 -m pip install -e .
```

如果你不想安装，也可以继续用 `PYTHONPATH=src` 的方式直接运行。

## 配置说明

Harness-Bench 主要依赖两个顶层配置文件：

- [config/app.yaml]
- [config/models.example.yaml]

### 应用配置

`config/app.yaml` 用来定义项目级路径与默认参数，例如：

- `tasks_dir`
- `results_dir`
- `work_root`
- `default_timeout_sec`

需要注意的是：这个仓库里的 `app.yaml` 已经带有一定本地环境假设，你在正式运行前最好检查并调整 `results_dir` 和 `work_root`。

你也可以通过环境变量指定另一份 app 配置：

```bash
export CLAWBENCHV2_APP_CONFIG=/absolute/path/to/app.yaml
```

### 模型配置

`config/models.example.yaml` 定义了可运行的模型 / 框架入口。每个 model 条目通常包含：

- `adapter`
- `command`
- `user_config`
- `session_prefix`
- `timeout_sec`
- 某些 adapter 特有字段

你也可以通过环境变量切换到另一份 model 配置：

```bash
export CLAWBENCHV2_MODELS_CONFIG=/absolute/path/to/models.yaml
```

### 框架私有配置

某些 adapter 还依赖本地私有配置文件，例如：

- `config/openclaw.json`
- `config/picoclaw.json`
- `config/nullclaw.json`
- `config/zeroclaw.toml`

这些文件通常带有本机路径或 API key，所以不一定会提交到共享仓库。实际运行时需要你根据本机环境手动创建。

## 快速开始

列出全部任务：

```bash
PYTHONPATH=src python3 -m clawbench_v2.cli tasks
```

运行一个 demo 任务：

```bash
PYTHONPATH=src python3 -m clawbench_v2.cli run-task \
  --task 01-file \
  --model demo-local \
  --mode demo
```

运行一题 live 任务：

```bash
PYTHONPATH=src python3 -m clawbench_v2.cli run-task \
  --task 01-file \
  --model openclaw-local \
  --mode live
```

运行整套 benchmark：

```bash
PYTHONPATH=src python3 -m clawbench_v2.cli run-suite \
  --model openclaw-local \
  --mode live
```

从某一题开始续跑：

```bash
PYTHONPATH=src python3 -m clawbench_v2.cli run-suite \
  --model moltis-local \
  --mode live \
  --from-task 07-session-memory
```

如果想在运行后删除 sandbox：

```bash
PYTHONPATH=src python3 -m clawbench_v2.cli run-task \
  --task 01-file \
  --model demo-local \
  --mode demo \
  --delete-sandbox
```

## CLI 概览

CLI 入口位于 [src/clawbench_v2/cli.py]

当前支持的命令有：

- `tasks`
- `run-task`
- `run-suite`

常用参数包括：

- `--task`
- `--model`
- `--mode`
- `--delete-sandbox`
- `--from-task`，用于整套评测中断后续跑

`run-task` 和 `run-suite` 都会在终端打印进度与耗时，并在输出 JSON 中带上 `elapsed_sec`。

## 运行主流程

主运行逻辑在 [src/clawbench_v2/runner.py]

每次运行大致会经历：

1. 在 `work_root` 下创建一个新的 sandbox
2. 在 sandbox 中创建真实工作区 `workspace`
3. 把题目的 fixtures 拷贝进工作区
4. 渲染 prompt
5. 调用可选的 task hooks
6. 调用选定的 adapter
7. 对产物运行 oracle 判分
8. 从 usage proxy 和 / 或框架 session 日志中提取 usage
9. 运行 process rubric
10. 把结果写入结果目录

## 输出目录与结果文件

具体输出路径取决于 [config/app.yaml]中的：

- `results_dir`
- `work_root`

典型运行后的目录结构如下：

```text
<work_root>/
└── oc-bench-v2-.../       # 单次任务运行的 sandbox
    ├── workspace/         # 真实任务工作区
    ├── usage-proxy/       # 代理日志与原始响应
    └── prompt-round1.txt  # 渲染后的 prompt 快照

<results_dir>/
└── <model_id>/
    └── <task_id>.json
```

每道题的结果 JSON 通常包含：

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

这个设计的目的就是同时保留结果层证据与过程层证据，方便后续复盘和横向比较。

## 评分机制

Harness-Bench 通常会综合几类评分信号：

- oracle 的结果分
- rubric 过程分
- 合成后的 combined score

在当前实现里：

- 结果分通常来自每题自己的 `oracle_grade.py`
- 过程分通常来自 proxy trace 和 rubric 逻辑
- `combined_score` 一般会结合结果与过程两类信号

对于部分图像类或 rubric-primary 任务，过程 / 回复质量评分的权重可能会高于单纯的文件存在性校验。

## Usage 统计

在可行的情况下，benchmark 会优先通过 usage proxy 统计请求信息；如果代理链路不可用，也会尝试从框架自己的 session 日志中补采 usage。

`usage_summary` 中常见字段包括：

- `input_tokens`
- `output_tokens`
- `cache_read_tokens`
- `cache_write_tokens`
- `total_tokens`
- provider / model 相关元数据

这意味着它不仅能比较“能不能做对”，也能比较“做对的代价和效率”。

## 如何新增一个 adapter

adapter 层位于 [src/clawbench_v2/adapters]。

新增一个框架时，一般需要：

1. 在 `src/clawbench_v2/adapters/` 下新增 adapter 类
2. 在 `src/clawbench_v2/adapters/__init__.py` 里导出
3. 在 [src/clawbench_v2/registry.py]里注册
4. 在 `config/models.example.yaml` 中增加对应 model 条目
5. 视情况补充包装脚本或本地私有配置文件

## 如何新增一个任务

新增 benchmark 任务时，一般步骤是：

1. 在 `tasks/` 下创建新的任务目录
2. 编写 `task.yaml`
3. 添加 prompt 文件
4. 如果需要输入材料，放入 `fixtures/`
5. 编写 `oracle_grade.py`
6. 如有必要，编写 `hooks.py`
7. 如果过程评分需要特定逻辑，再加 rubric 相关文件

一套好的任务通常具备这些特点：

- 在真实工作区中执行
- 有明确、可检查的产物
- 能通过代码做自动审计
- 能对不同框架形成有效区分
- 足够贴近真实 agent 工作流

## 这个 benchmark 的价值

Harness-Bench 更适合拿来评测真实 agent harness 的工程能力，而不只是评测 prompt 表面效果。

和纯问答型 benchmark 相比，它更能反映：

- 工具调用稳定性
- 工作区操作纪律
- 长链路执行行为
- 受限 / 对抗场景下的安全表现
- 真实工程可用性，而不只是推理表现

## 当前仓库中的本地环境假设

这个仓库里存在一些本地化假设，后续如果你要共享或开源，建议优先检查：

- model 条目里的本地配置路径
- `config/app.yaml` 中的输出目录
- 带 API key 或私有路径的配置文件
- `data/` 下的运行结果和中间产物

## 许可证与对外发布

如果你准备对外发布，建议在正式公开前补充：

- LICENSE
- 对外 release 策略
- 哪些本地私有配置不应提交

