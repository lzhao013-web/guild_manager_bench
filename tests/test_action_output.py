"""只读业务结果投影，不执行游戏操作或评分。"""
from copy import deepcopy
import json

from guild_manager_bench.api.action_output import action_output
from guild_manager_bench.api.workbench_watch import WatchJournal
from test_workbench_trajectories import observation, write


def test_live_and_whole_run_share_the_same_lightweight_equipment_return(tmp_path):
    before = observation()
    after = deepcopy(before)
    after["equipment_inventory"] = [{"instance_id": "eq-new", "name": "铁剑", "slot": "main_hand", "stats": {"attack": 7}, "equipped_by": None, "unused_large_field": "do-not-copy"}]
    path = tmp_path / "trace.jsonl"
    write(path, [{"type": "turn_started", "turn": 1, "observation": before},
                 {"type": "tool_result", "name": "craft_equipment", "arguments": {"recipe_id": "sword"},
                  "result": {"ok": True, "_observation_after": after, "event": {"summary": "制作铁剑"}}}])
    original = path.read_bytes()
    journal = WatchJournal(path, archived=True)
    returned = journal.read()["records"][-1]["event"]["result"]["_action_output"]
    indexed = journal.history()["entries"][0]["event"]["result"]["_action_output"]
    assert returned == indexed
    assert returned["equipment_inventory"] == [{"instance_id": "eq-new", "name": "铁剑", "slot": "main_hand", "stats": {"attack": 7}, "equipped_by": None}]
    assert "do-not-copy" not in json.dumps(indexed)
    assert "_observation_after" not in json.dumps(journal.history())
    assert path.read_bytes() == original


def test_output_contains_actual_changed_entities_not_everyone_or_current_inventory():
    before = observation()
    before["adventurers"][0].update(level=1, effective_stats={"attack": 12}, resources={"current_hp": 80, "current_mp": 0})
    before["equipment_inventory"] = [{"instance_id": "old", "name": "旧铁剑", "stats": {"attack": 1}, "equipped_by": None}]
    after = deepcopy(before)
    after["adventurers"][0].update(level=2, effective_stats={"attack": 15})
    after["global_upgrades"][0]["unlocked"] = True
    output = action_output({"ok": True}, before, after)
    assert output["adventurers"][0]["level"] == 2
    assert output["global_upgrades"][0]["upgrade_id"] == "training"
    assert "equipment_inventory" not in output
    assert action_output({"ok": True}, {}, after) == {}
    assert action_output({"ok": True}, before, {}) == {}


def test_explicit_recruit_return_works_without_snapshots_but_does_not_override_known_changes():
    recruited = {"adventurer_id": "new", "name": "新剑士", "level": 1}
    assert action_output({"ok": True, "recruited_adventurer": recruited}, {}, {})["adventurers"] == [recruited]
    before = observation()
    before["adventurers"][0]["level"] = 1
    after = deepcopy(before)
    after["adventurers"][0]["level"] = 2
    after["adventurers"].append(recruited)
    output = action_output({"ok": True, "recruited_adventurer": recruited}, before, after)
    assert [item["adventurer_id"] for item in output["adventurers"]] == ["hero", "new"]


def test_failed_or_simulated_calls_do_not_become_real_output():
    before = observation()
    after = deepcopy(before)
    after["global_upgrades"][0]["unlocked"] = True
    assert action_output({"ok": False}, before, after) == {}
    assert action_output({"ok": True, "simulation_only": True}, before, after) == {}


def test_dismissal_and_equipment_replacement_keep_both_sides_of_the_actual_result():
    before = observation()
    before["equipment_inventory"] = [{"instance_id": "eq", "name": "铁剑", "equipped_by": "hero", "stats": {"attack": 2}}]
    after = deepcopy(before)
    after["adventurers"] = []
    after["equipment_inventory"][0]["equipped_by"] = None
    output = action_output({"ok": True}, before, after)
    assert output["dismissed_adventurers"][0]["name"] == "战士"
    assert output["equipment_inventory"][0]["equipped_by"] is None


def test_index_keeps_recorded_battle_rewards_without_copying_full_combat(tmp_path):
    path = tmp_path / "trace.jsonl"
    write(path, [{"type": "turn_started", "turn": 1, "observation": observation()},
                 {"type": "tool_result", "name": "end_turn", "result": {"ok": True, "turn_result": {"battles": [
                     {"adventurer_name": "战士", "monster_name": "史莱姆", "won": True, "reward": {"gold": 0, "experience": 5},
                      "combat": {"events": ["large-record"]}}
                 ]}}}])
    result = WatchJournal(path, archived=True).history()["entries"][0]["event"]["result"]
    assert result["turn_result"]["battles"][0]["reward"] == {"gold": 0, "experience": 5}
    assert "large-record" not in json.dumps(result)


def test_successful_unchanged_tactics_still_show_the_recorded_target_state(tmp_path):
    before = observation()
    before["adventurers"][0]["tactics"] = {"skill_priority": [], "disabled_skills": []}
    path = tmp_path / "trace.jsonl"
    write(path, [{"type": "turn_started", "turn": 1, "observation": before},
                 {"type": "tool_call", "name": "configure_tactics", "call_id": "same", "arguments": {"adventurer_id": "hero", "skill_priority": [], "disabled_skills": []}},
                 {"type": "tool_result", "name": "configure_tactics", "call_id": "same", "result": {"ok": True, "_observation_after": deepcopy(before), "event": {"summary": "保持默认战术", "changes": []}}}])
    journal = WatchJournal(path, archived=True)
    indexed = journal.history()["entries"][0]["event"]["result"]["_action_output"]
    recorded = journal.read()["records"][-1]["event"]["result"]["_action_output"]
    assert indexed == recorded
    assert recorded["adventurers"][0]["adventurer_id"] == "hero"
    assert recorded["adventurers"][0]["tactics"] == {"skill_priority": [], "disabled_skills": []}
