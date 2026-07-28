from __future__ import annotations

from npabench.missions.base import Mission
from npabench.missions.crafting import CraftingMission
from npabench.missions.mining import MiningMission
from npabench.missions.resource_gathering import ResourceGatheringMission

_MISSIONS: list[Mission] = [
    ResourceGatheringMission(),
    MiningMission(),
    CraftingMission(),
]

MISSIONS: dict[str, Mission] = {mission.id: mission for mission in _MISSIONS}


def get_mission(mission_id: str) -> Mission:
    try:
        return MISSIONS[mission_id]
    except KeyError:
        known = ", ".join(sorted(MISSIONS))
        raise ValueError(f"unknown mission {mission_id!r}; known missions: {known}") from None
