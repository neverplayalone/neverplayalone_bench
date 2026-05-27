# neverplayalone_mcbench

Benchmark harness for **mineflayer-style Minecraft agents** — protocol-level bots that connect to a real Minecraft server, with structured world state instead of pixels.

Inspired by [MCU](https://arxiv.org/abs/2310.08367), but built for production-realistic agents instead of vision policies. Each task runs on an **ephemeral Paper server in Docker**, so every evaluation starts from a clean, reproducible world.

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

The harness is agent-agnostic: any process that can connect to a Minecraft server as a player can be benchmarked. The reference example is a Node.js mineflayer agent driven over stdio.

## Quick start

```bash
# install
pip install -e .

# bring up an ephemeral Paper server
mcbench server up

# run a task with the example agent
mcbench run --task tasks/simple/chop_oak_log.yaml \
            --agent agents_examples/random_walker

# results land in results/<run_id>/
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

Pass `--record` to capture an MP4 of the agent's POV. A sidecar Node process
joins as a second account (`RecorderCam`), spectates the agent, and writes
frames via [prismarine-viewer's headless mode](https://github.com/PrismarineJS/prismarine-viewer) + ffmpeg.

```bash
mcbench run --task tasks/simple/chop_oak_log.yaml \
            --agent agents_examples/random_walker \
            --record \
            --record-fps 20 --record-width 640 --record-height 480 \
            --record-pov first      # or `third`
```

Output lands at `results/<run_id>/recording.mp4`.

### One-time setup for recording

Recording requires `ffmpeg` plus several system libraries used by `node-canvas-webgl`:

```bash
# Debian/Ubuntu
sudo apt install -y \
  ffmpeg \
  libcairo2-dev libpango1.0-dev libjpeg-dev libgif-dev librsvg2-dev pkg-config \
  libx11-dev libxi-dev libxrandr-dev libxinerama-dev libxcursor-dev libgl1-mesa-dev

# then install the Node deps
(cd mcbench/recorder && npm install)
```

If `--record` is set but the deps are missing, the runner logs a clear message
and continues without recording — the rest of the run still proceeds and is graded.

## Status

Early scaffold. The harness boots a server, runs a task, and grades the trace end-to-end — but the task set and grader rules are deliberately small. Add tasks under `tasks/` and rules under `mcbench/grader/rules.py`.
