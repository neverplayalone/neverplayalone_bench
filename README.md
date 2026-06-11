# Never Play Alone MCBench

Validator-oriented Minecraft resource-gathering benchmark for protocol agents.

The benchmark generates one shared resource challenge, builds one canonical
world template, copies that world into isolated Docker slots, and runs one miner
agent per slot. Each miner receives the same natural-language task, same world
state, same spawn state, and same time limit.

## Quick Start

```bash
pip install -e .
(cd mcbench/recording/sidecar && npm install)
(cd agents_examples/log_gatherer && npm install)

mcbench run --competition resource_gathering_v1 \
  --seed 42 \
  --agent log_gatherer=agents_examples/log_gatherer \
  --record
```

`mcbench resource-gather` is a shorthand alias for
`mcbench run --competition resource_gathering_v1`.

Multiple miners can be evaluated in parallel by repeating `--agent`:

```bash
mcbench run --competition resource_gathering_v1 \
  --seed 42 \
  --agent miner_a=/path/to/miner_a \
  --agent miner_b=/path/to/miner_b \
  --record
```

## Challenge Model

Each competition bundles a single config file with its code at
`mcbench/competitions/<competition>/configs/config.yaml`. It holds the run
settings (version, memory, duration, world_size, difficulty, kit, scoring) and
the challenge `catalog` — the menu of tasks the seed picks from. The default
starter kit intentionally uses unenchanted netherite tools, keeping
Mineflayer/prismarine agents compatible with Minecraft 1.21 item metadata while
still giving every miner strong baseline tools.

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

For each evaluation batch, the validator derives a deterministic generated
challenge from the catalog and seed. Example:

```json
{
  "resource": "logs",
  "target_count": 64,
  "goal": "Before sunset, gather 64 logs. Keep the items in your inventory and finish within 20 blocks of spawn."
}
```

Only resources in the miner's inventory at the end are counted. The score is the
resource score scaled by a distance multiplier based on how close the miner ends
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

Batch outputs are written under `results/resource_gathering/batches/<challenge_id>/`:

- `generated_challenge.json`
- `batch_report.json`
- `world_template/`
- `miners/<miner>__slot<N>/score.json`
- `miners/<miner>__slot<N>/trace.json`
- `miners/<miner>__slot<N>/recording.mcpr` when `--record` is enabled

## Recording

Recording uses a sidecar Mineflayer process that joins as `RecorderCam`,
spectates the miner, captures the Minecraft protocol stream, and exports a
ReplayMod-compatible `.mcpr` file.

To regenerate a ReplayMod file from a packet log:

```bash
mcbench replay export-mcpr results/<run_id>/packets.jsonl.gz
```

## Repository Layout

The package is split into a generic engine (`core/`) and one self-contained
plugin per competition (`competitions/<name>/`). Adding a competition = drop in
a new folder implementing `Competition`; the engine needs no changes.

```text
mcbench/                   Python package
  cli.py                   CLI (run --competition <id>, replay)
  registry.py              Competition registry (id -> Competition)
  paths.py                 Filesystem locations
  core/                    Generic engine (competition-agnostic)
    competition.py         Competition ABC + shared RunConfig / KitItem
    runner.py              Single-slot run loop (drives a Competition)
    batch.py               World template + parallel slots
    slot.py  container.py  Slot definition / Docker container lifecycle
    trace.py               Trace + final-state models
  minecraft/               Server interaction (rcon, server, world, commands)
  recording/               Recorder wrapper, ReplayMod export, Node sidecar/
  agents/                  Agent subprocess adapter
  competitions/
    resource_gathering/    v1 plugin: competition, config, challenge,
      configs/             scoring, world setup, + bundled config.yaml
agents_examples/
  log_gatherer/            Reference Mineflayer log-gathering miner
docker/                    Paper server config (bukkit.yml)
```
