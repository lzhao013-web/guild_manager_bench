from copy import deepcopy

from guild_manager_bench.api.curve_milestones import extract_milestones
from guild_manager_bench.api.report_events import extract_report_events, select_highlights
from guild_manager_bench.api.run_report import render_run_report
from test_report_events import rich_replay
from test_run_report import Document


def add_upgrades(replay, upgrades):
    first, second = replay['turns']
    identities = {item['upgrade_id'] for item in upgrades}
    for observations, unlocked in (([first['observation_before'], *(s['observation_after'] for s in first['steps'][:2])], False),
                                  ([*(s['observation_after'] for s in first['steps'][2:]), second['observation_before'],
                                    second['steps'][0]['observation_after'], replay['final_observation']], True)):
        for observation in observations:
            observation['global_upgrades'] = [item for item in observation['global_upgrades'] if item['upgrade_id'] not in identities]
            observation['global_upgrades'].extend({**item, 'unlocked': unlocked} for item in upgrades)


def test_ordinary_operations_stay_in_history_without_filling_highlight_quotas():
    replay = rich_replay()
    events = extract_report_events(replay)
    selected = select_highlights(events)
    assert {e['kind'] for e in selected} == {'expansion', 'full_roster', 'level_9', 'equipment', 'boss_victory'}
    assert {'recruit', 'craft', 'upgrade', 'gold_spent', 'experience_spent', 'tactics', 'first_defeat'} <= {e['kind'] for e in events}
    equipment = next(e for e in selected if e['kind'] == 'equipment')
    assert equipment['label'] == '测试骑士装上终局主手「终局长剑」'
    assert next(e for e in selected if e['kind'] == 'boss_victory')['label'] == '首次击败首领：山林首领'
    assert next(e for e in selected if e['kind'] == 'full_roster')['label'] == '首次达成 2 人满编'
    assert len({e['id'] for e in selected}) == len(selected)


def test_every_high_tier_or_explicit_exclusive_choice_survives_without_a_twelve_item_cap():
    replay = rich_replay()
    upgrades = [{'upgrade_id': f'high-{i}', 'name': f'构造高阶天赋{i}', 'branch': 'martial', 'tier': 3} for i in range(13)]
    upgrades += [{'upgrade_id': 'choice', 'name': '构造互斥选择', 'branch': 'survival', 'tier': 2,
                  'exclusive_upgrade_ids': ['alternative']},
                 {'upgrade_id': 'unknown-tier', 'name': '名字自称终阶', 'branch': 'arcane'}]
    add_upgrades(replay, upgrades)
    events = extract_report_events(replay)
    before = deepcopy(events)
    selected = select_highlights(events)
    assert events == before
    selected_upgrades = [e for e in selected if e['kind'] == 'upgrade']
    assert len(selected_upgrades) == 14
    assert len(selected) > 12
    assert all(any(item['name'] in e['label'] for e in selected_upgrades) for item in upgrades[:-1])
    assert not any('名字自称终阶' in e['label'] for e in selected)
    assert '解锁构造高阶天赋0（武备第 3 阶）' in [e['label'] for e in selected]
    assert any('选择互斥天赋：构造互斥选择' in e['label'] for e in selected)
    assert [e['turn'] for e in selected] == sorted(e['turn'] for e in selected)
    html = render_run_report(replay)
    fragment = html.split('<ol class="milestones">', 1)[1].split('</ol>', 1)[0]
    assert len([tag for tag, _ in Document(fragment).tags if tag == 'h3']) == len(selected)


def test_final_weapon_is_not_repeated_for_crafting_re_equipping_or_ordinary_final_armor():
    replay = rich_replay()
    first, second = replay['turns']
    prepared = first['steps'][2]['observation_after']
    armor = {'instance_id': 'armor', 'name': '终局普通护甲', 'template_id': 'leather_armor'}
    for observation in [prepared, first['steps'][3]['observation_after']]:
        observation['adventurers'][0]['equipment_slots'].append({'slot': 'armor', 'item': armor})
    # 同一把终局主手先卸下再穿回，只保留最后一次确认穿戴。
    removed = deepcopy(prepared)
    removed['adventurers'][0]['equipment_slots'][0]['item'] = None
    restored = deepcopy(prepared)
    second['observation_before'] = deepcopy(prepared)
    second['steps'][:0] = [
        {'type': 'tool_result', 'name': 'unequip_item', 'result': {'ok': True}, 'observation_after': removed},
        {'type': 'tool_result', 'name': 'equip_item', 'result': {'ok': True}, 'observation_after': restored},
    ]
    second['steps'][-1]['observation_after'] = restored
    replay['final_observation'] = restored
    selected = select_highlights(extract_report_events(replay))
    equipment = [e for e in selected if e['kind'] == 'equipment']
    assert len(equipment) == 1
    assert equipment[0]['turn'] == 2 and equipment[0]['evidence']['step'] == 2
    assert '终局长剑' in equipment[0]['label']
    assert '终局普通护甲' not in equipment[0]['label'] + equipment[0]['detail']
    assert not any(e['kind'] == 'craft' for e in selected)


