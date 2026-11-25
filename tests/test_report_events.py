from copy import deepcopy
import json

from guild_manager_bench.api.curve_milestones import extract_milestones
from guild_manager_bench.api.workbench_curves import CurveStore
from guild_manager_bench.api.run_report import render_run_report
from test_run_report import report_replay


def rich_replay():
    replay = report_replay()
    start = deepcopy(replay['turns'][0]['observation_before'])
    start.update(gold=1000, experience_pool=1000, party_size_limit=2,
                 equipment_inventory=[], material_exchange={'used_points': 0})
    start['adventurers'][0]['template_id'] = 'foot_knight'
    start['adventurers'][0]['equipment_slots'][0]['item'].update(instance_id='old', template_id='iron_sword')
    start['global_upgrades'].append({'upgrade_id': 'forge', 'name': '精炼锻造', 'unlocked': False, 'branch': 'martial', 'tier': 1})
    recruited = deepcopy(start)
    recruited['gold'] = 900
    recruited['adventurers'].append({'adventurer_id': 'archer', 'name': '林地射手', 'template_id': 'woodland_archer',
                                    'level': 1, 'equipment_slots': [], 'tactics': {'skill_priority': [], 'disabled_skills': []}})
    trained = deepcopy(recruited)
    trained['experience_pool'] = 400
    trained['adventurers'][0]['level'] = 9
    prepared = deepcopy(trained)
    prepared['global_upgrades'][0]['unlocked'] = True
    prepared['global_upgrades'][1]['unlocked'] = True
    prepared['gold'] = 200
    prepared['equipment_inventory'] = [{'instance_id': 'blade', 'template_id': 't3_heavy_sword', 'name': '终局长剑'}]
    prepared['adventurers'][0]['equipment_slots'][0]['item'] = prepared['equipment_inventory'][0]
    prepared['adventurers'][0]['tactics']['skill_priority'] = []
    prepared['material_exchange']['used_points'] = 2
    def step(name, after, **result):
        return {'type': 'tool_result', 'name': name, 'result': {'ok': True, **result}, 'observation_after': deepcopy(after)}
    replay['turns'] = [{'turn': 1, 'status': 'completed', 'observation_before': start, 'steps': [
        step('recruit_adventurer', recruited), step('allocate_experience', trained),
        step('execute_plan', prepared),
        step('end_turn', prepared, turn_result={'battles': [
            {'adventurer_id': 'hero', 'monster_id': 'boss', 'won': False, 'reward': {'gold': 0}},
        ]}),
    ]}, {'turn': 2, 'status': 'completed', 'observation_before': prepared, 'steps': [
        step('end_turn', prepared, turn_result={'battles': [
            {'adventurer_id': 'hero', 'monster_id': 'boss', 'won': True, 'reward': {'gold': 47}},
        ]}),
    ]}]
    replay['final_observation'] = prepared
    return replay


def test_report_has_richer_history_but_curve_contract_stays_exactly_the_same(tmp_path):
    replay = rich_replay()
    expected = extract_milestones(replay, {1, 2})
    assert [m['kind'] for m in expected] == ['level_9', 'expansion', 'boss_victory']
    path = tmp_path / 'replay.json'
    path.write_text(json.dumps(replay), encoding='utf-8')
    before = path.read_bytes()
    curve = CurveStore().read(path)
    html = render_run_report(replay)
    for label in ('人物成长履历', '完整历程', '林地射手加入', '精炼锻造', '终局长剑', '首次战败', '经验投入', '战术调整'):
        assert label in html
    assert CurveStore().read(path) == curve
    assert curve['milestones'] == expected
    assert extract_milestones(replay, {1, 2}) == expected
    assert path.read_bytes() == before
    assert list(tmp_path.iterdir()) == [path]


def test_rich_projection_is_pure_and_excludes_failed_rolled_back_preview_and_repeated_commits():
    from guild_manager_bench.api.report_events import extract_report_events
    replay = rich_replay()
    ghost = deepcopy(replay['turns'][0]['steps'][0])
    ghost['observation_after']['adventurers'][1]['name'] = '不该导出的试算角色'
    replay['turns'][0]['steps'][0:0] = [
        {**ghost, 'name': 'preview_team_power', 'result': {'ok': True, 'simulation_only': True}},
        {**ghost, 'result': {'ok': False}},
        {**ghost, 'name': 'get_party'},
    ]
    replay['turns'].insert(0, {'turn': 1, 'status': 'rolled_back', 'steps': [ghost]})
    replay['turns'].insert(0, {'turn': 1, 'status': 'failed', 'steps': [ghost]})
    replay['turns'][2]['steps'].insert(6, {**deepcopy(replay['turns'][2]['steps'][5]), 'result': {'ok': True, 'already_executed': True}})
    before = deepcopy(replay)
    events = extract_report_events(replay)
    assert replay == before
    assert '不该导出' not in json.dumps(events, ensure_ascii=False)
    recruits = [e for e in events if e['kind'] == 'recruit']
    assert len(recruits) == 1 and recruits[0]['members'] == ['archer']
    assert recruits[0]['evidence']['step'] == 4
    assert len([e for e in events if e['kind'] == 'equipment']) == 1


