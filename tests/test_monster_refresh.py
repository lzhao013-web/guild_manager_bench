from copy import deepcopy
from dataclasses import replace
from pathlib import Path

import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

from guild_manager_bench.bench.llm.forecast import query_forecast
from guild_manager_bench.bench.llm.harness import TurnToolHarness
from guild_manager_bench.bench.llm.prompts import build_system_prompt, build_turn_prompt
from guild_manager_bench.bench.llm.runner import _format_tool_result_for_model
from guild_manager_bench.bench.llm.tools import GuildManagerTools
from guild_manager_bench.game.actions import EndTurnAction, RefreshMonstersAction
from guild_manager_bench.game.engine import (
    GameError, apply_preparation_action, end_turn, monster_refresh_cost,
    new_game, preview_monster_refresh, spawn_monsters, spawn_recruit_candidates,
)
from guild_manager_bench.game.loader import load_game_definition
from guild_manager_bench.runtime.action_codec import decode_preparation_action, encode_preparation_action
from guild_manager_bench.runtime.observation import build_observation
from guild_manager_bench.runtime.session import GameSession, _restore_from_observation, _serialize_state, _deserialize_state

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def definition():
    return load_game_definition(ROOT / 'data/presets/advanced')


def at_turn(d, turn=1, gold=10000):
    return replace(new_game(d), turn=turn, gold=gold,
        current_monsters=spawn_monsters(d,turn), recruit_candidates=spawn_recruit_candidates(d,turn))


@pytest.fixture
def game(definition):
    tools=GuildManagerTools(definition)
    sid=tools.start_session('refresh')['session_id']
    tools._sessions[sid].state=at_turn(definition)
    return tools,sid


@pytest.mark.parametrize('turn,cost',[(1,15),(7,15),(8,30),(17,30),(18,50),(29,50),(30,100),(34,100)])
def test_preset_price_boundaries_and_doubling(definition,turn,cost):
    state=at_turn(definition,turn)
    assert monster_refresh_cost(definition,state)==cost
    once=apply_preparation_action(definition,state,RefreshMonstersAction())
    twice=apply_preparation_action(definition,once,RefreshMonstersAction())
    assert once.gold==state.gold-cost and once.monster_refresh_count==1
    assert monster_refresh_cost(definition,once)==cost*2
    assert twice.gold==state.gold-cost*3 and twice.monster_refresh_count==2
    assert monster_refresh_cost(definition,twice)==cost*4


@pytest.mark.parametrize('prices',[
    [{'from_turn':2,'gold':15}],
    [{'from_turn':1,'gold':0}],
    [{'from_turn':1,'gold':True}],
    [{'from_turn':1,'gold':15},{'from_turn':1,'gold':30}],
    [{'from_turn':1,'gold':15},{'from_turn':18,'gold':50},{'from_turn':8,'gold':30}],
])
def test_loader_rejects_invalid_price_schedule(tmp_path,prices):
    source=ROOT/'data/presets/advanced'
    for p in source.glob('*.yaml'):
        (tmp_path/p.name).write_bytes(p.read_bytes())
    p=tmp_path/'game.yaml'
    data=yaml.safe_load(p.read_text(encoding='utf-8'))
    data['rules']['monster_refresh']['base_prices']=prices
    p.write_text(yaml.safe_dump(data,allow_unicode=True),encoding='utf-8')
    with pytest.raises((ValueError,TypeError)):
        load_game_definition(tmp_path)


def test_loader_uses_custom_preset_prices_without_code_changes(tmp_path):
    for p in (ROOT/'data/presets/advanced').glob('*.yaml'):
        (tmp_path/p.name).write_bytes(p.read_bytes())
    p=tmp_path/'game.yaml'
    data=yaml.safe_load(p.read_text(encoding='utf-8'))
    data['rules']['monster_refresh']['base_prices']=[{'from_turn':1,'gold':23},{'from_turn':4,'gold':61}]
    p.write_text(yaml.safe_dump(data,allow_unicode=True),encoding='utf-8')
    d=load_game_definition(tmp_path)
    assert monster_refresh_cost(d,at_turn(d,3))==23
    assert monster_refresh_cost(d,at_turn(d,4))==61


