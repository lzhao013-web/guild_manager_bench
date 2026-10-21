from dataclasses import replace

import pytest

from guild_manager_bench.game import engine as E
from guild_manager_bench.game.actions import ConfigureTacticsAction, EndTurnAction, HuntAction, PurchaseUpgradeAction, RefreshMonstersAction
from guild_manager_bench.game.combat import Combatant, run_auto_battle
from guild_manager_bench.game.loader import load_game_definition
from guild_manager_bench.game.models import CombatResources, CombatStats
from guild_manager_bench.game.skills import Skill, SkillCondition, SkillEffect, StatusDefinition
from guild_manager_bench.game.state import AdventurerState, FloatCurve, GameContent, GameDefinition, GameRules, MonsterArchetype, MonsterSpawnRules, RewardBundle
from guild_manager_bench.game.upgrades import GlobalUpgrade
from guild_manager_bench.runtime.observation import build_observation
from guild_manager_bench.runtime.session import GameSession


@pytest.fixture(scope='module')
def advanced():
    return load_game_definition('data/presets/advanced')


def test_approved_tree_has_35_nodes_and_new_cross_branch_requirements(advanced):
    nodes = {u.upgrade_id: u for u in advanced.content.global_upgrades}
    assert len(nodes) == 35
    assert nodes['training_manual_2'].required_upgrade_ids == ('training_manual', 'scout_network')
    assert nodes['trade_network'].required_upgrade_ids == ('craft_standardization', 'scout_network')
    assert nodes['guild_contracts'].required_upgrade_ids == ('trade_network', 'supply_post')
    assert nodes['battle_coordination'].required_upgrade_ids == ('training_grounds', 'scout_network')
    assert nodes['mana_acceleration'].required_upgrade_ids == ('mana_harmony', 'meditation_hall')
    assert sum(len(u.required_upgrade_ids) for u in nodes.values()) == 41
    assert nodes['war_academy'].required_upgrade_ids == ('reinforced_forge', 'elite_hunters')
    assert nodes['roster_expansion_2'].required_upgrade_ids == ('roster_expansion_1',)
    assert [nodes[k].hunt_experience_bonus_percent for k in ('training_manual', 'training_manual_2', 'training_manual_3')] == [6, 8, 12]


@pytest.fixture
def economics():
    stats = CombatStats(hp=100, mp=20, attack=1000, defense=100, speed=100, recovery=0)
    return GameDefinition(
        rules=GameRules(max_turns=4, seed=7, monster_spawn=MonsterSpawnRules(count_curve=FloatCurve(base=1))),
        content=GameContent(
            adventurers=(AdventurerState('hero', '测试员', stats, CombatResources.full(stats)),),
            monster_archetypes=(MonsterArchetype('slime', '史莱姆', CombatStats(hp=10, mp=0, attack=1, defense=0, speed=1, recovery=0), base_reward=RewardBundle(gold=101, experience=61, materials={'herb': 3})),),
            global_upgrades=(
                GlobalUpgrade('trade', '委托', 0, elite_boss_gold_bonus_percent=18, monster_refresh_discount_percent=10),
                GlobalUpgrade('guild', '商路', 0, hunt_gold_bonus_percent=10, monster_refresh_discount_percent=10),
                GlobalUpgrade('xp1', '教范', 0, hunt_experience_bonus_percent=6),
                GlobalUpgrade('xp2', '进阶', 0, hunt_experience_bonus_percent=8),
                GlobalUpgrade('xp3', '大师', 0, hunt_experience_bonus_percent=12),
            ),
        ),
    )


@pytest.mark.parametrize('tier,gold', [('normal', 111), ('elite', 129), ('boss', 129), ('challenge', 111)])
def test_hunt_gold_and_xp_round_once_and_preview_matches_settlement(economics, tier, gold):
    d = economics
    state = E.new_game(d)
    for key in ('trade', 'guild', 'xp1', 'xp2', 'xp3'):
        state = E.apply_preparation_action(d, state, PurchaseUpgradeAction(key))
    monster = replace(state.current_monsters[0], tier=tier)
    state = replace(state, current_monsters=(monster,))
    obs = build_observation(d, state)
    assert obs['monsters'][0]['base_reward']['gold'] == 101
    assert obs['monsters'][0]['reward'] == {'gold': gold, 'experience': 76, 'materials': {'herb': 3}}
    preview = E.preview_battle(d, state, adventurer_id='hero', monster_id=monster.monster_id)
    result = E.end_turn(d, state, EndTurnAction(hunts=(HuntAction('hero', monster.monster_id),)))
    assert preview.reward == result.battles[0].reward
    assert result.state.gold == state.gold + gold
    assert result.state.experience_pool == 76
    assert result.state.materials['herb'] == 3


