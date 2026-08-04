from __future__ import annotations

import pytest

from npabench.missions.base import PromptMetadata, Task, TaskTarget
from npabench.missions.resource_gathering.prompting import (
    PROMPT_SCHEMA_VERSION,
    _prompt_brief,
    materialize_task_prompt,
)


def _sample_task() -> Task:
    return Task(
        task_id="resource_1_sand_dirt",
        seed=1,
        minecraft_seed=123,
        targets=[
            TaskTarget(
                key="logs",
                display_name="logs",
                items=["oak_log", "birch_log"],
                target_count=20,
                role="essential",
                points=25,
            ),
            TaskTarget(
                key="cobblestone",
                display_name="cobblestone",
                items=["cobblestone"],
                target_count=20,
                role="essential",
                points=25,
            ),
            TaskTarget(
                key="raw_meat",
                display_name="raw meat",
                items=["beef", "porkchop"],
                target_count=7,
                role="essential",
                points=25,
            ),
            TaskTarget(
                key="sand",
                display_name="sand",
                items=["sand", "red_sand"],
                target_count=16,
                role="optional",
                points=12.5,
            ),
            TaskTarget(
                key="dirt",
                display_name="dirt",
                items=["dirt"],
                target_count=16,
                role="optional",
                points=12.5,
            ),
        ],
    )


def test_materialize_task_prompt_reuses_cached_prompt(tmp_path) -> None:
    task = _sample_task()
    cached = task.model_copy(
        update={
            "prompt": "Gather the items and come back.",
            "prompt_metadata": PromptMetadata(
                provider="openai",
                model="gpt-test",
                schema_version=PROMPT_SCHEMA_VERSION,
            ),
        }
    )
    (tmp_path / "task.json").write_text(cached.model_dump_json(indent=2))

    materialized = materialize_task_prompt(task, tmp_path)

    assert materialized.prompt == cached.prompt
    assert materialized.prompt_metadata == cached.prompt_metadata


def test_materialize_task_prompt_requires_provider_config_when_cache_missing(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("NPABENCH_PROMPT_PROVIDER", "openrouter")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("NPABENCH_PROMPT_MODEL", raising=False)

    with pytest.raises(RuntimeError, match="OPENROUTER_API_KEY"):
        materialize_task_prompt(_sample_task(), tmp_path)


def test_prompt_brief_includes_real_examples() -> None:
    brief = _prompt_brief(_sample_task())

    assert "Examples:" in brief
    assert "Collect 24 logs, 20 cobblestone, 9 raw meat, 16 sand, and 3 apples" in brief
    assert "Bring back 30 logs, 22 cobblestone, 12 raw meat, 16 kelp, and 6 flowers" in brief
    assert "Retrieve 21 logs, 28 cobblestone, 7 raw meat, 16 dirt, and 5 pumpkins" in brief
    assert "Now write a prompt for this task." in brief
    assert "time budget" not in brief
    assert "do not always start with 'Gather'" in brief