def test_weapon_and_mark_from_one_submission_share_a_highlight_but_keep_both_names():
    replay = rich_replay()
    mark = {'instance_id': 'mark', 'name': '构造胜者印记', 'template_id': 'example_mark'}
    for observation in [replay['turns'][0]['steps'][2]['observation_after'], replay['turns'][0]['steps'][3]['observation_after'],
                        replay['turns'][1]['observation_before'], replay['turns'][1]['steps'][0]['observation_after'], replay['final_observation']]:
        member = observation['adventurers'][0]
        member['equipment_slots'] = [s for s in member['equipment_slots'] if s['slot'] != 'ultimate'] + [{'slot': 'ultimate', 'item': mark}]
    selected = select_highlights(extract_report_events(replay))
    equipment = [e for e in selected if e['kind'] == 'equipment']
    assert len(equipment) == 1
    assert '主手「终局长剑」' in equipment[0]['label'] and '印记「构造胜者印记」' in equipment[0]['label']


def test_each_new_roster_capacity_has_its_own_first_full_roster_and_specific_title():
    replay = rich_replay()
    first, second = replay['turns']
    for observation in [first['steps'][2]['observation_after'], first['steps'][3]['observation_after'],
                        second['observation_before'], second['steps'][0]['observation_after'], replay['final_observation']]:
        observation['party_size_limit'] = 3
    filled = deepcopy(replay['final_observation'])
    filled['adventurers'].append({'adventurer_id': 'third', 'name': '第三位成员', 'level': 1})
    second['steps'].insert(0, {'type': 'tool_result', 'name': 'recruit_adventurer', 'result': {'ok': True}, 'observation_after': filled})
    second['steps'][-1]['observation_after'] = filled
    replay['final_observation'] = filled
    selected = select_highlights(extract_report_events(replay))
    assert [e['label'] for e in selected if e['kind'] == 'full_roster'] == ['首次达成 2 人满编', '首次达成 3 人满编']
    expansion = next(e for e in selected if e['kind'] == 'expansion')
    assert '2 → 3 人' in expansion['label']


def test_batch_expansion_does_not_attribute_total_capacity_to_each_individual_unlock():
    replay = rich_replay()
    add_upgrades(replay, [{'upgrade_id': 'roster_expansion_2', 'name': '扩编 II'}])
    for observation in [replay['turns'][0]['steps'][2]['observation_after'], replay['turns'][0]['steps'][3]['observation_after'],
                        replay['turns'][1]['observation_before'], replay['turns'][1]['steps'][0]['observation_after'], replay['final_observation']]:
        observation['party_size_limit'] = 5
    selected = select_highlights(extract_report_events(replay))
    expansions = [e for e in selected if e['kind'] == 'expansion']
    assert len(expansions) == 2
    assert all('2 → 5' not in e['label'] for e in expansions)


def test_last_weapon_update_does_not_erase_an_earlier_mark_from_the_same_submission():
    replay = rich_replay()
    mark = {'instance_id': 'mark', 'name': '构造印记', 'template_id': 'mark'}
    for observation in [replay['turns'][0]['steps'][2]['observation_after'], replay['turns'][0]['steps'][3]['observation_after'],
                        replay['turns'][1]['observation_before'], replay['turns'][1]['steps'][0]['observation_after'], replay['final_observation']]:
        member = observation['adventurers'][0]
        member['equipment_slots'] = [s for s in member['equipment_slots'] if s['slot'] != 'ultimate'] + [{'slot': 'ultimate', 'item': mark}]
    removed = deepcopy(replay['final_observation'])
    removed['adventurers'][0]['equipment_slots'][0]['item'] = None
    restored = deepcopy(replay['final_observation'])
    replay['turns'][1]['steps'][:0] = [
        {'type': 'tool_result', 'name': 'unequip_item', 'result': {'ok': True}, 'observation_after': removed},
        {'type': 'tool_result', 'name': 'equip_item', 'result': {'ok': True}, 'observation_after': restored},
    ]
    selected = select_highlights(extract_report_events(replay))
    equipment = [e for e in selected if e['kind'] == 'equipment']
    assert len(equipment) == 2
    assert '印记「构造印记」' in equipment[0]['label'] and '终局长剑' not in equipment[0]['detail']
    assert '主手「终局长剑」' in equipment[1]['label'] and '构造印记' not in equipment[1]['detail']