def test_zero_reward_stays_zero_and_failed_hunt_has_no_income(economics):
    d = economics
    state = E.new_game(d)
    for key in ('trade', 'guild', 'xp1', 'xp2', 'xp3'):
        state = E.apply_preparation_action(d, state, PurchaseUpgradeAction(key))
    zero = RewardBundle(materials={'siege_core': 1})
    assert E.effective_hunt_reward(d, state, zero, tier='challenge') == zero
    monster = state.current_monsters[0]
    hero = replace(state.adventurers[0], resources=CombatResources(0, 0))
    # A live actor who cannot beat the target receives no money or XP.
    hero = replace(hero, base_stats=CombatStats(hp=1, mp=0, attack=1, defense=0, speed=1, recovery=0), resources=CombatResources(1, 0))
    monster = replace(monster, stats=CombatStats(hp=1000, mp=0, attack=100, defense=100, speed=100, recovery=0), tier='boss')
    state = replace(state, adventurers=(hero,), current_monsters=(monster,))
    result = E.end_turn(d, state, EndTurnAction(hunts=(HuntAction('hero', monster.monster_id),)))
    assert not result.battles[0].won and result.battles[0].reward == RewardBundle()
    assert result.state.gold == state.gold and result.state.experience_pool == state.experience_pool


def test_refresh_discount_is_rounded_before_doubling_and_survives_restore(advanced):
    nodes = {u.upgrade_id: u for u in advanced.content.global_upgrades}
    state = replace(E.new_game(advanced), gold=10000, experience_pool=10000,
        materials={m.material_id: 10000 for u in nodes.values() for m in u.material_costs})
    for key in ('blacksmith', 'craft_standardization', 'scout_network', 'trade_network', 'supply_post', 'guild_contracts'):
        state = E.apply_preparation_action(advanced, state, PurchaseUpgradeAction(key))
    # 10 * 80% = 8, then 16; next turn resets to 8.
    assert E.monster_refresh_cost(advanced, state) == 8
    refreshed = E.apply_preparation_action(advanced, state, RefreshMonstersAction())
    assert E.monster_refresh_cost(advanced, refreshed) == 16
    session = GameSession(advanced, state=refreshed)
    restored = GameSession.from_export(advanced, session.export_replay())
    assert restored.observation() == session.observation()
    assert restored.preview_monster_refresh()['gold_cost'] == 16
    nxt = E.end_turn(advanced, refreshed, EndTurnAction()).state
    assert E.monster_refresh_cost(advanced, nxt) == 8


@pytest.mark.parametrize('field', ['hunt_gold_bonus_percent', 'elite_boss_gold_bonus_percent', 'monster_refresh_discount_percent'])
def test_new_economy_fields_reject_negative_values(field):
    with pytest.raises(ValueError):
        GlobalUpgrade('bad', '错误', 0, **{field: -1})


@pytest.mark.parametrize('case', ['individual', 'combined'])
def test_refresh_discount_cannot_exceed_100(case, economics):
    with pytest.raises(ValueError):
        if case == 'individual':
            GlobalUpgrade('bad', '错误', 0, monster_refresh_discount_percent=101)
        else:
            replace(economics.content, global_upgrades=(GlobalUpgrade('a', 'A', 0, monster_refresh_discount_percent=60), GlobalUpgrade('b', 'B', 0, monster_refresh_discount_percent=50)))


@pytest.mark.parametrize('trigger,once,free,effect', [
    ('unknown', True, False, SkillEffect('heal', 10, target='self')),
    ('action_start', False, False, SkillEffect('heal', 10, target='self')),
    ('action_start', True, True, SkillEffect('heal', 10, target='self')),
    ('action_start', True, False, SkillEffect('true_damage', 10)),
])
def test_invalid_automatic_recovery_definitions_are_rejected(trigger, once, free, effect):
    with pytest.raises(ValueError):
        Skill('bad', '错误', 'active', SkillCondition('always'), (effect,), trigger=trigger, once_per_battle=once, free=free)


