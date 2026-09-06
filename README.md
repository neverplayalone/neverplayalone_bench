# Never Play Alone Benchmark

NPABench is a Minecraft benchmark harness for protocol agents.

Registered missions are `resource_gathering`, `mining`, `crafting_v1`,
`crafting_v2`, `crafting_v3`, and `combat`.

The `combat` mission starts the agent empty and provides eight minutes to
gather and craft equipment, followed by seven minutes of staged, deterministic
target waves. Each seed selects one Easy, two Medium, and two Hard mob targets.
The tiers are worth 20, 35, and 45 points, respectively, for an exact maximum
score of 100. Inventory is retained after death, but each death deducts five
points, capped at a maximum 25-point penalty.

## Quick Start

```bash
pip install -e .
(cd tools/recorder && npm install)
(cd examples/agents/log_gatherer && npm install)

npabench run log_gatherer=examples/agents/log_gatherer \
  --mission resource_gathering \
  --seed 42
```

Multiple agents on the same generated task:

```bash
npabench run \
  agent_a=/path/to/agent_a \
  agent_b=/path/to/agent_b \
  --mission resource_gathering \
  --seed 42 \
  --max-parallel 2
```

Host subprocess mode for trusted local debugging:

```bash
npabench run log_gatherer=examples/agents/log_gatherer --no-sandbox
```

## Python API

```python
from npabench import AgentSpec, evaluate_single_agent

report = evaluate_single_agent(
    AgentSpec(name="log_gatherer", path="examples/agents/log_gatherer"),
    mission_id="resource_gathering",
    seed=42,
)
print(report.score, report.status)
```

## Layout

```text
npabench/
  config.py
  cli.py
  missions/
  evaluation/
  agents/
  minecraft/
  recording/
tools/
  recorder/
examples/
  agents/
tests/
  unit/
  integration/
```
