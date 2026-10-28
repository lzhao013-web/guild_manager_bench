from copy import deepcopy
from pathlib import Path

from guild_manager_bench.bench.leaderboard import _adventurer_detail, _extract_run_info, _unlocked_upgrades
from guild_manager_bench.bench.leaderboard_development import development_summary


def observation(**patch):
    return {
        "turn": 30, "max_turns": 35, "challenge_start_turn": 30,
        "defeated_challenge_ids": [], "gold": 100, "experience_pool": 600,
        "materials": {"herb": 4}, "adventurers": [], "equipment_inventory": [],
        "global_upgrades": [], "crafting_recipes": [],
        "monsters": [{"tier": "challenge", "archetype_id": identity, "name": name}
                     for identity, name in [("iron_colossus", "巨像"), ("doom_oracle", "先知"), ("blade_tyrant", "暴君")]],
        **patch,
    }


def step(name, arguments=None, after=None, ok=True):
    return {"type": "tool_result", "name": name, "arguments": arguments or {},
            "result": {"ok": ok}, **({"observation_after": after} if after is not None else {})}


def turn(before, steps, status="completed"):
    return {"turn": before["turn"], "status": status, "observation_before": before, "steps": steps}


def test_archived_replay_uses_directory_identity_but_keeps_published_filename():
    replay = {"kind": "llm_replay", "status": "completed", "session_id": "sid", "agent": {"config": {"model": "model"}},
              "data": {}, "score": {"rank_score": 100}, "turns": [], "final_observation": {}}
    assert _extract_run_info(replay, source_path=Path("/archives/model-run/replay.json"))["run_id"] == "model-run"
    assert _extract_run_info(replay, source_path=Path("/published/old-id.json"))["run_id"] == "old-id"
    assert _extract_run_info(replay)["run_id"] == "sid"


def test_talents_keep_recorded_costs_dependencies_economy_and_automatic_trigger():
    node = {"upgrade_id": "training", "name": "训练", "unlocked": True, "gold_cost": 100, "experience_cost": 80,
            "material_costs": {"herb": 2}, "branch": "economy", "tier": 2, "required_upgrade_ids": ["base"],
            "exclusive_upgrade_ids": ["other"], "hunt_experience_bonus_percent": 6,
            "skills": [{"skill_id": "heal", "trigger": "action_start", "effects": [{"type": "mp_restore_percent", "value": .3, "minimum": 12}]}]}
    original = deepcopy(node)
    result = _unlocked_upgrades({"global_upgrades": [node, {"upgrade_id": "base", "name": "训练场"}, {"upgrade_id": "other", "name": "另一终阶"}]})
    assert node == original
    actual = result[0]
    for key in ("experience_cost", "material_costs", "branch", "tier", "hunt_experience_bonus_percent"):
        assert actual[key] == node[key]
    assert actual["required_upgrade_ids_names"] == ["训练场"]
    assert actual["exclusive_upgrade_ids_names"] == ["另一终阶"]
    assert actual["skills"][0]["trigger"] == "action_start"
    assert actual["skills"][0]["effects"][0]["minimum"] == 12
    old = _unlocked_upgrades({"global_upgrades": [{"upgrade_id": "old", "name": "旧升级", "unlocked": True, "gold_cost": 10, "skills": [{"skill_id": "old"}]}]})[0]
    assert "experience_cost" not in old
    assert "branch" not in old
    assert "trigger" not in old["skills"][0]


def test_adventurer_keeps_tactics_and_does_not_invent_it_for_old_records():
    actor = {"adventurer_id": "a", "skills": [], "equipment_slots": [], "tactics": {
        "skill_priority": ["star"], "disabled_skills": ["missile"],
        "available_active_skills": [{"skill_id": "star", "name": "星陨"}, {"skill_id": "missile", "name": "飞弹"}],
    }}
    detail = _adventurer_detail(actor)
    assert detail["tactics"]["disabled_skills"] == ["missile"]
    assert detail["tactics"]["available_active_skills"][1]["name"] == "飞弹"
    assert "tactics" not in _adventurer_detail({"adventurer_id": "old"})


