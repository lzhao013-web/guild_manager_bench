from copy import deepcopy
import json

from guild_manager_bench.api.workbench_curves import CurveStore
from test_workbench_curves import completed_turn, replay


def hero(identity="hero", name="测试剑士", level=8):
    return {"adventurer_id": identity, "name": name, "level": level}


def monster(identity, tier, name):
    return {"monster_id": identity, "tier": tier, "name": name}


def state(*, heroes=None, upgrades=(), monsters=()):
    return {"gold": 100, "experience_pool": 20, "adventurers": heroes if heroes is not None else [hero()],
            "global_upgrades": [{"upgrade_id": key, "unlocked": unlocked} for key, unlocked in upgrades],
            "monsters": list(monsters)}


def action(name, *, after=None, arguments=None, ok=True, **result):
    return {"type": "tool_result", "name": name, "arguments": arguments or {}, "result": {"ok": ok, **result},
            **({"observation_after": deepcopy(after)} if after is not None else {})}


def settlement(battles, after):
    return action("end_turn", after=after, turn_result={"battles": battles})


def battle(identity, won=True):
    return {"monster_id": identity, "monster_name": identity, "adventurer_id": "hero", "adventurer_name": "测试剑士",
            "won": won, "reward": {"gold": 10 if won else 0, "experience": 20 if won else 0}}


def project(tmp_path, *turns, final=None):
    payload = replay(*turns)
    if final is not None:
        payload["final_observation"].update(final)
    path = tmp_path / "replay.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    original = path.read_bytes()
    data = CurveStore().read(path)
    assert path.read_bytes() == original
    assert list(tmp_path.iterdir()) == [path]
    return data


def test_marks_expansions_each_hero_level9_and_real_boss_challenge_wins(tmp_path):
    enemies = [monster("boss", "boss", "真正的首领"), monster("challenge", "challenge", "固定挑战"),
               monster("elite", "elite", "精英"), monster("lost", "boss", "未击败首领")]
    before = state(upgrades=[("roster_expansion_1", False), ("roster_expansion_2", False)], monsters=enemies)
    expanded = {**before, "global_upgrades": [{"upgrade_id": "roster_expansion_1", "unlocked": True},
                                            {"upgrade_id": "roster_expansion_2", "unlocked": False}]}
    trained = {**expanded, "adventurers": [hero(level=10)]}
    recruited = {**trained, "adventurers": [hero(level=10), hero("new", "另一位剑士", 1)]}
    trained_both = {**recruited, "adventurers": [hero(level=10), hero("new", "另一位剑士", 9)]}
    turn = completed_turn(1)
    turn["observation_before"] = before
    turn["steps"] = [
        action("preview_team_power", after=trained_both, simulation_only=True),
        action("allocate_experience", after=trained_both, ok=False),
        action("purchase_upgrade", after=expanded, arguments={"upgrade_id": "roster_expansion_1"}),
        action("allocate_experience", after=trained),
        action("recruit_adventurer", after=recruited),
        action("allocate_experience", after=trained_both),
        action("allocate_experience", after=trained_both),
        settlement([battle("boss"), battle("challenge"), battle("elite"), battle("lost", False)], trained_both),
    ]
    data = project(tmp_path, turn)
    marks = data["milestones"]
    assert [m["kind"] for m in marks] == ["expansion", "level_9", "level_9", "boss_victory", "challenge_victory"]
    assert all(m["turn"] == 1 for m in marks)
    assert len({m["id"] for m in marks}) == 5
    assert "扩编 1" in marks[0]["label"]
    assert "测试剑士" in marks[1]["detail"] and "Lv8" in marks[1]["detail"] and "Lv10" in marks[1]["detail"]
    assert "另一位剑士" in marks[2]["detail"]
    assert "真正的首领" in marks[3]["detail"] and "测试剑士" in marks[3]["detail"]
    assert "固定挑战" in marks[4]["detail"]
    assert data["points"][0]["rank_score"] == 100
    assert all(set(mark) == {"id", "turn", "kind", "label", "detail"} for mark in marks)


def test_failed_rolled_back_pending_and_already_level9_are_not_new_milestones(tmp_path):
    before = state(heroes=[hero(level=9)], upgrades=[("roster_expansion_1", True)],
                   monsters=[monster("normal", "normal", "首领字样不能当成首领类型")])
    ghost = state(heroes=[hero(level=12)], upgrades=[("roster_expansion_2", True)])
    failed = completed_turn(1)
    failed.update(status="failed", observation_before=state(), steps=[action("allocate_experience", after=ghost)])
    rolled = {**failed, "status": "rolled_back"}
    committed = completed_turn(1)
    committed.update(observation_before=before, steps=[
        action("get_party", after=ghost),
        action("refresh_monsters", arguments={"preview": True}, after=ghost, simulation_only=True),
        action("purchase_upgrade", arguments={"upgrade_id": "roster_expansion_2"}, after=ghost, ok=False),
        settlement([battle("normal"), battle("unknown")], before),
    ])
    pending = {**failed, "turn": 2, "status": "running"}
    assert project(tmp_path, failed, rolled, committed, pending)["milestones"] == []


