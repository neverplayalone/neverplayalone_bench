import random

from pydantic import Field

from npabench.missions.base import Task

from .config_schema import (
    CROP_ITEMS,
    SPECIES,
    AnimalHusbandryConfig,
    AnimalTarget,
    CropCount,
    CropItem,
)


class AnimalTask(Task):
    generator_version: str = "4.1.0"
    schema_version: int = 5
    starter_crops: dict[CropItem, CropCount]
    targets: list[AnimalTarget] = Field(default_factory=list)


def generate_task(config: AnimalHusbandryConfig, seed: int, task_id: str | None = None):
    starter_crops = {
        item: random.Random(f"animal-crop-counts-v1:{seed}:{item}").randint(1, 4)
        for item in CROP_ITEMS
    }
    chosen = random.Random(f"animal-targets-v1:{seed}").sample(sorted(SPECIES), 2)
    targets = [
        AnimalTarget(
            key=s,
            display_name=s.replace("_", " ").title(),
            target_count=random.Random(f"animal-counts-v1:{seed}:{s}").randint(
                *config.goal_count_ranges[s]
            ),
        )
        for s in chosen
    ]
    goals = "; ".join(
        f"{t.target_count} raised adult {t.key} offspring (50 points)" for t in targets
    )
    prompt = (
        f"Grow crops and establish a herd: {goals}. You have a hard limit of {config.duration_seconds} seconds (20 minutes). "
        "After ready, a double chest containing independently randomized quantities (1-4 each) "
        "of wheat seeds, carrots, potatoes and beetroot seeds, plus 128 oak fences and 8 oak fence gates "
        "appears two blocks east of your setup spawn. No farm, leveled terrain, tools or soil "
        "are supplied. No harvested wheat, beetroot or bulk animal feed is provided. "
        "Inspect the chest and decide how much to feed directly, plant, and reserve for replanting. "
        "Plant, grow, harvest and replant crops to increase your food supply as needed. "
        "Plan crop growth alongside construction; "
        "natural growth is not accelerated (random_tick_speed=3). Find water and gather "
        "materials to craft farming tools yourself. Choose suitable land, clear or level it "
        "yourself, and build your enclosure. Crop fields must be OUTSIDE the animal enclosure: "
        "tilled farmland, irrigation water and crops fail its solid-floor/standing-space checks. "
        "Crops receive no separate points; use harvested food to breed and grow animals. "
        f"The interior must be rectangular and axis-aligned, at least {config.min_farm_size} by "
        f"{config.min_farm_size} and at most {config.max_farm_size} by {config.max_farm_size} blocks, "
        "excluding the perimeter. All floor cells, including beneath the perimeter, must be full "
        "solid cubes at one height. Interior standing space must be walkable for two blocks above "
        "the floor: no solid obstructions, liquids or hazardous plants. Build every perimeter "
        "fence/gate yourself with no gaps; gates must be closed and aligned with their side. "
        "Corner gates are not allowed. Fence connections must be continuous. "
        "Register with /npafarm register X Y Z WIDTH DEPTH, where X,Z are the northwest FENCE "
        "corner and Y is the fence base/animal feet level (floor blocks are at Y-1). WIDTH and "
        "DEPTH are interior dimensions. Stand within 128 blocks of the center and visit the site "
        "so its chunks are loaded. The server replies NPA_FARM with validity and issue details. "
        "The first valid enclosure is locked for this run and receives two starter babies per "
        "selected species exactly once. /npafarm status rechecks it. Keep it valid until completion: "
        "invalid final enclosure means ZERO total points even if offspring were raised. "
        "Choose the right food and handling methods yourself. Raise the supplied babies, breed "
        "same-species parents inside, then raise their offspring. Starters never earn points. "
        "Only offspring born and first observed adult inside, still alive/adult/inside at the "
        "final snapshot count. The scored interior excludes the perimeter and uses half-open "
        "bounds from Y through Y+16. Each species has linear partial credit capped at 50. "
        "Wild animals, egg-hatched chickens and hybrids do not count; extra animals cannot "
        "replace the other target. No world-seed filtering or automatic land preparation is used. "
        "Emit done when finished; otherwise the task ends at timeout."
    )
    target_suffix = "_".join(f"{t.key}{t.target_count}" for t in targets)
    return AnimalTask(
        task_id=task_id or f"animal_husbandry_v4_1_{seed}_{target_suffix}",
        seed=seed,
        minecraft_seed=random.Random(f"animal-world-v1:{seed}").getrandbits(64),
        targets=targets,
        starter_crops=starter_crops,
        prompt=prompt,
    )
