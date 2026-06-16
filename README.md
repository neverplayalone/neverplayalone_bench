# Never Play Alone MCBench

Harness-oriented Minecraft resource-gathering benchmark for protocol agents.

The benchmark generates one shared resource-gathering task instance, builds one
canonical world template, copies that world into isolated Docker slots, and runs
one agent per slot. Each agent receives the same natural-language task, same
world state, same spawn state, and same time limit.

## Quick Start

```bash
pip install -e .
(cd assets/recorder-sidecar && npm install)
(cd examples/agents/log_gatherer && npm install)

mcbench run examples/agents/log_gatherer \
  --task resource_gathering_v1 \
  --seed 42 \
  --record
```

Multiple agents can be evaluated in parallel by passing multiple positional
agent assignments:

```bash
mcbench run agent_a=/path/to/agent_a agent_b=/path/to/agent_b \
  --task resource_gathering_v1 \
  --seed 42 \
  --record
```

Agents run in sandboxed Docker containers by default. Use `--normal` only for
trusted local development, where agents run directly as host subprocesses.

## Task Instance Model

Each task bundles a default config file with its code at
`mcbench/tasks/<task>/configs/default.yaml`. It holds the run settings (version,
memory, duration, world_size, difficulty, kit, scoring) and the instance
`catalog` — the menu of resource targets the seed picks from. The default
starter kit intentionally uses unenchanted netherite tools, keeping
Mineflayer/prismarine agents compatible with Minecraft 1.21 item metadata while
still giving every agent strong baseline tools.

The `catalog` section lists the selectable resources:

```yaml
catalog:
  resources:
    logs:
      biome: minecraft:forest
      items: [oak_log, birch_log, spruce_log]
      target_range: [100, 150]
      points: 100
```

For each evaluation batch, the harness derives one deterministic generated
instance from the catalog and seed. Example:

```json
{
  "resource": "logs",
  "target_count": 64,
  "goal": "Before sunset, gather 64 logs. Keep the items in your inventory and finish within 20 blocks of spawn."
}
```

Only resources in the agent's inventory at the end are counted. The score is the
resource score scaled by a distance multiplier based on how close the agent ends
to spawn:

```text
resource_score = min(inventory_count, target_count) / target_count * points
score          = resource_score * distance_multiplier
```

The distance multiplier is `1.0` within 10 blocks of spawn and steps down by band
to a `0.20` floor beyond 2000 blocks (configurable via `scoring.distance_bands`).
Time to finish is not scored; it is reported as `time_efficiency` only to break
ties between equal scores.

## Outputs

Batch outputs are written under `results/resource_gathering/batches/<instance_id>/`:

- `generated_instance.json`
- `batch_report.json`
- `world_template/`
- `agents/<agent>__slot<N>/score.json`
- `agents/<agent>__slot<N>/trace.json`
- `agents/<agent>__slot<N>/recording.mcpr` when `--record` is enabled

## Recording

Recording uses a sidecar Mineflayer process that joins as `RecorderCam`,
spectates the agent, captures the Minecraft protocol stream, and exports a
ReplayMod-compatible `.mcpr` file.

To regenerate a ReplayMod file from a packet log:

```bash
mcbench replay export-mcpr results/<run_id>/packets.jsonl.gz
```

## Repository Layout

The package is layered so dependencies only point downward — `core` ← `infra`
← `engine`, with `tasks` plugging into `core`/`infra`. Adding a task means adding
a folder under `tasks/` that implements `Task`; nothing in the engine changes.

```text
mcbench/                   Python package
  cli.py                   CLI (run <agent> --task <id>, replay)
  paths.py                 Filesystem locations
  core/                    Kernel: contracts + data models (depends on nothing internal)
    task.py                Task ABC + shared RunConfig / KitItem
    trace.py               Trace + final-state models
    slot.py                Slot + ServerConfig (run addressing)
  infra/                   Capabilities the engine drives (depend only on core)
    minecraft/             Server interaction: rcon, server, commands, spawn, container
    agents/                Agent execution adapters (subprocess + Docker sandbox)
    recording/             Recorder wrapper + ReplayMod export
  engine/                  Orchestration: runner, batch, registry (depends on core + infra)
  tasks/
    resource_gathering/    v1 task plugin
      plugin.py            Task hook implementation
      config_schema.py     Config models and validation
      instance.py          Deterministic generated task instance
      environment.py       World + agent setup
      capture.py           Final-state capture
      scoring.py           Score calculation
      configs/default.yaml Bundled default config
examples/
  agents/                  Reference agents (log_gatherer, tree_feller, random_breaker)
assets/
  recorder-sidecar/        Node packet-recorder sidecar (non-Python, runs on host)
docker/                    Paper server config + Docker agent runtime
```