def test_lifecycle_uses_identity_not_display_name_and_does_not_invent_recruit_dates():
    from guild_manager_bench.api.report_events import extract_report_events
    replay = rich_replay()
    replay['turns'][0]['observation_before']['adventurers'][0]['name'] = '同名角色'
    for turn in replay['turns']:
        for step in turn['steps']:
            for member in step['observation_after']['adventurers']:
                member['name'] = '同名角色'
    events = extract_report_events(replay)
    assert any(e['kind'] == 'first_seen' and e['members'] == ['hero'] for e in events)
    assert not any(e['kind'] == 'recruit' and e['members'] == ['hero'] for e in events)
    assert [e['members'] for e in events if e['kind'] == 'recruit'] == [['archer']]


def test_missing_snapshot_locations_remain_uncertain_and_final_state_is_not_used_for_events():
    from guild_manager_bench.api.report_events import extract_report_events
    replay = rich_replay()
    for step in replay['turns'][0]['steps']:
        step.pop('observation_after')
    events = extract_report_events(replay)
    recruit = next(e for e in events if e['kind'] == 'recruit')
    assert recruit['evidence'] == {'source': 'next_opening', 'turn': 2}
    assert not any(e['kind'] in ('gold_spent', 'experience_spent') for e in events)
    replay['turns'] = replay['turns'][:1]
    assert not any(e['kind'] == 'recruit' for e in extract_report_events(replay))


def test_snapshot_gaps_do_not_assign_earlier_changes_or_spending_to_later_action():
    from guild_manager_bench.api.report_events import extract_report_events
    replay = rich_replay()
    replay['turns'][0]['steps'][0].pop('observation_after')
    expected_curve = extract_milestones(replay, {1, 2})
    events = extract_report_events(replay)
    for kind in ('recruit', 'level_9'):
        event = next(e for e in events if e['kind'] == kind)
        assert event['evidence']['source'] == 'snapshot_range'
        assert event['evidence']['step'] == 2
    assert not any(e['kind'] in ('gold_spent', 'experience_spent') and e['evidence'].get('step') == 2 for e in events)
    assert extract_milestones(replay, {1, 2}) == expected_curve


def test_opening_full_roster_does_not_become_first_full_roster_after_recruiting_again():
    from guild_manager_bench.api.report_events import extract_report_events
    replay = rich_replay()
    first = replay['turns'][0]
    original = first['observation_before']
    first['observation_before'] = deepcopy(first['steps'][0]['observation_after'])
    first['steps'].insert(0, {'type': 'tool_result', 'name': 'dismiss_adventurer', 'result': {'ok': True}, 'observation_after': original})
    events = extract_report_events(replay)
    assert not any(e['kind'] == 'full_roster' for e in events)


def test_partial_tactic_record_keeps_missing_disabled_list_unknown():
    from guild_manager_bench.api.report_events import extract_report_events
    replay = rich_replay()
    for turn in replay['turns']:
        for step in turn['steps']:
            for member in step['observation_after']['adventurers']:
                member['tactics'].pop('disabled_skills', None)
    event = next(e for e in extract_report_events(replay) if e['kind'] == 'tactics')
    assert '停用：未记录' in event['detail']


def test_final_equipment_history_belongs_to_final_owner_not_any_past_wearer():
    from guild_manager_bench.api.report_events import extract_report_events
    replay = rich_replay()
    moved = deepcopy(replay['final_observation'])
    item = moved['adventurers'][0]['equipment_slots'][0]['item']
    moved['adventurers'][0]['equipment_slots'][0]['item'] = None
    moved['adventurers'][1]['equipment_slots'] = [{'slot': 'main_hand', 'item': item}]
    replay['turns'][1]['steps'].insert(0, {'type': 'tool_result', 'name': 'equip_item', 'result': {'ok': True}, 'observation_after': moved})
    replay['turns'][1]['steps'][-1]['observation_after'] = moved
    replay['final_observation'] = moved
    events = extract_report_events(replay)
    equipment = [e for e in events if e['kind'] == 'equipment' and e['final_equipment']]
    assert [e['members'] for e in equipment] == [['archer']]


def test_resource_investments_are_measured_but_do_not_fill_highlight_quotas():
    from guild_manager_bench.api.report_events import extract_report_events, select_highlights
    replay = rich_replay()
    events = extract_report_events(replay)
    selected = select_highlights(events)
    assert selected
    assert [e['turn'] for e in selected] == sorted(e['turn'] for e in selected)
    assert len({e['id'] for e in selected}) == len(selected)
    assert not any(e['kind'] in ('first_defeat', 'gold_spent', 'experience_spent') for e in selected)
    spend = [e for e in events if e['kind'] == 'gold_spent']
    assert [e['amount'] for e in spend] == [100, 700]
    assert spend[-1]['evidence']['tool'] == 'execute_plan'
