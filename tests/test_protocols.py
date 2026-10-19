import hashlib
import json
import shutil
from pathlib import Path

import pytest

from guild_manager_bench.bench import protocols
from guild_manager_bench.bench.llm import runner


def contract_transcript(runtime, data_dir):
    tools = runtime.GuildManagerTools(runtime.load_game_definition(data_dir))
    sid = "full-contract"
    tools.start_session(sid)
    system = runtime.build_system_prompt(
        turn_recovery_rules=tools.get_observation(sid)["observation"]["turn_recovery_rules"],
    )
    transcript = [system]
    for turn in range(1, 36):
        harness = runtime.TurnToolHarness(tools, sid, max_tool_calls=20,
                                          max_battle_preview_per_turn=3, endgame_start_turn=31)
        observation = tools.get_observation(sid)["observation"]
        transcript.append(runtime.build_turn_prompt(observation, endgame_start_turn=31))
        transcript.append(harness.tool_schemas())
        actions = []
        if turn == 1:
            actions = [("get_recruitment", {}), ("get_upgrades", {}), ("get_crafting", {}),
                       ("recruit_adventurer", {"candidate_id": 1}),
                       ("recruit_adventurer", {"candidate_id": 2}),
                       ("recruit_adventurer", {"candidate_id": 3}),
                       ("craft_equipment", {"recipe_id": 1}),
                       ("equip_item", {"adventurer_id": 1, "equipment_instance_id": 1}),
                       ("preview_battle", {"adventurer_id": 1, "monster_id": 3}),
                       ("allocate_experience", {"adventurer_id": 1, "amount": 1})]
        elif turn == 2:
            actions = [("purchase_upgrade", {"upgrade_id": 3}), ("get_inventory", {}),
                       ("allocate_experience", {"adventurer_id": 1, "amount": 100})]
        elif turn == 31:
            transcript.append(runtime.build_endgame_system_prompt(turn, 35))
        for name, args in actions:
            transcript.append(runtime._format_tool_result_for_model(name, harness.call_tool(name, args)))
        hunts = [{"adventurer_id": 1, "monster_id": 3}] if turn == 1 else []
        transcript.append(runtime._format_tool_result_for_model("end_turn", harness.call_tool("end_turn", {"hunts": hunts})))
    transcript.append(tools.get_observation(sid)["observation"])
    return transcript


