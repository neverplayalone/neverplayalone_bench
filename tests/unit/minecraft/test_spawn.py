from __future__ import annotations

from npabench.minecraft.spawn import set_exact_spawn, use_world_spawn


class FakeRcon:
    def __init__(self) -> None:
        self.commands: list[str] = []

    def command(self, command: str) -> str:
        self.commands.append(command)
        if command == "data get entity npabench_agent Pos":
            return "npabench_agent has the following entity data: [12.3d, 72.0d, -8.8d]"
        return ""


def test_set_exact_spawn_issues_expected_commands() -> None:
    rcon = FakeRcon()
    spawn = set_exact_spawn(rcon, "npabench_agent", 12, 72, -8)

    assert spawn == (12, 72, -8)
    assert rcon.commands == [
        "gamerule respawn_radius 0",
        "setworldspawn 12 72 -8",
        "spawnpoint npabench_agent 12 72 -8",
        "tp npabench_agent 12.5 72 -7.5 0 0",
    ]


def test_use_world_spawn_reads_player_position() -> None:
    rcon = FakeRcon()
    spawn = use_world_spawn(rcon, "npabench_agent")

    assert spawn == (12, 72, -8)
    assert "data get entity npabench_agent Pos" in rcon.commands
