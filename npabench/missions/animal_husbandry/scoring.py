import time

from .config_schema import AnimalHusbandryConfig


def score_run(config: AnimalHusbandryConfig, trace, snapshot):
    spawned = trace.agent_ready_at is not None
    snapshot = snapshot or {}
    evidence = snapshot.get("animal_snapshot") or {}
    if not isinstance(evidence, dict):
        evidence = {}
    error = snapshot.get("error")
    if spawned and not error:
        if evidence.get("status") != "ok" or evidence.get("closed") is not True:
            error = "missing or invalid animal snapshot"
        elif evidence.get("targets") != sorted(t.key for t in config.targets):
            error = "animal snapshot target mismatch"
        elif evidence.get("run_id") != snapshot.get("run_id"):
            error = "animal snapshot run mismatch"
    records = evidence.get("animals", [])
    if not isinstance(records, list):
        error = "invalid animal records"
        records = []
    counts = {t.key: 0 for t in config.targets}
    starters = evidence.get("starters", [])
    if not isinstance(starters, list):
        error = "invalid starter animal evidence"
        starters = []
    starter_ids = {
        a["uuid"] for a in starters if isinstance(a, dict) and isinstance(a.get("uuid"), str)
    }
    seen = set()
    for animal in records:
        if not isinstance(animal, dict):
            error = "invalid animal record"
            continue
        key = animal.get("species")
        identity = animal.get("uuid")
        if not isinstance(identity, str) or not identity or identity in seen:
            error = "invalid or duplicate animal identity"
            continue
        seen.add(identity)
        if (
            identity not in starter_ids
            and isinstance(key, str)
            and key in counts
            and all(
                animal.get(k) is True
                for k in ("birth_inside", "matured_inside", "alive", "adult", "inside")
            )
        ):
            counts[key] += 1
    resources = []
    for t in config.targets:
        achieved = min(counts[t.key], t.target_count)
        resources.append(
            {
                "key": t.key,
                "mob": t.key,
                "display_name": t.display_name,
                "target_count": t.target_count,
                "observed": counts[t.key],
                "achieved": achieved,
                "points": 50.0 * achieved / t.target_count,
                "max_points": 50.0,
            }
        )
    animal_score = sum(t["points"] for t in resources)
    enclosure = evidence.get("enclosure", {})
    enclosure_valid = isinstance(enclosure, dict) and enclosure.get("valid") is True
    score = animal_score if spawned and not error and enclosure_valid else 0.0
    if not enclosure_valid:
        for row in resources:
            row["potential_points"] = row["points"]
            row["points"] = 0.0
    return {
        "task_id": config.id,
        "agent": trace.agent_name,
        "seed": config.seed,
        "elapsed_seconds": max(
            0.0, (trace.ended_at or time.time()) - (trace.agent_ready_at or trace.started_at)
        ),
        "score": score,
        "ranking_score": score,
        "max_score": 100.0,
        "status": "error" if error else ("ok" if spawned else "agent_never_spawned"),
        "spawned": spawned,
        "error": error,
        "resources": resources,
        "scoring_method": "crop_fed_enclosure_offspring_v4",
        "enclosure_valid": enclosure_valid,
        "enclosure": enclosure,
        "animal_score_before_gate": animal_score,
        "animal_snapshot": evidence,
        "farm_bounds": snapshot.get("farm_bounds"),
        "supply_chest": snapshot.get("supply_chest"),
        "duration_seconds": config.duration_seconds,
        "timed_out": trace.timed_out,
    }
