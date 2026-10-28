import pytest

from guild_manager_bench.game.combat import Combatant, run_auto_battle
from guild_manager_bench.game.equipment import apply_equipment_stats, combine_equipment_skills
from guild_manager_bench.game.loader import load_game_definition
from guild_manager_bench.game.models import CombatResources, CombatStats


@pytest.fixture(scope="module")
def definition():
    return load_game_definition("data/presets/advanced")


@pytest.fixture(scope="module")
def prayer_beads(definition):
    return next(e for e in definition.content.equipment_templates if e.equipment_id == "t1_prayer_beads")


def test_prayer_beads_only_adds_existing_sustain_stats(prayer_beads):
    base = CombatStats(hp=100, mp=20, attack=10, defense=4, speed=5, recovery=1, mp_recovery=2)

    assert prayer_beads.skills == ()
    assert apply_equipment_stats(base, (prayer_beads,)) == CombatStats(
        hp=100, mp=23, attack=10, defense=6, speed=5, recovery=13, mp_recovery=14,
    )


@pytest.mark.parametrize("class_id,level,hp,mp,expected_skill", [
    ("cleric", 1, 70, 9, "minor_heal"),
    ("cleric", 1, 70, 8, None),
    ("cleric", 3, 60, 7, "soothing_light"),
    ("cleric", 3, 60, 6, None),
    ("cleric", 6, 100, 10, "holy_strike"),
    ("cleric", 6, 100, 9, None),
    ("ascetic_monk", 6, 70, 5, None),
])
def test_prayer_beads_does_not_inject_skills(definition, prayer_beads, class_id, level, hp, mp, expected_skill):
    template = next(a for a in definition.content.recruitable_adventurers if a.template_id == class_id)
    skills = template.skills + tuple(
        skill for unlock in template.level_skill_unlocks if unlock.level <= level for skill in unlock.skills
    )
    stats = CombatStats(hp=100, mp=20, attack=10, defense=0, speed=100, recovery=0)
    resources = CombatResources(hp, mp)
    foe = Combatant("foe", CombatStats(hp=1000, mp=0, attack=1, defense=0, speed=1, recovery=0))
    baseline = run_auto_battle(Combatant("hero", stats, resources, skills), foe, max_actions=1)
    equipped = run_auto_battle(
        Combatant("hero", apply_equipment_stats(stats, (prayer_beads,)), resources,
                  combine_equipment_skills(skills, (prayer_beads,))),
        foe,
        max_actions=1,
    )

    assert len(baseline.events) == 1
    assert baseline.events[0].skill_id == expected_skill
    assert equipped == baseline
