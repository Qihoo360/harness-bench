#!/usr/bin/env node
// One headless mixdog exec turn driven by the HarnessBench round contract.
// The controller renders the provider contract into env (model, base URL,
//
// budgets) and the entrypoint passes the prompt as the only argument; this
// launcher resolves the installed mixdog runtime, injects the benchmark
// provider config through a per-trial boundary, and exits with the turn's
// code. Model traffic flows through the per-trial ModelBudgetProxy base URL,
// so provider traces are recorded by the trusted controller.
import { existsSync, readFileSync } from 'node:fs';

const MIXDOG_ROOT = '/usr/local/lib/node_modules/mixdog';
if (!existsSync(`${MIXDOG_ROOT}/package.json`)) {
  process.stderr.write(`[harness-launch] mixdog runtime is absent at ${MIXDOG_ROOT}\n`);
  process.exit(127);
}
const model = process.env.MIXDOG_MODEL;
const baseURL = process.env.MIXDOG_BASE_URL;
if (!model || !baseURL) {
  process.stderr.write('[harness-launch] MIXDOG_MODEL and MIXDOG_BASE_URL are required\n');
  process.exit(64);
}
const prompt = process.argv[2];
if (!prompt) {
  process.stderr.write('[harness-launch] one prompt argument is required\n');
  process.exit(64);
}
const contextWindow = Number(process.env.MIXDOG_CONTEXT_WINDOW || 200000);
const maxTokens = Number(process.env.MIXDOG_MAX_TOKENS || 32768);
const effort = process.env.MIXDOG_EFFORT || 'high';
const maxTurns = Number(process.env.MIXDOG_MAX_TURNS || 150);
void maxTokens; void maxTurns;

const { runHeadlessExec } = await import(`${MIXDOG_ROOT}/src/headless-exec.mjs`);
const { createPristineExecutionBoundary } = await import(
  `${MIXDOG_ROOT}/src/runtime/shared/pristine-execution.mjs`
);

const provider = 'custom-harnessbench';
const config = {
  type: 'custom', schemaVersion: 2, name: 'HarnessBench',
  protocol: 'openai-chat',
  baseURL: `${baseURL.replace(/\/+$/, '')}/`,
  auth: 'key', enabled: true, preconnect: false,
  discovery: { newModelsHidden: true },
  modelOverrides: {
    [model]: {
      manual: true, hidden: false, contextWindow,
    },
  },
};

process.env.MIXDOG_USAGE_LOG = '/trace/usage.json';
process.exitCode = await runHeadlessExec({
  provider, model, effort, json: true,
  message: prompt,
  cwd: '/workspace',
  maxTurns,
  boundaryFactory(route) {
    const boundary = createPristineExecutionBoundary(route);
    const load = boundary.loadConfig;
    boundary.loadConfig = () => {
      const initial = load();
      initial.providers[provider] = { ...config, apiKey: 'harnessbench-local' };
      return initial;
    };
    return boundary;
  },
  write() {},
  writeErr() {},
});