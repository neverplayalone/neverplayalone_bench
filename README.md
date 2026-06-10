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

Each competition's config lives under `configs/<competition>/`. For resource
gathering the base runtime settings are `configs/resource_gathering/base.yaml`.
The default starter kit intentionally uses unenchanted netherite tools. This
keeps Mineflayer/prismarine agents compatible with Minecraft 1.21 item metadata
while still giving every miner strong baseline tools.

The resource catalog lives in `configs/resource_gathering/catalog.yaml`:

```yaml
resources:
  logs:
    items: [oak_log, birch_log, spruce_log]
    target_range: [16, 128]
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

```text
mcbench/                   Python package
  cli.py                   CLI (run --competition <id>, replay)
  registry.py              Competition registry (id -> config)
  config.py  paths.py      YAML loaders / filesystem locations
  runner.py                Single-slot run loop
  batch.py                 Challenge generation, world template, parallel slots
  scoring.py               Resource * distance-multiplier scoring
  slot.py  container.py     Slot definition / Docker container lifecycle
  models/                  Pydantic models (competition, challenge, trace)
  minecraft/               Server interaction (rcon, server, world, commands)
  recording/               Recorder wrapper, ReplayMod export, Node sidecar/
  agents/                  Agent subprocess adapter
configs/
  resource_gathering/      base.yaml (runtime defaults) + catalog.yaml
agents_examples/
  log_gatherer/            Reference Mineflayer log-gathering miner
docker/                    Paper server config (bukkit.yml)
```
