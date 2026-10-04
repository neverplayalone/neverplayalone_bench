# Never Play Alone Benchmark

NPABench is a Minecraft benchmark harness for protocol agents.

Registered missions are `resource_gathering`, `mining`, `crafting_v1`,
`crafting_v2`, `crafting_v3`, `combat`, and `farming`.

The `farming` mission starts the agent with an empty inventory at the natural
spawn of a seeded world. It does not clear terrain or build farm plots. A small
barrel beside spawn contains starter crops, tools, crop-specific materials, and
an empty bucket. A small, refillable water pool is placed nearby. The agent
chooses where to farm. Each 20-minute task selects two easy
crops worth 20 points each and two medium crops worth 30 points each, for an
exact maximum of 100. Growth uses Minecraft's native random-tick mechanics.
A bundled Paper listener scores mature target crops when the agent harvests
them. Naturally occurring crops count; player-placed blocks count only after
genuine growth. Repeatedly placing and breaking a starter crop never earns
credit. Starter items and immature crops do not score. Final inventory is
reported but is not used for scoring.

The `combat` mission starts the agent empty and provides ten minutes to
gather and craft equipment, followed by eight minutes of staged, deterministic
target waves (1,080 seconds total). Eight waves become eligible at 60-second
intervals: three Easy waves, three Medium waves, then two Hard waves. Each seed
selects one Easy, two Medium, and two Hard mob targets. The tiers are worth
30, 40, and 30 points, respectively, for an exact maximum score of 100.
Completing Easy and Medium earns 70 points, or 50 after two deaths. Inventory
is retained after death, but each death still deducts ten points until the
score reaches zero.

The scheduler admits new spawns only while fewer than three living, loaded
controlled wave mobs are present. Eligible spawns wait in a queue until capacity
is available; earlier survivors are not cleared. Unloaded mobs cannot be counted
by the server selector: revisiting them can temporarily exceed the limit, in
which case new spawns pause until the loaded count falls below three.
Reserve spawns replace missing kill opportunities instead of adding enemies
when enough of that target are already alive. Queued mobs left at the deadline
are reported without erasing earned partial credit. Natural hostile spawning
remains disabled. The combat world seed is generated independently of target
and wave randomization so future schedule changes can be compared on the same
world; this refactoring changes the previous seed-to-world mapping once.

Agent implementations must use the mission's preparation/combat durations and
runtime timeout; agents hardcoded to the old 480/420-second phases need updating.

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
