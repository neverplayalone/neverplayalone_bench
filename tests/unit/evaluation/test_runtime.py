from __future__ import annotations

from npabench.config import DEFAULT_BASE_GAME_PORT, DEFAULT_BASE_RCON_PORT
from npabench.evaluation.runtime import reserve_evaluation_runtime


def test_concurrent_runtime_leases_have_disjoint_names_and_ports(tmp_path) -> None:
    with reserve_evaluation_runtime(
        output_dir=tmp_path / "one",
        slot_count=2,
        base_game_port=DEFAULT_BASE_GAME_PORT,
        base_rcon_port=DEFAULT_BASE_RCON_PORT,
    ) as first:
        with reserve_evaluation_runtime(
            output_dir=tmp_path / "two",
            slot_count=2,
            base_game_port=DEFAULT_BASE_GAME_PORT,
            base_rcon_port=DEFAULT_BASE_RCON_PORT,
        ) as second:
            assert first.namespace != second.namespace
            first_ports = {
                first.base_game_port,
                first.base_game_port + 1,
                first.base_rcon_port,
                first.base_rcon_port + 1,
            }
            second_ports = {
                second.base_game_port,
                second.base_game_port + 1,
                second.base_rcon_port,
                second.base_rcon_port + 1,
            }
            assert first_ports.isdisjoint(second_ports)


def test_explicit_ports_are_preserved_but_namespace_is_unique(tmp_path) -> None:
    kwargs = {
        "slot_count": 1,
        "base_game_port": 41000,
        "base_rcon_port": 42000,
    }
    with reserve_evaluation_runtime(output_dir=tmp_path / "one", **kwargs) as first:
        with reserve_evaluation_runtime(output_dir=tmp_path / "two", **kwargs) as second:
            assert first.base_game_port == second.base_game_port == 41000
            assert first.base_rcon_port == second.base_rcon_port == 42000
            assert first.namespace != second.namespace