def test_compact_purchase_refs_and_next_opening_prove_legacy_milestones(tmp_path):
    turn = completed_turn(1)
    turn["observation_before"] = state()
    turn["steps"].insert(0, action("purchase_upgrade", arguments={"upgrade_id": 17},
                                 _llm_refs={"upgrade": {"roster_expansion_2": 17}}))
    del turn["steps"][-1]["observation_after"]
    next_turn = {"turn": 2, "status": "running", "steps": [], "observation_before": state(heroes=[hero(level=9)])}
    marks = project(tmp_path, turn, next_turn)["milestones"]
    assert [m["kind"] for m in marks] == ["expansion", "level_9"]
    assert "扩编 2" in marks[0]["label"]
    assert all(m["turn"] == 1 for m in marks)


def test_real_refresh_snapshot_supplies_actual_monster_tier(tmp_path):
    before = state(monsters=[monster("old", "normal", "普通怪")])
    refreshed = state(monsters=[monster("new", "boss", "刷新首领")])
    turn = completed_turn(1)
    turn.update(observation_before=before, steps=[
        action("preview_monster_refresh", after=state(monsters=[monster("new", "normal", "不是实际结果")])),
        action("refresh_monsters", after=refreshed),
        settlement([battle("new")], state(monsters=[])),
    ])
    marks = project(tmp_path, turn)["milestones"]
    assert len(marks) == 1 and marks[0]["kind"] == "boss_victory"
    assert "刷新首领" in marks[0]["detail"]


def test_dismissed_and_recruited_heroes_with_same_name_are_distinct(tmp_path):
    turn = completed_turn(1)
    start = state(heroes=[hero("first", "同名角色", 8)])
    trained = state(heroes=[hero("first", "同名角色", 9)])
    empty = state(heroes=[])
    recruited = state(heroes=[hero("second", "同名角色", 1)])
    retrained = state(heroes=[hero("second", "同名角色", 9)])
    turn.update(observation_before=start, steps=[
        action("allocate_experience", after=trained), action("dismiss_adventurer", after=empty),
        action("recruit_adventurer", after=recruited), action("allocate_experience", after=retrained),
        settlement([], retrained),
    ])
    marks = project(tmp_path, turn)["milestones"]
    assert len(marks) == 2 and all(mark["kind"] == "level_9" for mark in marks)
    assert marks[0]["id"] != marks[1]["id"]


def test_milestones_and_points_share_cache_and_preserve_stale_projection(tmp_path):
    path = tmp_path / "replay.json"
    turn = completed_turn(1)
    turn["steps"].insert(0, action("purchase_upgrade", arguments={"upgrade_id": "roster_expansion_1"}))
    path.write_text(json.dumps(replay(turn)), encoding="utf-8")
    store = CurveStore()
    first = store.read(path)
    assert len(first["milestones"]) == 1
    assert store.read(path) == first
    path.write_text("{partial", encoding="utf-8")
    stale = store.read(path)
    assert stale["warning"] and stale["milestones"] == first["milestones"] and stale["points"] == first["points"]
    second = completed_turn(2)
    second["steps"].insert(0, action("purchase_upgrade", arguments={"upgrade_id": "roster_expansion_2"}))
    path.write_text(json.dumps(replay(turn, second)), encoding="utf-8")
    refreshed = store.read(path)
    assert len(refreshed["milestones"]) == 2 and len(refreshed["points"]) == 2 and refreshed["warning"] is None


def test_missing_evidence_does_not_infer_from_final_state_or_display_names(tmp_path):
    turn = completed_turn(1)
    turn["steps"] = [
        action("purchase_upgrade", arguments={"upgrade_id": 17}),
        action("purchase_upgrade", arguments={"upgrade_id": "roster_expand"}, after={
            "global_upgrades": [{"upgrade_id": "roster_expand", "name": "扩编名册", "unlocked": True}]}),
        action("allocate_experience"),
        settlement([battle("boss")], {"gold": 50, "experience_pool": 0}),
    ]
    data = project(tmp_path, turn, final=state(heroes=[hero(level=9)], upgrades=[("roster_expansion_1", True)]))
    assert data["milestones"] == []
