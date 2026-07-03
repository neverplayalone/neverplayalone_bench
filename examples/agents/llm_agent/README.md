# llm_agent

Example agent for the `resource_gathering` mission that uses an LLM to plan.
It is a baseline — a starting point to build a real miner on, not a strong agent.

## What it does

1. Connects with mineflayer and emits `ready`.
2. Sends the mission prompt (`NPABENCH_AGENT_PROMPT`) to the LLM through the
   OpenAI-compatible proxy and gets a structured plan back:
   `{"targets":[{"name":"logs","count":24,"blocks":["oak_log", ...]}]}`.
3. Gathers each target with `mineflayer-pathfinder` (find nearest matching block
   → walk → equip the best tool → dig → pick up the drop), reusing the same
   primitives as `log_gatherer`.
4. Returns near spawn and emits `done`.

If the proxy isn't available (or the call errors), it falls back to a small
keyword parser so it still runs.

## Contract (shared by all agents)

The runtime mounts the agent read-only at `/agent` and runs `node index.js` in a
network-isolated container. `mineflayer` and `mineflayer-pathfinder` are baked
into the image — you cannot add other npm deps. Node's global `fetch` is
available for LLM calls.

Env: `NPABENCH_HOST`, `NPABENCH_PORT`, `NPABENCH_AGENT_USERNAME`,
`NPABENCH_AGENT_PROMPT`, `NPABENCH_TIMEOUT_SECONDS`, and — when the validator
enables the proxy — `OPENAI_BASE_URL` / `OPENAI_API_KEY` (mirrored as
`OPENROUTER_*`).

Output: one JSON trace event per line on stdout. Emit `ready` once spawned
(**required** — no `ready` ⇒ zero score) and `done` when finished (ends the run;
emit it after returning to spawn). Scoring reads your **real inventory via RCON**
and multiplies by a distance-from-spawn factor, so you must actually gather and
come back.

## Choose the model

The agent picks its own model (`AGENT_LLM_MODEL`, default `openai/gpt-4o-mini`).
It must be on the validator's allowlist (`NPA_PROXY_ALLOWED_MODELS`) or the proxy
returns a 403. Mind the per-run spend cap — this example makes a single call.

## Run locally

```bash
(cd examples/agents/llm_agent && npm install)   # for local dev only; the image bakes deps

# give the agent an LLM endpoint for local runs (host mode inherits your env):
export OPENAI_BASE_URL=https://openrouter.ai/api/v1
export OPENAI_API_KEY=sk-or-...
export AGENT_LLM_MODEL=openai/gpt-4o-mini

npabench run llm_agent=examples/agents/llm_agent --mission resource_gathering --seed 42 --no-sandbox
```

Without an LLM endpoint it falls back to the heuristic plan.

## Ideas to improve it

- Craft tools (planks → sticks → pickaxe) so you can mine stone for cobblestone.
- Hunt passive mobs for `raw meat` targets (this baseline only mines blocks).
- Let the LLM make step-by-step decisions, not just one up-front plan.
- Smarter routing to raise the distance-from-spawn multiplier.
