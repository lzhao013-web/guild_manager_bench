from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import shutil

import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

from guild_manager_bench.bench.llm.harness import TurnToolHarness
from guild_manager_bench.bench.llm.prompts import build_system_prompt, build_turn_prompt
from guild_manager_bench.bench.llm.runner import (
    LlmAgentResponse, LlmRunConfig, LlmToolCall, _format_tool_result_for_model,
    _replay_confirmed_tool_result, run_llm_game,
)
from guild_manager_bench.bench.llm.tools import GuildManagerTools, ToolCallError
from guild_manager_bench.bench.protocols import protocol_metadata, verify_full_release
from guild_manager_bench.game.actions import (
    DismissAction, EndTurnAction, ExchangeMaterialsAction,
    RecruitAction, RefreshMonstersAction,
)
from guild_manager_bench.game.engine import GameError, apply_preparation_action, end_turn, new_game
from guild_manager_bench.game.loader import load_game_definition
from guild_manager_bench.game.presets import list_data_presets, resolve_data_preset
from guild_manager_bench.runtime.action_codec import decode_preparation_action, encode_preparation_action
from guild_manager_bench.runtime.observation import build_observation
from guild_manager_bench.runtime.session import GameSession, _serialize_state

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / 'data/presets/advanced'


@pytest.fixture(scope='module')
def definition():
    return load_game_definition(DATA)


@pytest.fixture
def state(definition):
    game = new_game(definition)
    game = apply_preparation_action(definition, game, RecruitAction(game.recruit_candidates[0].candidate_id))
    wounded = replace(game.adventurers[0], resources=replace(game.adventurers[0].resources, current_hp=17, current_mp=0))
    return replace(game, turn=12, gold=1000, adventurers=(wounded,),
                   materials={'iron_ore': 40, 'bone': 40, 'herb': 40, 'sharp_claw': 40})


def toolbox(definition, state, sid='exchange'):
    tools = GuildManagerTools(definition)
    tools.start_session(sid)
    tools._sessions[sid].state = state
    return tools, TurnToolHarness(tools, sid, max_tool_calls=20)


@pytest.mark.parametrize('material,quantity,gold,inputs,points', [
    ('mithril_shard', 3, 105, {'iron_ore': 12, 'bone': 6}, 6),
    ('spider_silk', 2, 80, {'herb': 8, 'sharp_claw': 4}, 4),
    ('beast_hide', 6, 120, {'bone': 12, 'sharp_claw': 12}, 6),
])
def test_exact_cost_and_only_exchange_resources_change(definition, state, material, quantity, gold, inputs, points):
    action = ExchangeMaterialsAction(material, quantity)
    assert decode_preparation_action(encode_preparation_action(action)) == action
    actual = apply_preparation_action(definition, state, action)
    expected = dict(state.materials)
    for key, amount in inputs.items():
        expected[key] -= amount
    expected[material] = quantity
    assert actual.gold == state.gold-gold
    assert dict(actual.materials) == expected
    assert actual.exchange_used_points == points
    assert replace(actual, gold=state.gold, materials=state.materials, exchange_used_points=0) == state
    assert actual.adventurers[0].resources.current_hp == 17
    assert actual.adventurers[0].resources.current_mp == 0


def test_shared_quota_persists_through_turn_recruitment_dismissal_and_refresh(definition, state):
    state = apply_preparation_action(definition, state, ExchangeMaterialsAction('spider_silk', 2))
    state = end_turn(definition, state, EndTurnAction()).state
    candidate = state.recruit_candidates[0].candidate_id
    state = apply_preparation_action(definition, state, RecruitAction(candidate))
    state = apply_preparation_action(definition, state, DismissAction(state.adventurers[-1].adventurer_id))
    state = apply_preparation_action(definition, state, RefreshMonstersAction())
    assert state.exchange_used_points == 4
    state = apply_preparation_action(definition, state, ExchangeMaterialsAction('beast_hide', 2))
    assert state.exchange_used_points == 6
    for material in ['mithril_shard', 'spider_silk', 'beast_hide']:
        with pytest.raises(GameError, match='额度不足'):
            apply_preparation_action(definition, state, ExchangeMaterialsAction(material, 1))


