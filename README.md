# Never Play Alone MCBench

Validator-oriented Minecraft resource-gathering benchmark for protocol agents.

The benchmark generates one shared resource challenge, builds one canonical
world template, copies that world into isolated Docker slots, and runs one miner
agent per slot. Each miner receives the same natural-language task, same world
state, same spawn state, and same time limit.

## Quick Start

```bash
pip install -e .
(cd mcbench/recorder && npm install)
(cd agents_examples/log_gatherer && npm install)

mcbench resource-gather \
  --seed 42 \
  --agent log_gatherer=agents_examples/log_gatherer \
  --record
```

Multiple miners can be evaluated in parallel by repeating `--agent`:

```bash
mcbench resource-gather \
  --seed 42 \
  --agent miner_a=/path/to/miner_a \
  --agent miner_b=/path/to/miner_b \
  --record
```

## Challenge Model

The base runtime settings live in `resource_base.yaml`.
The default starter kit intentionally uses unenchanted netherite tools. This
keeps Mineflayer/prismarine agents compatible with Minecraft 1.21 item metadata
while still giving every miner strong baseline tools.

The resource catalog lives in `resource_catalog.yaml`:

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

Only resources in the miner inventory are counted, and they only score when the
miner finishes within 20 horizontal blocks of the selected spawn position.

Score:

```text
min(inventory_count, target_count) / target_count * points
```

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
  cli.py                   CLI
  competition.py           Single-slot resource run and scoring
  resource_batch.py        Challenge generation, world template, parallel slots
  server.py                Docker lifecycle helpers
  recorder.py              Recorder process wrapper
  replay_tool.py           Packet log to ReplayMod export
  agents/                  Agent subprocess adapter
agents_examples/
  log_gatherer/            Reference Mineflayer log-gathering miner
docker/                    Paper server compose/config
resource_base.yaml         Runtime defaults
resource_catalog.yaml      Resource categories and target ranges
```
