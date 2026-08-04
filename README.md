# Never Play Alone Benchmark

NPABench is a Minecraft benchmark harness for protocol agents.

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

Independent commands may also run concurrently. Each evaluation automatically
leases a disjoint host-port block and gives its Minecraft servers, Docker
networks, and sandboxed agents a unique campaign namespace. Give concurrent
commands different `--output-dir` paths; an output directory is the artifact
identity and must have a single writer. Explicit non-default ports supplied
through the Python API are preserved and remain the caller's responsibility.

For promotion evidence, compare two exact agent trees in counterbalanced AB/BA
pairs. Each seed is run in both slot orders, each completed trial is
checkpointed atomically, and rerunning the same command resumes only missing
trials:

```bash
npabench compare \
  incumbent=/path/to/extracted-incumbent \
  candidate=/path/to/extracted-candidate \
  --mission mining \
  --seed 42 \
  --seed 314 \
  --pairs-per-seed 3 \
  --output-dir results/incumbent-vs-candidate \
  --no-record
```

`comparison.json` records hashes of both exact directory trees, the fixed
schedule, every raw trial score/status/trace, pair-level normalized deltas, and
the absolute AB/BA order gap. A large order gap or direction disagreement is
reported explicitly instead of being hidden inside the mean.
`promotion_gate` keeps a complete diagnostic run separate from evidence strong
enough to support replacement: it requires at least three complete pairs,
finite successful runs, no AB/BA direction disagreement, at least an 80% pair
win rate, a positive aggregate, and no non-positive seed aggregate. One pair is
still useful for diagnosis, but can never report sufficient repeated evidence.
The command reports evidence; it never promotes or submits an agent.

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