def test_batch_equals_split_and_crafting_discount_does_not_reduce_exchange_cost(definition, state):
    discounted = replace(state, unlocked_upgrade_ids=frozenset({'blacksmith', 'craft_standardization'}))
    batch = apply_preparation_action(definition, discounted, ExchangeMaterialsAction('mithril_shard', 3))
    split = discounted
    for _ in range(3):
        split = apply_preparation_action(definition, split, ExchangeMaterialsAction('mithril_shard', 1))
    assert batch == split
    assert batch.gold == discounted.gold-105


@pytest.mark.parametrize('turn,allowed', [(1, False), (11, False), (12, True), (34, True), (35, True), (36, False)])
def test_opening_and_finished_boundaries(definition, state, turn, allowed):
    at_turn = replace(state, turn=turn)
    if allowed:
        assert apply_preparation_action(definition, at_turn, ExchangeMaterialsAction('mithril_shard', 1)).exchange_used_points == 2
    else:
        with pytest.raises(GameError):
            apply_preparation_action(definition, at_turn, ExchangeMaterialsAction('mithril_shard', 1))


@pytest.mark.parametrize('quantity', [0, -1, True, 1.5, '2'])
def test_invalid_quantities_are_rejected_without_state_change(definition, state, quantity):
    tools, harness = toolbox(definition, state)
    result = harness.call_tool('exchange_materials', {'material_id': 'spider_silk', 'quantity': quantity})
    assert result['ok'] is False
    assert result['tool_budget']['used'] == 1
    assert tools.get_state('exchange') is state


@pytest.mark.parametrize('reason', ['gold', 'materials', 'quota', 'output'])
def test_failed_trade_has_no_partial_payment_or_output(definition, state, reason):
    material, quantity = 'mithril_shard', 2
    if reason == 'gold':
        state = replace(state, gold=69)
    elif reason == 'materials':
        state = replace(state, materials={**state.materials, 'bone': 3})
    elif reason == 'quota':
        state = replace(state, exchange_used_points=4)
    else:
        material = 'siege_core'
    tools, harness = toolbox(definition, state)
    result = harness.call_tool('exchange_materials', {'material_id': material, 'quantity': quantity})
    assert result['ok'] is False
    assert result['tool_budget']['used'] == 1
    assert tools.get_state('exchange') is state
    assert result['event']['type'] == 'action_rejected'


def test_tool_budget_preview_plan_atomic_execution_and_recovery(definition, state):
    tools, harness = toolbox(definition, state)
    operations = [
        {'tool': 'exchange_materials', 'arguments': {'material_id': 'spider_silk', 'quantity': 2}},
        {'tool': 'craft_equipment', 'arguments': {'recipe_id': 'recipe_t2_blood_ring'}, 'as': 'ring'},
    ]
    preview = harness.call_tool('preview_team_power', {'operations': operations})
    assert preview['ok'] and preview['required_calls'] == 2
    assert preview['tool_budget']['used'] == 1
    assert tools.get_state('exchange') is state
    assert any(c['label'] == '兑换剩余额度' and c['before'] == 6 and c['after'] == 2 for c in preview['changes'])
    assert 'rank_score_delta' not in preview  # Midgame preview must not leak rank.
    result = harness.call_tool('execute_plan', {'plan_id': preview['plan_id']})
    after = tools.get_state('exchange')
    assert result['ok'] and not result['already_executed']
    assert result['tool_budget']['used'] == 1
    assert after.exchange_used_points == 4 and after.gold == state.gold-114
    assert after.materials['spider_silk'] == 0
    assert after.equipment_inventory[-1].template_id == 't2_blood_ring'
    repeated = harness.call_tool('execute_plan', {'plan_id': preview['plan_id']})
    assert repeated['already_executed']
    assert tools.get_state('exchange') is after
    assert '兑换剩余额度: 6 -> 2' in _format_tool_result_for_model('execute_plan', result)

    restored_tools, restored = toolbox(definition, state, 'restored')
    _replay_confirmed_tool_result(restored, {'type': 'tool_result', 'name': 'execute_plan', 'result': result})
    _replay_confirmed_tool_result(restored, {'type': 'tool_result', 'name': 'execute_plan', 'result': repeated})
    assert restored_tools.get_state('restored') == after
    assert restored_tools.get_events('restored')['events'][1:][0]['action']['type'] == 'exchange_materials'

    before, events = tools.get_state('exchange'), deepcopy(tools.get_events('exchange'))
    bad = harness.call_tool('preview_team_power', {'operations': [
        {'tool': 'exchange_materials', 'arguments': {'material_id': 'mithril_shard', 'quantity': 1}},
        {'tool': 'exchange_materials', 'arguments': {'material_id': 'beast_hide', 'quantity': 1}},
    ]})
    assert not bad['ok'] and '第 2 步' in bad['error']
    assert tools.get_state('exchange') is before and tools.get_events('exchange') == events