@pytest.mark.parametrize('value,minimum', [(-.1, 0), (1.1, 0), (.3, -1)])
def test_invalid_percent_mp_recovery_is_rejected(value, minimum):
    with pytest.raises(ValueError):
        SkillEffect('mp_restore_percent', value, target='self', minimum=minimum)


@pytest.mark.parametrize('skill_id,target_hp,mp,expected', [
    ('talent_battle_coordination', 500, 100, 105),
    ('talent_battle_coordination', 499, 100, 100),
    ('talent_mana_harmony', 1000, 50, 105),
    ('talent_mana_harmony', 1000, 49, 100),
])
def test_mixed_flat_and_multiplier_order_at_condition_boundaries(advanced, skill_id, target_hp, mp, expected):
    skills = {s.skill_id: s for u in advanced.content.global_upgrades for s in u.skills}
    stats = CombatStats(hp=1000, mp=100, attack=100, defense=0, speed=100, recovery=0)
    foe = CombatStats(hp=1000, mp=0, attack=1, defense=0, speed=1, recovery=0)
    r = run_auto_battle(Combatant('hero', stats, CombatResources(1000, mp), (skills[skill_id],)), Combatant('foe', foe, CombatResources(target_hp, 0)), max_actions=1)
    assert r.events[0].damage == expected


@pytest.mark.parametrize('hp,expected', [(45, 86), (46, 90)])
def test_bastion_45_percent_boundary(advanced, hp, expected):
    skill = next(s for u in advanced.content.global_upgrades for s in u.skills if s.skill_id == 'talent_last_bastion')
    stats = CombatStats(hp=100, mp=0, attack=1, defense=10, speed=1, recovery=0)
    foe = CombatStats(hp=1000, mp=0, attack=100, defense=0, speed=100, recovery=0)
    result = run_auto_battle(Combatant('hero', stats, CombatResources(hp, 0), (skill,)), Combatant('foe', foe), max_actions=1)
    assert result.events[0].damage == expected


@pytest.mark.parametrize('mp,ticks', [(70, 20), (69, 25), (0, 25)])
def test_mana_acceleration_70_percent_boundary(advanced, mp, ticks):
    skill = next(s for u in advanced.content.global_upgrades for s in u.skills if s.skill_id == 'talent_mana_acceleration')
    stats = CombatStats(hp=100, mp=100, attack=1, defense=0, speed=4, recovery=0)
    foe = CombatStats(hp=1000, mp=0, attack=1, defense=0, speed=1, recovery=0)
    result = run_auto_battle(Combatant('hero', stats, CombatResources(100, mp), (skill,)), Combatant('foe', foe), max_actions=1)
    assert result.events[0].time_elapsed == ticks


def recovery_skills():
    care = Skill('care', '救护', 'active', SkillCondition('self_hp_pct_lte', .3),
        (SkillEffect('heal_percent', .25, target='self'),), priority=2, once_per_battle=True, trigger='action_start')
    reflux = Skill('reflux', '回流', 'active', SkillCondition('self_mp_pct_lte', .25),
        (SkillEffect('mp_restore_percent', .3, target='self', minimum=12),), priority=1, once_per_battle=True, trigger='action_start')
    return care, reflux


def test_two_auto_recoveries_and_finisher_share_one_action_without_bonus_attack():
    care, reflux = recovery_skills()
    finisher = Skill('spell', '法术', 'active', SkillCondition('always'), (SkillEffect('true_damage', 50),), mp_cost=6, priority=10)
    stats = CombatStats(hp=100, mp=100, attack=5, defense=0, speed=100, recovery=0)
    resources = CombatResources(30, 20)
    foe = CombatStats(hp=50, mp=0, attack=1, defense=0, speed=1, recovery=0)
    result = run_auto_battle(Combatant('hero', stats, resources, (finisher, reflux, care)), Combatant('foe', foe))
    assert result.outcome == 'left_win' and result.actions_taken == 1
    assert [e.skill_id for e in result.events] == ['care', 'reflux', 'spell']
    assert [e.action_index for e in result.events] == [1, 1, 1]
    assert [e.damage for e in result.events] == [0, 0, 50]
    assert result.left_resources == CombatResources(55, 44)
    assert resources == CombatResources(30, 20)