@pytest.mark.parametrize('preset',['default','full'])
def test_refresh_disabled_for_other_presets(preset):
    d=load_game_definition(ROOT/'data/presets'/preset)
    tools=GuildManagerTools(d)
    assert not {'preview_monster_refresh','refresh_monsters'} & {s['name'] for s in tools.list_tool_schemas()}
    assert 'monster_refresh' not in build_observation(d,new_game(d))
    with pytest.raises(GameError,match='未启用'):
        preview_monster_refresh(d,new_game(d))


def test_preview_is_pure_repeatable_and_does_not_require_gold(definition):
    state=at_turn(definition,gold=0)
    before=_serialize_state(state)
    a=preview_monster_refresh(definition,state)
    b=preview_monster_refresh(definition,state)
    assert a==b and _serialize_state(state)==before
    result=GameSession(definition=definition,state=state).preview_monster_refresh()
    text=_format_tool_result_for_model('preview_monster_refresh',result)
    assert '仅预览' in text and '金币不足，不能实际刷新。' in text
    with pytest.raises(GameError,match='金币不足'):
        apply_preparation_action(definition,state,RefreshMonstersAction())
    assert _serialize_state(state)==before


def test_locks_preserve_identity_and_do_not_change_other_slot_candidates(definition):
    state=at_turn(definition,18)
    no_locks=preview_monster_refresh(definition,state)
    first=state.current_monsters[0]
    locked=preview_monster_refresh(definition,state,(first.monster_id,))
    assert locked[0] is first
    assert locked[1:]==no_locks[1:]
    assert len(locked)==len(state.current_monsters)
    result=apply_preparation_action(definition,state,RefreshMonstersAction((first.monster_id,)))
    assert result.current_monsters==locked
    again=preview_monster_refresh(definition,result,(first.monster_id,))
    assert again[0] is first
    assert not ({m.monster_id for m in again[1:]} & {m.monster_id for m in no_locks})


def test_rejections_are_atomic_and_all_locked_is_not_paid(game):
    tools,sid=game
    before=tools.get_state(sid)
    ids=[m.monster_id for m in before.current_monsters]
    for locked in ([ids[0],ids[0]],['missing'],['future:T2:monsters:1'],ids,'invalid',None,[True]):
        result=tools.refresh_monsters(sid,locked)
        assert not result['ok'],result
        assert tools.get_state(sid) is before


def test_final_preparation_and_finished_game_reject_refresh(definition):
    for turn in (35,36):
        state=at_turn(definition,turn)
        with pytest.raises(GameError):preview_monster_refresh(definition,state)
        with pytest.raises(GameError):apply_preparation_action(definition,state,RefreshMonstersAction())
        assert not build_observation(definition,state)['monster_refresh']['available']


def test_challenges_are_automatically_retained_and_not_duplicated(definition):
    state=at_turn(definition,30)
    original=tuple(m for m in state.current_monsters if m.tier=='challenge')
    updated=apply_preparation_action(definition,state,RefreshMonstersAction())
    assert tuple(m for m in updated.current_monsters if m.tier=='challenge')==original
    with pytest.raises(GameError,match='自动保留'):
        preview_monster_refresh(definition,state,(original[0].monster_id,))
    cleared=replace(state,defeated_challenge_ids=frozenset({'doom_oracle'}),
        current_monsters=spawn_monsters(definition,30,defeated_challenge_ids=frozenset({'doom_oracle'})))
    again=apply_preparation_action(definition,cleared,RefreshMonstersAction())
    assert {m.archetype_id for m in again.current_monsters if m.tier=='challenge'}=={'iron_colossus','blade_tyrant'}


@pytest.mark.parametrize('seed',[0,42,9876])
def test_refresh_does_not_affect_next_turn_forecast_recruits_or_locks(definition,seed):
    d=replace(definition,rules=replace(definition.rules,seed=seed))
    original=at_turn(d,18)
    forecast=query_forecast(d,original,kind='monsters',start_turn=19,detail='summary',limit=10)
    state=original
    for _ in range(3):
        state=apply_preparation_action(d,state,RefreshMonstersAction((original.current_monsters[0].monster_id,)))
    assert query_forecast(d,state,kind='monsters',start_turn=19,detail='summary',limit=10)==forecast
    next_state=end_turn(d,state,EndTurnAction()).state
    assert next_state.current_monsters==spawn_monsters(d,19)
    assert next_state.recruit_candidates==spawn_recruit_candidates(d,19)
    assert next_state.monster_refresh_count==0
    assert monster_refresh_cost(d,next_state)==50