def test_batch_submit_failure_is_atomic_and_direct_calls_use_normal_budget(definition, state):
    session = GameSession(definition, state=state)
    before_events = tuple(session.events)
    with pytest.raises(GameError):
        session.apply_preparations((ExchangeMaterialsAction('spider_silk', 2), ExchangeMaterialsAction('mithril_shard', 2)))
    assert session.state is state and tuple(session.events) == before_events
    tools, _ = toolbox(definition, state)
    harness = TurnToolHarness(tools, 'exchange', max_tool_calls=1)
    ok = harness.call_tool('exchange_materials', {'material_id': 'spider_silk', 'quantity': 2})
    assert ok['ok'] and ok['tool_budget']['used'] == 1 and ok['tool_budget']['battle_preview_used'] == 0
    after = tools.get_state('exchange')
    assert not harness.call_tool('exchange_materials', {'material_id': 'mithril_shard', 'quantity': 1})['ok']
    assert tools.get_state('exchange') is after


@pytest.mark.parametrize('format', ['_state', 'final_observation'])
def test_save_restore_never_refills_quota_and_missing_counter_is_rejected(definition, state, format):
    state = apply_preparation_action(definition, state, ExchangeMaterialsAction('spider_silk', 2))
    payload = _serialize_state(state) if format == '_state' else build_observation(definition, state)
    saved = json.loads(json.dumps({format: payload}))
    session = GameSession.from_export(definition, saved)
    assert session.state.exchange_used_points == 4
    session.end_turn(EndTurnAction())
    session.apply_preparation(ExchangeMaterialsAction('mithril_shard', 1))
    assert session.state.exchange_used_points == 6
    with pytest.raises(GameError, match='额度不足'):
        session.apply_preparation(ExchangeMaterialsAction('beast_hide', 1))
    if format == '_state':
        del saved[format]['exchange_used_points']
    else:
        del saved[format]['material_exchange']['used_points']
    with pytest.raises(ValueError, match='缺少兑换额度'):
        GameSession.from_export(definition, saved)


@pytest.mark.parametrize('format', ['_state', 'final_observation'])
@pytest.mark.parametrize('counter', [-1, True, 1.5, 7])
def test_corrupt_saved_quota_is_rejected(definition, state, counter, format):
    if format == '_state':
        raw = _serialize_state(state)
        raw['exchange_used_points'] = counter
    else:
        raw = build_observation(definition, state)
        raw['material_exchange']['used_points'] = counter
    with pytest.raises(ValueError):
        GameSession.from_export(definition, {format: raw})


@pytest.mark.parametrize('preset', ['default', 'full'])
def test_other_presets_do_not_expose_exchange_or_change_old_observations(preset):
    d = load_game_definition(ROOT/'data/presets'/preset)
    tools = GuildManagerTools(d)
    sid = tools.start_session('legacy')['session_id']
    names = {s['name'] for s in tools.list_tool_schemas()}
    assert 'exchange_materials' not in names
    obs = tools.get_observation(sid)['observation']
    assert 'material_exchange' not in obs and '材料兑换' not in build_turn_prompt(obs)
    with pytest.raises(ToolCallError, match='未知工具'):
        tools.call_tool('exchange_materials', {'session_id': sid, 'material_id': 'spider_silk', 'quantity': 1})
    with pytest.raises(GameError, match='未启用'):
        apply_preparation_action(d, new_game(d), ExchangeMaterialsAction('spider_silk', 1))
    restored = GameSession.from_export(d, {'final_observation': obs})
    assert restored.state.exchange_used_points == 0
    old_state = _serialize_state(tools.get_state(sid))
    del old_state['exchange_used_points']
    assert GameSession.from_export(d, {'_state': old_state}).state.exchange_used_points == 0


