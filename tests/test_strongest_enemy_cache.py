from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import json
import shutil

import pytest
import yaml

from guild_manager_bench.bench.llm import runner
from guild_manager_bench.bench.metrics import monster_combat_power
from guild_manager_bench.bench.llm.trace import LlmGameRun, ToolCallRecord, TurnTrace
from guild_manager_bench.game.loader import load_game_definition
from guild_manager_bench.game.models import CombatStats
from guild_manager_bench.game.skills import Skill, SkillCondition, SkillEffect
from guild_manager_bench.runtime.observation import _skill_to_dict


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def preset(tmp_path):
    path = tmp_path / "preset"
    path.mkdir()
    for file in (ROOT / "data/presets/default").glob("*.yaml"):
        shutil.copy2(file, path / file.name)
    game = yaml.safe_load((path / "game.yaml").read_text(encoding="utf-8"))
    game["rules"]["max_turns"] = 3
    game["rules"]["monster_spawn"]["count_curve"] = {"base": 1, "per_turn": 0, "maximum": 1}
    game["scoring"].update(waves=1, wave_size=1, difficulty_factors=[1],
                           elite_chance=0, boss_chance=0,
                           rank_min_diff=1, rank_max_diff=1, rank_waves=1)
    (path / "game.yaml").write_text(yaml.safe_dump(game, allow_unicode=True), encoding="utf-8")
    skills = yaml.safe_load((path / "skills.yaml").read_text(encoding="utf-8"))
    skills["skills"].append({"id": "cache_marker", "name": "标记", "kind": "passive",
        "condition": {"type": "always"},
        "effects": [{"type": "stat_bonus", "stat": "attack", "value": 1, "target": "self"}]})
    (path / "skills.yaml").write_text(yaml.safe_dump(skills, allow_unicode=True), encoding="utf-8")
    monsters = {"monsters": [{"id": "training_target", "name": "训练目标",
        "base_stats": {"hp": 1, "mp": 0, "attack": 0, "defense": 0, "speed": 1, "recovery": 0},
        "base_reward": {"gold": 1, "experience": 1}, "skills": ["cache_marker"]}]}
    (path / "monsters.yaml").write_text(yaml.safe_dump(monsters, allow_unicode=True), encoding="utf-8")
    return path


@pytest.fixture
def definition(preset):
    return load_game_definition(preset)


def observe_power_calls(monkeypatch):
    original = runner.monster_combat_power
    calls = []

    def measured(definition, stats, skills):
        calls.append((definition, stats, skills))
        return original(definition, stats, skills)

    monkeypatch.setattr(runner, "monster_combat_power", measured)
    return calls


class HuntingAgent:
    def __init__(self, *, recruit=False, interrupt_after=None):
        self.recruit = recruit
        self.calls = 0
        self.interrupt_after = interrupt_after

    def respond(self, *, messages, tools):
        self.calls += 1
        if self.interrupt_after == self.calls:
            raise RuntimeError("test interruption")
        if self.recruit:
            self.recruit = False
            call = runner.LlmToolCall("recruit_adventurer", {"candidate_id": 1})
        else:
            call = runner.LlmToolCall("end_turn", {"hunts": [{"adventurer_id": 1, "monster_id": 1}]})
        return runner.LlmAgentResponse(tool_calls=(call,))


def test_runner_reuses_monster_power_across_checkpoints_and_final_archive(preset, tmp_path, monkeypatch):
    calls = observe_power_calls(monkeypatch)
    run = runner.run_llm_game(HuntingAgent(recruit=True), data_dir=preset,
                             config=runner.LlmRunConfig(archive_dir=tmp_path / "archive"))
    assert run.status == "completed"
    assert len(calls) == 1
    replay = json.loads((Path(run.archive_dir) / "replay.json").read_text(encoding="utf-8"))
    strongest = replay["stats"]["game_actions"]["strongest_defeated_enemy"]
    assert strongest["turn"] == 1  # Equal power keeps the first victory.
    assert strongest["power"] == monster_combat_power(*calls[0])


def test_cache_keys_include_full_stats_and_skill_effects(definition, monkeypatch):
    calls = observe_power_calls(monkeypatch)
    evaluate = runner._cached_monster_power(definition)
    stats = CombatStats(100, 0, 20, 5, 10, 0)
    skill = Skill("same_id", "技能", "passive", SkillCondition("always"),
                  (SkillEffect("stat_bonus", value=1, stat="attack", target="self"),))
    expected = evaluate(stats, (skill,))
    assert evaluate(replace(stats), (replace(skill),)) == expected
    assert len(calls) == 1
    evaluate(replace(stats, attack=21), (skill,))
    evaluate(stats, (replace(skill, effects=(replace(skill.effects[0], value=2),)),))
    evaluate(stats, (replace(skill, condition=SkillCondition("self_hp_pct_lte", value=0.5)),))
    assert len(calls) == 4