def test_codec_and_both_save_formats_preserve_refresh_sequence(definition):
    action=RefreshMonstersAction()
    assert decode_preparation_action(encode_preparation_action(action))==action
    state=apply_preparation_action(definition,at_turn(definition),action)
    raw=_deserialize_state(_serialize_state(state))
    observation=_restore_from_observation(definition,build_observation(definition,state))
    saved=GameSession.from_export(definition,{'_state':_serialize_state(state)})
    saved_observation=GameSession.from_export(definition,{'final_observation':build_observation(definition,state)})
    for restored in (raw,observation,saved.state,saved_observation.state):
        assert restored.monster_refresh_count==1
        assert restored.current_monsters==state.current_monsters
        assert preview_monster_refresh(definition,restored)==preview_monster_refresh(definition,state)
        assert apply_preparation_action(definition,restored,action)==apply_preparation_action(definition,state,action)


def test_preview_refs_are_not_real_ids_and_budget_is_not_battle_preview(game):
    tools,sid=game
    harness=TurnToolHarness(tools,sid,max_tool_calls=20)
    original=tools.get_state(sid)
    refs=deepcopy(harness._turn_refs)
    events=deepcopy(tools.get_events(sid))
    result=harness.call_tool('preview_monster_refresh',{'locked_monster_ids':[1]})
    assert result['ok'] and result['simulation_only']
    assert result['tool_budget']['used']==1 and result['tool_budget']['battle_preview_used']==0
    assert tools.get_state(sid) is original and tools.get_events(sid)==events
    assert refs==harness._turn_refs
    assert all('monster_id' not in m for m in result['monsters'])
    assert all(m['refresh_ref'].startswith('refresh:') for m in result['monsters'])
    text=_format_tool_result_for_model('preview_monster_refresh',result)
    assert '仅预览' in text and '不可用于' in text
    assert not harness.call_tool('refresh_monsters',{'locked_monster_ids':[result['monsters'][1]['refresh_ref']]})['ok']
    assert tools.get_state(sid) is original


def test_real_refresh_allocates_new_refs_and_old_targets_cannot_be_used(game):
    tools,sid=game
    harness=TurnToolHarness(tools,sid,max_tool_calls=20)
    assert harness.call_tool('recruit_adventurer',{'candidate_id':1})['ok']
    before=tools.get_state(sid)
    preview=harness.call_tool('preview_monster_refresh',{'locked_monster_ids':[1]})
    result=harness.call_tool('refresh_monsters',{'locked_monster_ids':[1]})
    assert result['ok'] and not result.get('simulation_only',False)
    assert tools.get_state(sid).gold==before.gold-15
    assert harness._turn_refs['monster'][before.current_monsters[0].monster_id]==1
    new=tools.get_state(sid).current_monsters[1]
    assert harness._turn_refs['monster'][new.monster_id]>len(before.current_monsters)
    assert result['monsters'][1]['stats']==preview['monsters'][1]['stats']
    text=_format_tool_result_for_model('refresh_monsters',result)
    assert new.monster_id not in text
    assert f"{harness._turn_refs['monster'][new.monster_id]} " in text
    stale=harness.call_tool('refresh_monsters',{'locked_monster_ids':[2]})
    assert not stale['ok'] and tools.get_state(sid).monster_refresh_count==1
    snapshot=tools.get_state(sid)
    assert not harness.call_tool('end_turn',{'hunts':[{'adventurer_id':1,'monster_id':2}]})['ok']
    assert tools.get_state(sid) is snapshot


def test_refresh_tools_respect_normal_call_budget(game):
    tools,sid=game
    harness=TurnToolHarness(tools,sid,max_tool_calls=1)
    assert harness.call_tool('preview_monster_refresh',{'locked_monster_ids':[]})['ok']
    before=tools.get_state(sid)
    assert not harness.call_tool('refresh_monsters',{'locked_monster_ids':[]})['ok']
    assert tools.get_state(sid) is before


