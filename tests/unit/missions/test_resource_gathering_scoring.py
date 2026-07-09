from __future__ import annotations

from npabench.evaluation.run_trace import AgentRunTrace, FinalAgentState, TraceEvent
from npabench.missions.resource_gathering.config_schema import ResourceGatheringMissionConfig, ResourceSpec
from npabench.missions.resource_gathering.scoring import score_resource_gathering_run


def test_multi_target_scoring_uses_fixed_weights_and_max_score_100() -> None:
    mission_config = ResourceGatheringMissionConfig(
        id="resource_1_sand_dirt",
        resources=[
            ResourceSpec(
                item="logs",
                items=["oak_log", "birch_log"],
                display_name="logs",
                target_count=20,
                points=25,
                role="essential",
            ),
            ResourceSpec(
                item="cobblestone",
                items=["cobblestone"],
                display_name="cobblestone",
                target_count=20,
                points=25,
                role="essential",
            ),
            ResourceSpec(
                item="raw_meat",
                items=["beef", "porkchop"],
                display_name="raw meat",
                target_count=10,
                points=25,
                role="essential",
            ),
            ResourceSpec(
                item="sand",
                items=["sand", "red_sand"],
                display_name="sand",
                target_count=10,
                points=12.5,
                role="optional",
            ),
            ResourceSpec(
                item="dirt",
                items=["dirt"],
                display_name="dirt",
                target_count=10,
                points=12.5,
                role="optional",
            ),
        ],
    )
    trace = AgentRunTrace(
        task_id="resource_1_sand_dirt",
        agent_name="agent",
        started_at=0,
        agent_ready_at=1,
        ended_at=10,
        final_state=FinalAgentState(
            inventory={
                "logs": 20,
                "cobblestone": 10,
                "raw_meat": 5,
                "sand": 10,
                "dirt": 0,
            }
        ),
    )

    report = score_resource_gathering_run(
        mission_config,
        trace,
        final_snapshot={
            "alive": True,
            "deaths": 0,
            "distance_from_spawn": 5,
        },
    )

    assert report["max_score"] == 100
    assert report["resource_score"] == 62.5
    assert report["score"] == 62.5
    assert [resource["role"] for resource in report["resources"][:3]] == [
        "essential",
        "essential",
        "essential",
    ]


def test_time_efficiency_breaks_ties_for_equal_base_scores() -> None:
    mission_config = ResourceGatheringMissionConfig(
        id="resource_tiebreak",
        duration_seconds=120,
        resources=[
            ResourceSpec(
                item="logs",
                items=["oak_log"],
                display_name="logs",
                target_count=10,
                points=100,
                role="essential",
            )
        ],
    )
    early_trace = AgentRunTrace(
        task_id="resource_tiebreak",
        agent_name="early",
        started_at=0,
        agent_ready_at=0,
        ended_at=20,
        events=[TraceEvent(kind="done", data={})],
        final_state=FinalAgentState(inventory={"logs": 10}),
    )
    late_trace = AgentRunTrace(
        task_id="resource_tiebreak",
        agent_name="late",
        started_at=0,
        agent_ready_at=0,
        ended_at=80,
        events=[TraceEvent(kind="done", data={})],
        final_state=FinalAgentState(inventory={"logs": 10}),
    )

    early_report = score_resource_gathering_run(
        mission_config,
        early_trace,
        final_snapshot={"alive": True, "deaths": 0, "distance_from_spawn": 0},
    )
    late_report = score_resource_gathering_run(
        mission_config,
        late_trace,
        final_snapshot={"alive": True, "deaths": 0, "distance_from_spawn": 0},
    )

    assert early_report["score"] == late_report["score"] == 100.0
    assert early_report["time_efficiency"] > late_report["time_efficiency"]
    assert early_report["ranking_score"] > late_report["ranking_score"]
