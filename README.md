# Never Play Alone Benchmark

NPABench is a Minecraft benchmark harness for protocol agents.

Registered missions are `resource_gathering`, `mining`, `crafting_v1`,
`crafting_v2`, `crafting_v3`, `combat`, `farming`, and experimental `animal_husbandry`.

The `farming` mission starts the agent with an empty inventory at the natural
spawn of a seeded world. It does not clear terrain or build farm plots. A small
barrel beside spawn contains starter crops, a stone hoe, stone pickaxe, stone axe,
crop-specific materials, and an empty bucket. A small, refillable water pool is
placed nearby. The agent
chooses where to farm. Each 20-minute task selects two easy
crops worth 20 points each and two medium crops worth 30 points each, for an
exact maximum of 100. Growth uses Minecraft's native random-tick mechanics.
Scoring uses only the final inventory count of each target item, with linear
partial credit capped at the target's points. Starter supplies and items gathered
anywhere count without subtracting initial supplies. Items left in containers or
on the ground do not count. Repeated placement and breaking does not accumulate
points: only the items held at the end matter. Oak targets count oak-log items,
and melon targets count melon blocks (not melon slices). No farming plugin or
harvest-event tracker is installed or required.

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

## Crop-fed animal husbandry (experimental, v4.1)

Status: implemented locally. Not deployed. Previous v3 agent scores do not evaluate this version.

1. **Hard time limit: 20 minutes (1,200 seconds).**
2. Start in the natural world with no prepared animal pen or crop field.
3. The chest contains **1–4 of each: wheat seeds, carrots, potatoes and beetroot seeds**, independently randomized per task, plus **128 oak fences and 8 gates**. No bulk feed, tools, water bucket or soil is provided.
4. Plant early, harvest and replant to expand food production while constructing the animal enclosure. Find water and collect materials for farming tools yourself. Native crop growth stays at `random_tick_speed=3`.
5. Build the same flat, solid-floor enclosure: at least **20×20 interior**, up to 64×64, with a continuous agent-built fence perimeter and closed, correctly aligned gates. No corner gates, holes, liquids or obstructed standing space.
6. **Crop fields go outside the animal enclosure.** Tilled farmland and irrigation water do not pass its full-solid-floor check. No crop area is prepared or registered by the benchmark.
7. Use `/npafarm register X Y Z WIDTH DEPTH`: X/Z are the northwest fence corner and Y is the fence base, one above the floor. The first valid enclosure is locked and receives two baby starters per target species, once.
8. Raise starters, breed animals inside, and grow offspring using the food you produce. Choose appropriate foods yourself.

## Targets and scoring

Two distinct targets are chosen from **cow, sheep, pig, chicken and rabbit**. Each gets a randomized goal of **2–4 qualifying adult offspring**, worth up to 50 points. The first crop-fed version uses these five species; the previous ten-species pool is not sampled.

Only same-species offspring born inside with both parents inside, first becoming adult inside, and still alive, adult and inside at completion score. Starters never score. The scored interior excludes fences and uses half-open bounds from Y to Y+16.

`score = 0` if the final enclosure is invalid. Otherwise sum `50 × min(qualifying adults / goal, 1)` for both targets. Crops give no separate points and there is no extra harvest-score gate.

The agent inspects its chest and decides what to feed directly, plant, or save for replanting. Each crop quantity is independently sampled from 1–4 using the task seed, stored in the task manifest, and copied into the mission config. Every agent in the same task receives identical quantities. Sampling does not change the world seed, animal pair or offspring goals. Direct feeding is allowed; no harvest or provenance gate is added. Normal gathering remains allowed.

## Validation and compatibility

Task generator version is 4.1.0, task schema is 5, the tracker plugin remains 4.0.0, and generated task IDs include `v4_1` to distinguish these rules from old food-rich runs. Existing v3 replays and the v4.0.2 husbandry agent are historical baselines, not evaluations of this combined mission. Agent v6.8.0 scored **50/100 on task seed 1** in 1,060.4 seconds with the unchanged v4.1 mission: two qualifying adult chicken offspring, zero sheep offspring, a valid 20×20 enclosure, and no movement violations. Six crops were planted, but none harvested; scoring food came from normal seed foraging. This is a single-seed result, not a multi-seed success rate. Replay: `Agents/evaluations/crop-fed-husbandry-seed1-score50.mcpr`; evidence: `Agents/evaluations/crop-fed-husbandry-winning-summary.json`.

Rebuild with JDK 21+: `bash npabench/missions/animal_husbandry/plugin/build.sh /path/to/paper-data`.

Run: `npabench run my_agent=/path/to/agent --mission animal_husbandry --seed 10`.

Previous v4.0 validation: **49 tests passed** (34 combined-mission tests and 15 standalone-farming regressions), plus the live starter-supply/tick-rate check and 15 live enclosure checks. Evidence: `Agents/evaluations/crop-fed-husbandry-v4-fixtures/validation_summary.json`. These controlled fixtures are not a full agent evaluation.

Current v4.1 validation: **56 tests passed** (41 animal-husbandry tests and 15 farming regressions), including deterministic per-task crop quantities, all values 1–4, rejection of out-of-range counts, and matching chest setup. The full recorded agent evaluation above is separate from these fixture tests.