def test_schema_and_one_line_turn_status_use_preset_values(definition, state):
    tools, _ = toolbox(definition, state)
    schema = next(s for s in tools.list_tool_schemas() if s['name'] == 'exchange_materials')
    assert schema['parameters']['required'] == ['material_id', 'quantity']
    assert schema['parameters']['properties']['quantity']['minimum'] == 1
    assert schema['parameters']['properties']['material_id']['enum'] == ['mithril_shard','spider_silk','beast_hide']
    assert 'T12' in schema['description'] and 'T35最终准备' in schema['description']
    assert '35金币＋4铁矿石＋2骨头' in schema['description']
    assert '40金币＋4草药＋2利爪' in schema['description']
    assert '20金币＋2骨头＋2利爪' in schema['description']
    for turn, used, text in [(11,0,'材料兑换：未开放（T12起）'),(12,2,'材料兑换：剩余额度4/6点'),(35,6,'材料兑换：额度已用尽（0/6点）')]:
        obs = build_observation(definition, replace(state, turn=turn, exchange_used_points=used))
        prompt = build_turn_prompt(obs)
        assert [line for line in prompt.splitlines() if line.startswith('材料兑换：')] == [text]
        assert '35金币＋4铁矿石' not in prompt
    assert '材料兑换' not in build_system_prompt()
    assert 'exchange_materials' not in build_system_prompt()


def copy_preset(tmp_path):
    for path in DATA.glob('*.yaml'):
        shutil.copyfile(path, tmp_path/path.name)
    path = tmp_path/'game.yaml'
    return path, yaml.safe_load(path.read_text(encoding='utf-8'))


def test_loader_and_schema_share_changed_prices_not_separate_prompt_constants(tmp_path):
    path, data = copy_preset(tmp_path)
    data['rules']['material_exchange']['from_turn'] = 14
    data['rules']['material_exchange']['point_limit'] = 8
    data['rules']['material_exchange']['offers'][0]['gold'] = 47
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding='utf-8')
    definition = load_game_definition(tmp_path)
    game = replace(new_game(definition), turn=14, gold=100, materials={'iron_ore':4,'bone':2})
    changed = apply_preparation_action(definition, game, ExchangeMaterialsAction('mithril_shard',1))
    assert changed.gold == 53
    text = next(s['description'] for s in GuildManagerTools(definition).list_tool_schemas() if s['name']=='exchange_materials')
    assert '47金币' in text and '共享8点' in text and 'T14' in text
    assert '剩余额度6/8点' in build_turn_prompt(build_observation(definition, changed))


@pytest.mark.parametrize('change', ['zero_gold','negative_material','zero_points','bool_limit','late_open','duplicate','core','reverse','empty'])
def test_invalid_exchange_configuration_fails_loading(tmp_path, change):
    path, data = copy_preset(tmp_path)
    rules = data['rules']['material_exchange']
    if change == 'zero_gold': rules['offers'][0]['gold'] = 0
    elif change == 'negative_material': rules['offers'][0]['materials']['iron_ore'] = -1
    elif change == 'zero_points': rules['offers'][0]['points'] = 0
    elif change == 'bool_limit': rules['point_limit'] = True
    elif change == 'late_open': rules['from_turn'] = 36
    elif change == 'duplicate': rules['offers'].append(deepcopy(rules['offers'][0]))
    elif change == 'core': rules['offers'][0]['material_id'] = 'siege_core'
    elif change == 'reverse': rules['offers'][0]['materials'] = {'spider_silk':2}
    elif change == 'empty': rules['offers'] = []
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding='utf-8')
    with pytest.raises(ValueError):
        load_game_definition(tmp_path)