def test_full_contract_matches_frozen_golden():
    frozen = protocols.full_runtime()
    transcript = contract_transcript(frozen, protocols.FULL_ROOT / "data/presets/full")
    expected = json.loads((Path(__file__).parent / "fixtures/full_v1_contract.json").read_text(encoding="utf-8"))
    digest = hashlib.sha256(json.dumps(transcript, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    assert digest == expected["sha256"]


def test_full_entry_is_isolated_from_live_prompt_engine_and_formatter(monkeypatch, tmp_path):
    def changed(*args, **kwargs):
        raise AssertionError("live implementation must not run for full")
    monkeypatch.setattr(runner, "build_system_prompt", changed)
    monkeypatch.setattr(runner, "_format_tool_result_for_model", changed)
    monkeypatch.setattr(runner, "load_game_definition", changed)
    calls = []
    class Agent:
        def respond(self, *, messages, tools):
            calls.append((messages, tools))
            return runner.LlmAgentResponse(tool_calls=(runner.LlmToolCall("end_turn", {"hunts": []}),))
    run = runner.run_llm_game(Agent(), data_dir="data/presets/full",
                             config=runner.LlmRunConfig(archive_dir=tmp_path))
    assert run.status == "completed"
    assert len(calls) == 35
    replay = json.loads((Path(run.archive_dir) / "replay.json").read_text(encoding="utf-8"))
    assert replay["data"]["benchmark_version"] == "v1"
    assert replay["data"]["protocol_hash"] == protocols.FULL_PROTOCOL_HASH
    assert "行动条件：" not in calls[0][0][0]["content"]
    # 同版本续跑允许恢复，不重新调用模型。
    restored = runner.run_llm_game(Agent(), data_dir="data/presets/full",
                                  resume_archive_dir=run.archive_dir,
                                  config=runner.LlmRunConfig(archive_dir=tmp_path))
    assert restored.status == "completed"
    assert len(calls) == 35


def test_frozen_manifest_detects_skill_changes(monkeypatch, tmp_path):
    root = tmp_path / "full_v1"
    shutil.copytree(protocols.FULL_ROOT, root, ignore=shutil.ignore_patterns("__pycache__"))
    with (root / "data/presets/full/skills.yaml").open("a", encoding="utf-8") as f:
        f.write("\n# accidental change\n")
    monkeypatch.setattr(protocols, "FULL_ROOT", root)
    with pytest.raises(ValueError, match="frozen file changed"):
        protocols.verify_full_release()


def test_configuration_changes_are_different_versions():
    runtime = protocols.full_runtime()
    data = protocols.FULL_ROOT / "data/presets/full"
    a = runtime.LlmRunConfig()
    b = runtime.LlmRunConfig(max_tool_calls_per_turn=21)
    definition = runtime.load_game_definition(data)
    assert protocols.protocol_metadata(data, definition, a, frozen=True)["benchmark_version"] == "v1"
    assert protocols.protocol_metadata(data, definition, b, frozen=True)["benchmark_version"].startswith("v1-config-")


def test_resume_rejects_unverified_or_changed_protocol(tmp_path):
    (tmp_path / "replay.json").write_text(json.dumps({"data": {"preset": "full"}}))
    with pytest.raises(ValueError, match="unverified"):
        protocols.verify_resume_protocol(tmp_path, {"benchmark_version": "v1", "protocol_hash": "x", "settings_hash": "y"})


def test_frozen_sources_only_change_import_namespace():
    manifest = protocols.verify_full_release()
    for original, expected in manifest["source_files"].items():
        relative = Path(original).relative_to("src/guild_manager_bench")
        text = (protocols.FULL_ROOT / relative).read_text(encoding="utf-8")
        restored = text.replace("guild_manager_bench._frozen.full_v1.", "guild_manager_bench.")
        assert hashlib.sha256(restored.encode()).hexdigest() == expected


def test_versioned_full_replay_uses_frozen_scorer(monkeypatch):
    from guild_manager_bench.bench import replay_scoring
    def changed(*args, **kwargs):
        raise AssertionError("live scorer must not evaluate frozen full")
    monkeypatch.setattr(replay_scoring, "rank_score_breakdown_from_final_observation", changed)
    runtime = protocols.full_runtime()
    tools = runtime.GuildManagerTools(runtime.load_game_definition(protocols.FULL_ROOT / "data/presets/full"))
    observation = tools.start_session("score-test")["observation"]
    replay = {"data": {"preset": "full", "benchmark_version": "v1", "protocol_hash": protocols.FULL_PROTOCOL_HASH},
              "final_observation": observation, "score": {}}
    assert replay_scoring.with_rank_score_from_final_observation(replay, strict=True)["score"]["rank_score"] == 0


def test_frozen_scoring_is_picklable_in_worker_process():
    from concurrent.futures import ProcessPoolExecutor
    from dataclasses import replace
    from guild_manager_bench._frozen.full_v1.bench import metrics
    runtime = protocols.full_runtime()
    definition = runtime.load_game_definition(protocols.FULL_ROOT / "data/presets/full")
    definition = replace(definition, scoring=replace(definition.scoring, rank_max_diff=16, rank_waves=2))
    tools = runtime.GuildManagerTools(definition)
    tools.start_session("worker-test")
    candidate = tools.get_recruitment("worker-test")["recruit_candidates"][0]["candidate_id"]
    tools.recruit_adventurer("worker-test", candidate)
    state = tools.get_state("worker-test")
    direct = metrics.compute_rank_score(definition, state)
    with ProcessPoolExecutor(max_workers=1) as executor:
        pooled = metrics.compute_rank_score(definition, state, executor=executor)
    assert direct == pooled


def test_legacy_hash_does_not_claim_frozen_version():
    legacy = protocols.run_version({"data_hash": "abc123"})
    assert legacy["benchmark_version"] == "legacy-abc123"
    assert legacy["protocol_status"] == "unverified"
    assert protocols.run_version({**legacy, "data_hash": "abc123"}) == legacy
