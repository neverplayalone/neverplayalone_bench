from npabench.evaluation.run_trace import FinalAgentState
from npabench.minecraft.rcon_helpers import parse_pos, parse_scalar

from .plugin import manifest_hash, snapshot


def collect_state(rcon, config, setup_state):
    if not setup_state or "run_id" not in setup_state:
        raise RuntimeError("animal tracker was never started")
    evidence = snapshot(rcon, setup_state["run_id"])
    if evidence.get("manifest_sha256") != manifest_hash(config):
        raise RuntimeError("animal snapshot manifest mismatch")
    enclosure = evidence.get("enclosure")
    if not isinstance(enclosure, dict) or not isinstance(enclosure.get("valid"), bool):
        raise TypeError("missing enclosure validation evidence")
    if enclosure["valid"] and evidence.get("bounds") is None:
        raise RuntimeError("valid enclosure lacks registered bounds")
    starters = evidence.get("starters", [])
    if evidence.get("bounds") is not None:
        if len(starters) != 2 * config.starter_babies_per_species:
            raise RuntimeError("invalid starter identity evidence")
    elif starters:
        raise RuntimeError("starters exist without a registered enclosure")
    final = FinalAgentState(
        position=parse_pos(rcon.command(f"data get entity {config.username} Pos")),
        health=parse_scalar(rcon.command(f"data get entity {config.username} Health")),
    )
    return {
        "final_state": final,
        "animal_snapshot": evidence,
        **setup_state,
        "farm_bounds": evidence.get("bounds"),
        "starter_animals": starters,
    }