@pytest.mark.parametrize('mp,start,expected', [(0, 0, 0), (10, 0, 10), (20, 5, 17), (100, 25, 55), (101, 25, 55)])
def test_auto_mp_restore_minimum_clamp_floor_and_zero_pool(mp, start, expected):
    _, reflux = recovery_skills()
    stats = CombatStats(hp=100, mp=mp, attack=5, defense=0, speed=100, recovery=0)
    foe = CombatStats(hp=1000, mp=0, attack=1, defense=0, speed=1, recovery=0)
    result = run_auto_battle(Combatant('hero', stats, CombatResources(100, start), (reflux,)), Combatant('foe', foe), max_actions=1)
    assert result.left_resources.current_mp == expected
    assert sum(e.skill_id == 'reflux' for e in result.events) == int(mp > 0)
    assert result.events[-1].action_type == 'basic_attack'


def test_auto_recovery_once_per_battle_then_resets():
    _, reflux = recovery_skills()
    spell = Skill('spell', '法术', 'active', SkillCondition('always'), (SkillEffect('true_damage', 1),), mp_cost=30)
    stats = CombatStats(hp=100, mp=100, attack=1, defense=0, speed=100, recovery=0)
    foe = CombatStats(hp=1000, mp=0, attack=1, defense=0, speed=1, recovery=0)
    hero = Combatant('hero', stats, CombatResources(100, 0), (reflux, spell))
    for _ in range(2):
        result = run_auto_battle(hero, Combatant('foe', foe), max_actions=3)
        assert [e.skill_id for e in result.events] == ['reflux', 'spell', None, None]


def test_lethal_status_prevents_auto_recovery():
    care, _ = recovery_skills()
    poison = StatusDefinition('poison', '毒', 2, (SkillEffect('true_damage', 10),))
    cast = Skill('poison', '施毒', 'active', SkillCondition('always'), (SkillEffect('apply_status', target='target', status=poison),), once_per_battle=True)
    hero = CombatStats(hp=100, mp=0, attack=1, defense=0, speed=10, recovery=0)
    foe = replace(hero, speed=11)
    result = run_auto_battle(Combatant('hero', hero, CombatResources(10, 0), (care,)), Combatant('foe', foe, skills=(cast,)))
    assert result.reason == 'status_defeated_actor'
    assert all(e.skill_id != 'care' for e in result.events)


def test_auto_skills_can_be_disabled_but_not_reordered_and_restore_round_trip(economics):
    care, reflux = recovery_skills()
    stats = CombatStats(hp=100, mp=100, attack=5, defense=0, speed=100, recovery=0)
    hero = AdventurerState('hero', '测试员', stats, CombatResources(30, 20), skills=(care, reflux))
    d = replace(economics, content=replace(economics.content, adventurers=(hero,)))
    state = E.new_game(d)
    with pytest.raises(E.GameError, match='排序|优先级'):
        E.apply_preparation_action(d, state, ConfigureTacticsAction('hero', ('care',), ()))
    state = E.apply_preparation_action(d, state, ConfigureTacticsAction('hero', (), ('care',)))
    observation = build_observation(d, state)
    restored = GameSession.from_export(d, {'final_observation': observation}).state
    assert build_observation(d, restored) == observation
    skills = E.effective_adventurer_skills(d, restored, restored.adventurers[0])
    assert {s.skill_id for s in skills} == {'reflux'}
    assert skills[0].trigger == 'action_start' and skills[0].effects[0].minimum == 12
    session = GameSession(d, state=state)
    exported = GameSession.from_export(d, session.export_replay())
    assert exported.observation() == session.observation()
    battle, event = exported.end_turn(EndTurnAction(hunts=(HuntAction('hero', state.current_monsters[0].monster_id),)))
    assert battle.battles[0].won
    events = event.payload['battles'][0]['combat']['events']
    assert next(e for e in events if e['skill_id'] == 'reflux')['mp_restored'] == 30


def test_auto_formatting_and_tactic_text_are_not_described_as_extra_basic_attack():
    from guild_manager_bench.bench.llm.formatting import skill_summary
    from guild_manager_bench.runtime.observation import _skill_to_dict
    care, reflux = recovery_skills()
    text = skill_summary([_skill_to_dict(care), _skill_to_dict(reflux)])
    assert '自动' in text and '不占' in text
    assert '至少12' in text
    assert '触发后不替代普通攻击' not in text