def test_preparation_plan_can_pay_refresh_but_never_changes_real_board(game):
    from guild_manager_bench.bench.llm.preparation_plan import simulate_preparation_plan
    tools,sid=game
    before=tools.get_state(sid)
    plan=[{'tool':'refresh_monsters','arguments':{'locked_monster_ids':[before.current_monsters[0].monster_id]}}]
    simulated=simulate_preparation_plan(tools.definition,before,plan,tools.list_tool_schemas())
    actual=apply_preparation_action(tools.definition,before,RefreshMonstersAction((before.current_monsters[0].monster_id,)))
    assert simulated==actual and tools.get_state(sid) is before
    assert encode_preparation_action(RefreshMonstersAction())['type']=='refresh_monsters'


def test_observation_and_prompts_explain_cost_without_fake_pool(game):
    tools,sid=game
    obs=tools.get_observation(sid)['observation']
    assert obs['monster_refresh']['next_gold_cost']==15
    turn_prompt=build_turn_prompt(obs)
    assert '刷新：本回合0次，下次15金币。' in turn_prompt
    assert '可先免费预览' not in turn_prompt
    prompt=build_system_prompt(monster_refresh_rules=obs['monster_refresh'])
    assert 'refresh_monsters' in prompt and '翻倍' in prompt
    assert 'preview_monster_refresh 只预览' in prompt and 'refresh_monsters 扣费刷新' in prompt
    assert 'preview=true' not in prompt and 'preview=false' not in prompt
    assert '锁定' in prompt and '不影响' in prompt


def test_api_preview_commit_and_unknown_session(definition):
    from guild_manager_bench.api.routes_actions import actions_router
    from guild_manager_bench.api.store import SessionStore
    from guild_manager_bench.api.websocket import SessionHub
    store=SessionStore(definition)
    session=store.create('api-refresh')
    app=FastAPI()
    app.include_router(actions_router(store,SessionHub()))
    client=TestClient(app)
    before=session.state
    events=list(session.events)
    preview=client.post('/api/sessions/api-refresh/preview-monster-refresh',json={'locked_monster_ids':[]})
    assert preview.status_code==200 and preview.json()['gold_cost']==15
    assert session.state is before and session.events==events
    paid=client.post('/api/sessions/api-refresh/actions',json={'type':'refresh_monsters','locked_monster_ids':[]})
    assert paid.status_code==200
    obs=paid.json()['observation']
    assert obs['gold']==before.gold-15 and obs['monster_refresh']['refreshes_used']==1
    assert [m['stats'] for m in obs['monsters']]==[m['stats'] for m in preview.json()['monsters']]
    current=session.state
    rejected=client.post('/api/sessions/api-refresh/actions',json={
        'type':'refresh_monsters','locked_monster_ids':[before.current_monsters[0].monster_id]})
    assert rejected.status_code==400 and session.state is current
    assert client.post('/api/sessions/missing/preview-monster-refresh',json={}).status_code==404


def test_replay_rebuild_reexecutes_paid_refresh_and_not_preview(definition):
    from guild_manager_bench.bench.llm.runner import rebuild_replay_observations, replay_observations_complete
    tools=GuildManagerTools(definition)
    sid=tools.start_session('replay-refresh')['session_id']
    harness=TurnToolHarness(tools,sid,max_tool_calls=20)
    steps=[]
    for name,args in [
        ('preview_monster_refresh',{'locked_monster_ids':[1]}),
        ('refresh_monsters',{'locked_monster_ids':[1]}),
        ('refresh_monsters',{'locked_monster_ids':[1]}),
        ('end_turn',{'hunts':[]}),
    ]:
        result=harness.call_tool(name,args)
        assert result['ok']
        steps.append({'type':'tool_result','name':name,'arguments':args,'result':result,
                      'content':_format_tool_result_for_model(name,result)})
    replay={'turns':[{'turn':1,'status':'completed','steps':steps}]}
    assert not replay_observations_complete(replay)
    rebuilt=rebuild_replay_observations(replay,data_dir=ROOT/'data/presets/advanced')
    assert replay_observations_complete(rebuilt)
    observations=[s.get('observation_after') for s in rebuilt['turns'][0]['steps']]
    assert observations[0] is None
    assert observations[1]['monster_refresh']['refreshes_used']==1
    assert observations[2]['monster_refresh']['refreshes_used']==2
    assert observations[2]['gold']==175-15-30
    assert observations[3]['monster_refresh']['refreshes_used']==0
    assert observations[3]['monsters']==tools.get_observation(sid)['observation']['monsters']


