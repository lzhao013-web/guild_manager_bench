import json
from dataclasses import replace
from pathlib import Path

from guild_manager_bench.bench.leaderboard import build_leaderboard
from guild_manager_bench.bench.metrics import (
    rank_score_from_final_observation,
    score_final_state,
)
from guild_manager_bench.bench.replay_scoring import with_rank_score_from_final_observation
from guild_manager_bench.game.actions import RecruitAction
from guild_manager_bench.game.loader import load_game_definition
from guild_manager_bench.game.state import ScoringRules
from guild_manager_bench.runtime.session import GameSession


def test_rank_score_from_final_observation_matches_game_state() -> None:
    definition = _small_scoring_definition()
    session = GameSession(definition)
    candidate_id = session.observation()["recruit_candidates"][0]["candidate_id"]
    session.apply_preparation(RecruitAction(candidate_id=candidate_id))
    assert session.state is not None

    observation = session.observation()
    from_state = score_final_state(definition, session.state).rank_score
    from_observation = rank_score_from_final_observation(definition, observation)

    assert from_observation == from_state


def test_replay_rank_fallback_adds_missing_rank_score() -> None:
    definition = _small_scoring_definition()
    replay = _legacy_replay(definition)

    filled = with_rank_score_from_final_observation(replay, strict=True)

    assert replay["score"].get("rank_score") is None
    assert filled["score"]["score"] == 12.34
    assert filled["score"]["rank_score"] >= 0
    assert filled["score"]["rank_score_source"] == "final_observation"
    assert filled["score"]["rank_score_per_adventurer"]
    assert filled["score"]["per_adventurer"][0]["rank_score"] >= 0


def test_leaderboard_uses_final_observation_rank_fallback(tmp_path: Path) -> None:
    definition = _small_scoring_definition()
    replay = _legacy_replay(definition)
    replay["agent"] = {"config": {"model": "test-model"}}
    unlocked_upgrade = replay["final_observation"]["global_upgrades"][0]
    unlocked_upgrade["unlocked"] = True
    replay["stats"] = {
        "tool_calls": {
            "total": 3,
            "successful": 2,
            "failed": 1,
            "by_name": {"end_turn": 1, "get_party": 2},
            "by_name_detail": {
                "end_turn": {"total": 1, "successful": 1, "failed": 0},
                "get_party": {"total": 2, "successful": 1, "failed": 1},
            },
        },
        "game_actions": {
            "battles_total": 1,
            "battles_won": 1,
            "battles_lost": 0,
            "total_gold_earned": 12,
            "total_experience_earned": 34,
            "economy_curve": [
                {
                    "turn": 1,
                    "gold_earned": 12,
                    "experience_earned": 34,
                    "cumulative_gold_earned": 12,
                    "cumulative_experience_earned": 34,
                },
            ],
            "adventurer_stats": [
                {
                    "adventurer_id": "adv-1",
                    "adventurer_name": "先锋",
                    "cumulative_battles_total": 1,
                    "cumulative_battles_won": 1,
                    "cumulative_battles_lost": 0,
                    "cumulative_gold_earned": 12,
                    "cumulative_experience_earned": 34,
                }
            ],
            "adventurer_stats_curve": [
                {
                    "turn": 1,
                    "adventurers": [
                        {
                            "adventurer_id": "adv-1",
                            "adventurer_name": "先锋",
                            "cumulative_battles_total": 1,
                            "cumulative_battles_won": 1,
                            "cumulative_battles_lost": 0,
                            "cumulative_gold_earned": 12,
                            "cumulative_experience_earned": 34,
                        }
                    ],
                }
            ],
            "strongest_defeated_enemy": {
                "turn": 1,
                "monster_id": "m1",
                "name": "测试怪物",
                "power": 123,
                "stats": {"hp": 50, "attack": 5},
            },
        },
    }
    expected = with_rank_score_from_final_observation(replay, strict=True)
    replay_path = tmp_path / "replay.json"
    replay_path.write_text(json.dumps(replay), encoding="utf-8")
    output = tmp_path / "leaderboard_data.json"

    build_leaderboard(tmp_path, output)

    data = json.loads(output.read_text(encoding="utf-8"))
    model = data["models"][0]
    assert model["model"] == "test-model"
    assert model["rank_score"]["best"] == expected["score"]["rank_score"]
    assert model["run_details"][0]["run_id"] == "replay"
    assert model["run_details"][0]["score"] == 12.34
    assert model["run_details"][0]["rank_score"] == expected["score"]["rank_score"]
    assert model["run_details"][0]["rank_score_per_adventurer"]
    contributor = model["run_details"][0]["rank_score_per_adventurer"][0]
    adventurer = contributor["adventurer"]
    assert adventurer["adventurer_id"] == contributor["adventurer_id"]
    assert adventurer["effective_stats"]["hp"] > 0
    assert adventurer["skills"]
    assert adventurer["equipment_slots"]
    assert "next_level" not in adventurer
    assert model["run_details"][0]["preset"] == "default"
    assert model["run_details"][0]["tool_calls"]["by_name_detail"]["get_party"]["failed"] == 1
    assert model["run_details"][0]["game_actions"]["economy_curve"][0]["cumulative_gold_earned"] == 12
    assert model["run_details"][0]["game_actions"]["adventurer_stats"][0][
        "cumulative_experience_earned"
    ] == 34
    assert model["run_details"][0]["game_actions"]["strongest_defeated_enemy"]["name"] == "测试怪物"
    upgrade = model["run_details"][0]["upgrades"][0]
    assert upgrade["upgrade_id"] == unlocked_upgrade["upgrade_id"]
    assert upgrade["name"] == unlocked_upgrade["name"]
    assert upgrade["stats"] == unlocked_upgrade["stats"]
    assert upgrade["party_size_bonus"] == unlocked_upgrade["party_size_bonus"]
    assert upgrade["skills"] == unlocked_upgrade["skills"]
    assert "missing" not in upgrade


def _legacy_replay(definition):
    session = GameSession(definition)
    candidate_id = session.observation()["recruit_candidates"][0]["candidate_id"]
    session.apply_preparation(RecruitAction(candidate_id=candidate_id))
    observation = session.observation()
    observation["scoring"].update(
        {
            "rank_min_diff": definition.scoring.rank_min_diff,
            "rank_max_diff": definition.scoring.rank_max_diff,
            "rank_step": definition.scoring.rank_step,
            "rank_waves": definition.scoring.rank_waves,
        }
    )
    return {
        "kind": "llm_replay",
        "status": "completed",
        "created_at": "2026-05-31T00:00:00",
        "agent": {"config": {"model": "test-model"}},
        "data": {"data_dir": str(_data_dir())},
        "turns": [],
        "final_observation": observation,
        "score": {"score": 12.34},
    }


def _small_scoring_definition():
    definition = load_game_definition(_data_dir())
    return replace(
        definition,
        scoring=ScoringRules(
            seed=123,
            waves=4,
            wave_size=2,
            difficulty_factors=(0, 2),
            rank_min_diff=5,
            rank_max_diff=15,
            rank_step=5,
            rank_waves=2,
        ),
    )


def _data_dir() -> Path:
    return Path(__file__).resolve().parents[1] / "data" / "presets" / "default"