@pytest.mark.parametrize("change", ["seed", "pool", "difficulty"])
def test_caches_are_isolated_between_definitions(definition, monkeypatch, change):
    calls = observe_power_calls(monkeypatch)
    if change == "seed":
        other = replace(definition, scoring=replace(definition.scoring, seed=99))
    elif change == "pool":
        target = definition.content.monster_archetypes[0]
        other = replace(definition, content=replace(definition.content,
            monster_archetypes=(replace(target, base_stats=replace(target.base_stats, hp=200)),)))
    else:
        other = replace(definition, scoring=replace(definition.scoring, rank_max_diff=6))
    stats = CombatStats(100, 0, 20, 5, 10, 0)
    first, second = runner._cached_monster_power(definition), runner._cached_monster_power(other)
    a, b = first(stats, ()), second(stats, ())
    assert first(stats, ()) == a and second(stats, ()) == b
    assert len(calls) == 2
    assert calls[0][0] is definition and calls[1][0] is other


def make_run(monster, *, text_only=False):
    result = {"ok": True, "turn_result": {"battles": [{"monster_id": monster["monster_id"],
        "monster_name": monster["name"], "won": True, "reward": monster["reward"]}]}}
    turn = TurnTrace(turn=1, prompt="", observation_before={"monsters": [monster]})
    if text_only:
        result = {"ok": True}
        turn.messages = [{"role": "tool", "name": "end_turn", "content":
                          f"成功 end_turn\n- 1 冒险者 vs 1 {monster['name']}：胜利"}]
    turn.tool_calls = [ToolCallRecord("end_turn", {"hunts": []}, result)]
    turn.complete()
    return LlmGameRun("completed", "stats", {}, [turn])


@pytest.mark.parametrize("text_only", [False, True])
def test_cached_stats_equal_original_and_return_fresh_metadata(definition, monkeypatch, text_only):
    skill = definition.content.monster_archetypes[0].skills[0]
    monster = {"monster_id": "target_1", "name": "目标一", "tier": "normal",
        "stats": {"hp": 100, "mp": 0, "attack": 20, "defense": 5, "speed": 10, "recovery": 0},
        "skills": [_skill_to_dict(skill)], "reward": {"gold": 5, "experience": 2}}
    original = runner._compute_run_stats(make_run(monster, text_only=text_only), definition)
    calls = observe_power_calls(monkeypatch)
    evaluate = runner._cached_monster_power(definition)
    assert runner._compute_run_stats(make_run(monster, text_only=text_only), definition,
                                    power_evaluator=evaluate) == original
    altered = deepcopy(monster)
    altered.update(monster_id="target_2", name="目标二", reward={"gold": 10, "experience": 4})
    run = make_run(altered, text_only=text_only)
    actual = runner._compute_run_stats(run, definition, power_evaluator=evaluate)
    assert len(calls) == 1
    enemy = actual["game_actions"]["strongest_defeated_enemy"]
    assert enemy["monster_id"] == "target_2" and enemy["name"] == "目标二"
    assert enemy["reward"]["gold"] == 10
    enemy["stats"]["hp"] = 9999
    assert runner._compute_run_stats(run, definition, power_evaluator=evaluate)["game_actions"]["strongest_defeated_enemy"]["stats"]["hp"] == 100
    assert len(calls) == 1


def test_resume_rebuilds_cache_once_and_reuses_it_afterward(preset, tmp_path, monkeypatch):
    calls = observe_power_calls(monkeypatch)
    config = runner.LlmRunConfig(archive_dir=tmp_path / "archive", max_turn_retries=0)
    with pytest.raises(RuntimeError, match="test interruption"):
        runner.run_llm_game(HuntingAgent(recruit=True, interrupt_after=3), data_dir=preset, config=config)
    assert len(calls) == 1
    archive = next((tmp_path / "archive").iterdir())
    restored = runner.run_llm_game(HuntingAgent(), data_dir=preset, config=config,
                                  resume_archive_dir=archive)
    assert restored.status == "completed"
    assert len(calls) == 2