def test_existing_battle_preview_log_survives_monster_refresh(game):
    tools,sid=game
    harness=TurnToolHarness(tools,sid,max_tool_calls=20)
    assert harness.call_tool('recruit_adventurer',{'candidate_id':1})['ok']
    result=harness.call_tool('preview_battle',{'adventurer_id':1,'monster_id':1})
    assert result['ok']
    battle_id=result['preview']['battle_id']
    log=tools.get_battle_log(sid,battle_id,'events')
    assert harness.call_tool('refresh_monsters',{'locked_monster_ids':[]})['ok']
    assert tools.get_battle_log(sid,battle_id,'events')==log
    assert harness.call_tool('get_battle_log',{'battle_id':battle_id})['tool_budget']['battle_preview_used']==1


def test_refresh_has_separate_preview_and_paid_tools(game):
    tools,sid=game
    schemas={s['name']:s for s in tools.list_tool_schemas()}
    for name in ('preview_monster_refresh','refresh_monsters'):
        schema=schemas[name]['parameters']
        assert schema['required']==['locked_monster_ids']
        assert set(schema['properties'])=={'locked_monster_ids'}
    harness=TurnToolHarness(tools,sid,max_tool_calls=20)
    before=tools.get_state(sid)
    preview=harness.call_tool('preview_monster_refresh',{'locked_monster_ids':[]})
    assert preview['ok'] and preview['simulation_only']
    assert tools.get_state(sid) is before
    for name in ('preview_monster_refresh','refresh_monsters'):
        assert not harness.call_tool(name,{'locked_monster_ids':[],'preview':True})['ok']
        assert tools.get_state(sid) is before
    paid=harness.call_tool('refresh_monsters',{'locked_monster_ids':[]})
    assert paid['ok'] and tools.get_state(sid).gold==before.gold-preview['gold_cost']
    assert [m['stats'] for m in paid['monsters']]==[m['stats'] for m in preview['monsters']]


def test_preparation_plan_rejects_refresh_preview(game):
    from guild_manager_bench.bench.llm.preparation_plan import plan_operations_schema, simulate_preparation_plan
    tools,sid=game
    before=tools.get_state(sid)
    schema=plan_operations_schema(tools.list_tool_schemas())
    variant=next(v for v in schema['items']['anyOf'] if v['properties']['tool']['enum']==['refresh_monsters'])
    assert set(variant['properties']['arguments']['properties'])=={'locked_monster_ids'}
    with pytest.raises(ValueError,match='不允许查询、预览'):
        simulate_preparation_plan(tools.definition,before,[{
            'tool':'preview_monster_refresh','arguments':{'locked_monster_ids':[]}
        }],tools.list_tool_schemas())
    for value in (True,False,None,0,1,'false'):
        with pytest.raises(ValueError,match='未知参数'):
            simulate_preparation_plan(tools.definition,before,[{
                'tool':'refresh_monsters','arguments':{'locked_monster_ids':[],'preview':value}
            }],tools.list_tool_schemas())
    assert tools.get_state(sid) is before


def test_runner_records_only_actual_refresh_as_mutation(game):
    from guild_manager_bench.bench.llm.runner import LlmAgentResponse, LlmRunConfig, LlmToolCall, run_llm_turn, _is_retryable
    tools,sid=game

    class Agent:
        def __init__(self):
            self.responses=iter([
                LlmAgentResponse(tool_calls=(LlmToolCall('preview_monster_refresh',{'locked_monster_ids':[]}),)),
                LlmAgentResponse(tool_calls=(LlmToolCall('refresh_monsters',{'locked_monster_ids':[]}),)),
                LlmAgentResponse(tool_calls=(LlmToolCall('end_turn',{'hunts':[]}),)),
            ])

        def respond(self,**kwargs):
            return next(self.responses)

    trace=run_llm_turn(Agent(),tools,sid,config=LlmRunConfig(archive_dir=None))
    assert trace.status=='completed'
    preview,paid,ended=trace.tool_calls
    assert _is_retryable(replace(trace,tool_calls=[preview]))
    assert not _is_retryable(replace(trace,tool_calls=[paid]))
    assert preview.result['ok'] and preview.result['simulation_only']
    assert '_observation_after' not in preview.result
    assert paid.result['ok'] and paid.result['_observation_after']['monster_refresh']['refreshes_used']==1
    assert ended.result['ok'] and ended.result['_observation_after']['monster_refresh']['refreshes_used']==0