def test_missing_action_snapshot_keeps_equipment_title_and_evidence_uncertain():
    replay = rich_replay()
    for step in replay['turns'][0]['steps']:
        step.pop('observation_after')
    selected = select_highlights(extract_report_events(replay))
    equipment = next(e for e in selected if e['kind'] == 'equipment')
    assert equipment['label'] == '测试骑士已装备终局主手「终局长剑」'
    assert equipment['evidence'] == {'source': 'next_opening', 'turn': 2}


def battle_replay():
    enemies = [
        {'monster_id': 'boss-1', 'archetype_id': 'boss', 'name': '构造首领', 'tier': 'boss'},
        {'monster_id': 'c1', 'archetype_id': 'challenge-1', 'name': '同名挑战', 'tier': 'challenge'},
        {'monster_id': 'c2', 'archetype_id': 'challenge-2', 'name': '同名挑战', 'tier': 'challenge'},
    ]
    state = {'adventurers': [{'adventurer_id': 'hero', 'name': '测试角色', 'level': 1}], 'monsters': enemies}
    turns = []
    for number in (1, 2):
        turns.append({'turn': number, 'status': 'completed', 'observation_before': state, 'steps': [
            {'type': 'tool_result', 'name': 'end_turn', 'result': {'ok': True, 'turn_result': {'battles': [
                {'adventurer_id': 'hero', 'monster_id': e['monster_id'], 'won': True} for e in enemies
            ]}}, 'observation_after': state},
        ]})
    return {'kind': 'llm_replay', 'status': 'completed', 'turns': turns, 'final_observation': state}


def test_first_boss_and_each_distinct_challenge_are_kept_not_deduplicated_by_display_name():
    replay = battle_replay()
    expected_curve = extract_milestones(replay, {1, 2})
    events = extract_report_events(replay)
    selected = select_highlights(events)
    assert len([e for e in events if e['kind'] == 'boss_victory']) == 2
    assert len([e for e in events if e['kind'] == 'challenge_victory']) == 4
    assert [e['label'] for e in selected if e['kind'] == 'boss_victory'] == ['首次击败首领：构造首领']
    assert len([e for e in selected if e['kind'] == 'challenge_victory']) == 2
    assert all(e['label'] == '通关挑战：同名挑战' for e in selected if e['kind'] == 'challenge_victory')
    assert extract_milestones(replay, {1, 2}) == expected_curve


def rank_replay(values):
    return {'kind': 'llm_replay', 'status': 'completed', 'turns': [
        {'turn': number, 'status': 'completed', 'steps': [], 'score': 99,
         **({'rank_score': value} if value is not None else {})}
        for number, value in enumerate(values, 1)
    ], 'score': {'rank_score': 999999, 'score': 100}}


def test_rank_extrema_only_use_consecutive_recorded_ranks_and_have_score_field_evidence():
    replay = rank_replay([0, 100, None, 1000, 300, 400, 400])
    replay['turns'].insert(1, {'turn': 1, 'status': 'rolled_back', 'rank_score': 10000, 'steps': []})
    events = extract_report_events(replay)
    selected = select_highlights(events)
    assert [(e['kind'], e['turn']) for e in selected] == [('rank_rise', 2), ('rank_drop', 5)]
    assert '+100' in selected[0]['label'] and '-700' in selected[1]['label']
    assert selected[0]['evidence'] == {'source': 'rank_score', 'from_turn': 1, 'turn': 2}
    assert '1,000' in selected[1]['detail'] and '300' in selected[1]['detail']
    html = render_run_report(replay)
    assert 'T1 / T2' in html and 'rank_score' in html and '因果' in html
    assert '999,999' not in ' '.join(e['detail'] for e in events)


def test_rank_extrema_do_not_cross_missing_turns_mixed_protocols_or_invent_motion():
    for values in ([None, None], [0, 0, 0], [1, 1.000000001]):
        assert select_highlights(extract_report_events(rank_replay(values))) == []
    gap = rank_replay([100, 1000])
    gap['turns'][1]['turn'] = 3
    assert select_highlights(extract_report_events(gap)) == []
    mixed = rank_replay([100, 1000])
    mixed['data'] = {'protocol_status': 'mixed'}
    assert select_highlights(extract_report_events(mixed)) == []


def test_no_important_nodes_has_an_honest_empty_selection_and_preserves_full_history():
    replay = rich_replay()
    replay['turns'] = [replay['turns'][0]]
    replay['turns'][0]['steps'] = replay['turns'][0]['steps'][:1]
    replay['turns'][0]['observation_before']['party_size_limit'] = 6
    replay['turns'][0]['steps'][0]['observation_after']['party_size_limit'] = 6
    replay.pop('final_observation')
    doc = Document(render_run_report(replay))
    assert '没有符合精选条件的节点' in doc.text
    assert '完整历程' in doc.text and '林地射手加入' in doc.text
    assert '最多 12 条' not in doc.text
