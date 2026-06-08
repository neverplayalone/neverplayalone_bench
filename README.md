# MineCraft Benchmark

**A reproducible Minecraft benchmark harness for evaluating autonomous agents in real server environments.**

Never Play Alone MCBench runs task-based evaluations for Minecraft agents that connect through the normal game protocol. It is designed for **mineflayer-style agents**, scripted bots, and LLM-driven agents that act in a real Minecraft server using structured state instead of pixels.

Each run starts an **ephemeral Paper server in Docker**, initializes the world with task-specific commands, launches the agent, records a structured trace, and grades the result with deterministic rules or an optional LLM rubric.

Inspired by [MCU](https://arxiv.org/abs/2310.08367), but focused on production-realistic protocol agents rather than vision policies.

## Architecture

```
┌────────────────────────────────────────────────────────────┐
│  mcbench (Python)                                          │
│  ┌──────────┐  ┌───────────┐  ┌─────────┐  ┌────────────┐  │
│  │ Config   │→ │ Server    │→ │ Runner  │→ │ Grader     │  │
│  │ (YAML)   │  │ (Docker)  │  │ (Agent) │  │ (rule/LLM) │  │
│  └──────────┘  └───────────┘  └─────────┘  └────────────┘  │
└─────────────────────┬──────────────────────────────────────┘
                      │ RCON (init) / Protocol (agent)
                      ▼
        ┌──────────────────────────────┐
        │  Paper server (itzg/mc image)│
        └──────────────────────────────┘
                      ▲
                      │ mineflayer protocol
                      │
              ┌───────────────┐
              │  Agent (any   │   (Node.js mineflayer, Python bot,
              │   substrate)  │    LLM-driven, scripted, …)
              └───────────────┘
```

The harness is agent-agnostic: any process that can connect to a Minecraft server as a player can be benchmarked. The reference example is a Node.js mineflayer log-gathering agent driven over stdio.

## Quick start

```bash
# install
pip install -e .

# run a generated resource-gathering challenge with the example agent
mcbench resource-gather \
            --seed 42 \
            --agent log_gatherer=agents_examples/log_gatherer

# results land in results/resource_gathering/
```

## Task format

```yaml
# tasks/simple/chop_oak_log.yaml
id: chop_oak_log
difficulty: simple
goal: "Chop 5 oak logs and put them in your inventory."

setup:
  world: flat
  commands:
    - /give @p iron_axe 1
    - /setblock ~5 ~ ~ oak_log
    - /fill ~3 ~ ~3 ~8 ~3 ~8 oak_log

timeout_seconds: 120

success:
  rules:
    - kind: inventory_contains
      item: oak_log
      min_count: 5
```

For subjective tasks (e.g., "build a nice shelter"), set `success.llm_rubric` and the grader will call Claude with the trace.

## Repository layout

```
neverplayalone_mcbench/
├── mcbench/                  # Python package
│   ├── cli.py                # `mcbench` CLI
│   ├── config.py             # task YAML loader
│   ├── server.py             # Docker lifecycle
│   ├── rcon.py               # RCON wrapper
│   ├── trace.py              # trace schema
│   ├── runner.py             # orchestrate a task run
│   ├── agents/               # agent adapters
│   └── grader/               # rule + LLM graders
├── docker/                   # Paper server compose file
├── tasks/                    # task YAMLs (simple/ + hard/)
├── agents_examples/          # reference mineflayer agents
└── results/                  # run outputs (gitignored)
```

## Recording (optional)

Pass `--record` to capture a ReplayMod-compatible visual replay. A sidecar Node
process joins as a second account (`RecorderCam`), spectates the agent, records
the Minecraft protocol stream, and exports it to `.mcpr`.

```bash
mcbench resource-gather \
            --seed 42 \
            --agent log_gatherer=agents_examples/log_gatherer \
            --record
```

Outputs land under `results/<run_id>/`:

- `packets.jsonl.gz`: gzip-compressed Minecraft protocol packet stream
- `packets.manifest.json`: packet-capture metadata and packet counts
- `recording.mcpr`: ReplayMod visual replay generated from the packet stream

To regenerate a ReplayMod file from a packet log:

```bash
mcbench replay export-mcpr results/<run_id>/packets.jsonl.gz
```

Open `recording.mcpr` with ReplayMod using the same Minecraft version as the
recording.

### One-time setup for recording

Recording only requires the packet-recorder Node dependencies:

```bash
(cd mcbench/recorder && npm install)
```

If `--record` is set but the deps are missing, the runner logs a clear message
and continues without recording — the rest of the run still proceeds and is graded.

## Status

Early scaffold. The harness boots a server, runs a task, and grades the trace end-to-end — but the task set and grader rules are deliberately small. Add tasks under `tasks/` and rules under `mcbench/grader/rules.py`.