def test_refresh_separates_both_preview_formats_failures_and_missing_costs():
    before = observation(turn=20)
    paid = observation(turn=20, gold=70)
    unknown = step("refresh_monsters", {"preview": False})
    uncertain = {"type": "tool_result", "name": "refresh_monsters", "arguments": {"preview": False}, "content": "没有状态"}
    summary = development_summary([turn(before, [
        step("refresh_monsters", {"preview": True}),
        step("preview_monster_refresh"),
        step("refresh_monsters", {"preview": False}, paid),
        step("refresh_monsters", {"preview": False}, ok=False),
        unknown,
        step("refresh_monsters", {"preview": False}, observation(turn=20, gold=20)),
        uncertain,
    ])], observation(turn=35))
    assert summary["refresh"] == {"previews": 2, "paid": 3, "failed": 1, "unconfirmed": 1,
                                  "known_gold_spent": 30, "unknown_cost_count": 2}


def test_missing_write_snapshot_does_not_attribute_its_spending_to_refresh():
    before = observation(turn=10)
    summary = development_summary([turn(before, [step("craft_equipment"),
        step("refresh_monsters", {"preview": False}, observation(turn=10, gold=40))])], observation(turn=35))
    assert summary["refresh"]["known_gold_spent"] == 0
    assert summary["refresh"]["unknown_cost_count"] == 1


def test_failed_turns_do_not_enter_business_events_and_failed_actions_keep_known_balance():
    before = observation(turn=20)
    summary = development_summary([
        turn(before, [step("refresh_monsters", {}, observation(turn=20, gold=0))], status="failed"),
        turn(before, [step("craft_equipment", ok=False), step("refresh_monsters", {}, observation(turn=20, gold=70))]),
    ], observation(turn=35))
    assert summary["refresh"]["paid"] == 1
    assert summary["refresh"]["known_gold_spent"] == 30


def test_challenges_keep_recorded_kill_turn_actor_and_actual_materials():
    before = observation()
    after = observation(turn=31, defeated_challenge_ids=["iron_colossus"], monsters=before["monsters"][1:])
    action = step("end_turn", after=after)
    action["result"]["turn_result"] = {"battles": [
        {"monster_id": "challenge_iron_colossus", "monster_name": "巨像", "won": True,
         "adventurer_name": "炮手", "reward": {"materials": {"siege_core": 1}}},
        {"monster_id": "challenge_doom_oracle", "monster_name": "先知", "won": False},
    ]}
    summary = development_summary([turn(before, [action])], observation(turn=35, defeated_challenge_ids=["iron_colossus"]))
    assert summary["challenges"]["total"] == 3
    assert summary["challenges"]["defeated"] == 1
    giant = next(e for e in summary["challenges"]["entries"] if e["challenge_id"] == "iron_colossus")
    assert giant["turn"] == 30 and giant["adventurer_name"] == "炮手"
    assert giant["materials"] == {"siege_core": 1}
    assert next(e for e in summary["challenges"]["entries"] if e["challenge_id"] == "doom_oracle")["defeated"] is False


def test_unobserved_challenge_total_and_kill_turn_remain_unknown():
    summary = development_summary([], observation(turn=35, defeated_challenge_ids=["iron_colossus"]))
    assert summary["challenges"]["total"] is None
    assert summary["challenges"]["entries"][0]["turn"] is None