def test_exchange_uses_advanced_and_existing_protocol_versioning(definition, tmp_path):
    assert resolve_data_preset(ROOT/'data', 'advanced').data_dir == DATA
    assert {p.name for p in list_data_presets(ROOT/'data')} == {'default', 'full', 'advanced'}
    assert definition.rules.material_exchange is not None
    path, data = copy_preset(tmp_path)
    config = LlmRunConfig()
    current = protocol_metadata(DATA, definition, config)
    assert protocol_metadata(tmp_path, definition, config) == current

    del data['rules']['material_exchange']
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding='utf-8')
    previous = load_game_definition(tmp_path)
    assert replace(definition.rules, material_exchange=None) == previous.rules
    assert definition.content == previous.content and definition.scoring == previous.scoring
    previous_protocol = protocol_metadata(tmp_path, previous, config)
    assert current['protocol_hash'] != previous_protocol['protocol_hash']
    assert current['benchmark_version'] != previous_protocol['benchmark_version']
    assert current['settings_hash'] == previous_protocol['settings_hash']
    verify_full_release()


def test_action_api_preserves_integer_validation_and_exposes_remaining_quota(definition, state):
    from guild_manager_bench.api.routes_actions import actions_router
    from guild_manager_bench.api.store import SessionStore
    from guild_manager_bench.api.websocket import SessionHub
    store = SessionStore(definition)
    session = store.create('api-exchange')
    session.state = state
    app = FastAPI()
    app.include_router(actions_router(store, SessionHub()))
    with TestClient(app) as client:
        for invalid in [True, 1.5, 0]:
            response = client.post('/api/sessions/api-exchange/actions', json={'type':'exchange_materials','material_id':'spider_silk','quantity':invalid})
            assert response.status_code == 422 and session.state is state
        response = client.post('/api/sessions/api-exchange/actions', json={'type':'exchange_materials','material_id':'spider_silk','quantity':2})
        assert response.status_code == 200
        assert response.json()['observation']['material_exchange']['remaining_points'] == 2
        after = session.state
        response = client.post('/api/sessions/api-exchange/actions', json={'type':'exchange_materials','material_id':'mithril_shard','quantity':2})
        assert response.status_code == 400 and session.state is after


def test_scripted_run_archives_and_resumes_exchange_without_model_requests(tmp_path):
    data_dir = tmp_path/'data'
    data_dir.mkdir()
    path, data = copy_preset(data_dir)
    data['starting']['gold'] = 300
    data['starting']['materials'] = {'herb':20,'sharp_claw':20,'iron_ore':20,'bone':20}
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding='utf-8')
    # A local scripted agent exercises the runner, not an LLM endpoint or balance run.
    class Scripted:
        turn = 0
        def respond(self, *, messages, tools):
            self.turn += 1
            assert 'exchange_materials' in {tool['name'] for tool in tools}
            assert '材料兑换' not in messages[0]['content']
            calls = []
            if self.turn == 12:
                calls.append(LlmToolCall('exchange_materials', {'material_id':'spider_silk','quantity':2}))
            if self.turn == 35:
                calls.append(LlmToolCall('exchange_materials', {'material_id':'mithril_shard','quantity':1}))
            calls.append(LlmToolCall('end_turn', {'hunts':[]}))
            return LlmAgentResponse(tool_calls=tuple(calls))
    config = LlmRunConfig(archive_dir=tmp_path/'archives')
    run = run_llm_game(Scripted(), data_dir=data_dir, config=config)
    assert run.status == 'completed'
    replay_path = Path(run.archive_dir)/'replay.json'
    original = replay_path.read_bytes()
    replay = json.loads(original)
    assert replay['final_observation']['material_exchange']['used_points'] == 6
    step = next(s for s in replay['turns'][11]['steps'] if s.get('type')=='tool_result' and s.get('name')=='exchange_materials')
    assert step['observation_after']['material_exchange']['used_points'] == 4
    class NoCalls:
        def respond(self, **kwargs):
            raise AssertionError('Completed replay must not call an agent')
    resumed = run_llm_game(NoCalls(), data_dir=data_dir, config=config, resume_archive_dir=run.archive_dir)
    assert resumed.status == 'completed'
    assert resumed.final_observation['material_exchange']['used_points'] == 6