def test_purchase_and_seal_events_use_snapshot_costs_not_listed_costs():
    before = observation(global_upgrades=[{"upgrade_id": "a", "name": "训练", "unlocked": False, "gold_cost": 999}])
    purchased = observation(gold=75, experience_pool=520, materials={"herb": 2},
                            global_upgrades=[{"upgrade_id": "a", "name": "训练", "unlocked": True, "gold_cost": 999}])
    item = {"instance_id": "seal", "template_id": "seal_mage", "name": "星陨印记", "slot": "ultimate"}
    crafted = {**purchased, "materials": {}, "equipment_inventory": [item]}
    final = {**crafted, "turn": 35, "adventurers": [{"adventurer_id": "mage", "name": "魔弹", "equipment_slots": [{"slot": "ultimate", "item": item}]}],
             "crafting_recipes": [{"output_slot": "ultimate", "output_template_id": "seal_mage", "material_costs": {"siege_core": 1}}]}
    summary = development_summary([turn(before, [step("purchase_upgrade", after=purchased), step("craft_equipment", after=crafted), step("equip_item", after=final)])], final)
    purchase = next(e for e in summary["events"] if e["type"] == "upgrade")
    assert purchase["costs"] == {"gold": 25, "experience": 80, "materials": {"herb": 2}}
    assert {e["type"] for e in summary["events"]} == {"upgrade", "seal_craft", "seal_equip"}
    assert summary["seals"] == [{"name": "星陨印记", "adventurer_name": "魔弹", "recipe_materials": {"siege_core": 1}}]


def test_partial_purchase_snapshots_are_not_interpreted_as_empty_tree():
    before = observation()
    before.pop("global_upgrades")
    after = observation(global_upgrades=[{"upgrade_id": "a", "name": "训练", "unlocked": True}])
    summary = development_summary([turn(before, [step("purchase_upgrade", after=after)])], after)
    assert not summary["events"]
    assert summary["unlocated_purchases"] == 1


def test_official_changes_override_misassociated_snapshots_and_arguments():
    before = observation(turn=17)
    action = step("refresh_monsters", {"hunts": []}, deepcopy(before))
    action["content"] = "成功 refresh_monsters：付费刷新怪物，锁定3个普通目标\n变化:\n- 金币: 66 -> 46\n- 本回合刷新次数: 0 -> 1\n预算: 已用 10/20"
    summary = development_summary([turn(before, [action])], observation(turn=35))
    assert summary["refresh"]["known_gold_spent"] == 20
    assert summary["refresh"]["unknown_cost_count"] == 0
    assert summary["events"][0]["locked_count"] == 3


def test_official_purchase_text_recovers_missing_identity_without_using_list_price():
    before = observation(global_upgrades=[{"upgrade_id": "a", "name": "训练场", "unlocked": True}])
    action = step("purchase_upgrade", {"hunts": []}, deepcopy(before))
    action["content"] = "成功 purchase_upgrade：购买全局加成 训练场\n变化:\n- 金币: 123 -> 58\n- bone: 3 -> 1\n- iron_ore: 5 -> 3\n预算: 已用 4/20"
    summary = development_summary([turn(before, [action])], before)
    assert summary["unlocated_purchases"] == 0
    event = summary["events"][0]
    assert event["name"] == "训练场"
    assert event["costs"] == {"gold": 65, "experience": 0, "materials": {"bone": 2, "iron_ore": 2}}


def test_partial_changes_do_not_invent_missing_costs():
    action = step("purchase_upgrade")
    action["content"] = "成功 purchase_upgrade：购买全局加成 教范\n变化:\n- 金币: 100 -> 20"
    summary = development_summary([turn(observation(), [action])], observation(turn=35))
    costs = summary["events"][0]["costs"]
    assert costs["gold"] == 80
    assert costs["experience"] is None and costs["materials"] is None


def test_stale_equal_balances_do_not_prove_a_free_refresh():
    before = observation()
    summary = development_summary([turn(before, [step("refresh_monsters", after=deepcopy(before))])], observation(turn=35))
    assert summary["refresh"]["unknown_cost_count"] == 1


def test_official_mode_and_failure_are_not_overridden_by_misassociated_metadata():
    before = observation()
    preview = step("refresh_monsters", {"hunts": []})
    preview["content"] = "成功 refresh_monsters：仅预览下一次怪池"
    failed = step("refresh_monsters")
    failed["content"] = "失败 refresh_monsters：金币不足"
    summary = development_summary([turn(before, [preview, failed])], observation(turn=35))
    assert summary["refresh"]["previews"] == 1
    assert summary["refresh"]["failed"] == 1
    assert summary["refresh"]["paid"] == 0
