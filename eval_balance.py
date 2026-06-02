"""Full-set balance evaluation: simulate every class at stages A-G against all monsters.

Stage  | 代号 | 回合 | 等级 | 装备
A      | 白板新人 | T1  | L1   | 无
B      | 新兵+T1装 | T5  | L3   | 2-3件T1
C      | 主力前期 | T8  | L5   | T1全套
D      | 主力中期 | T12 | L6   | T1混少量T2
E      | 主力后期 | T18 | L8   | T2全套
F      | 精锐     | T25 | L9   | T2混少量T3
G      | 满配     | T35 | L12  | T3全套+部分升级
"""

from guild_manager_bench.game.loader import load_game_definition
from guild_manager_bench.game.combat import run_auto_battle, Combatant
from guild_manager_bench.game.models import CombatStats, CombatResources, CombatStatModifier
from guild_manager_bench.game.engine import apply_stat_modifier, scale_stat_modifier
from guild_manager_bench.game.progression import level_stat_modifier

defn = load_game_definition("data/presets/full")
exp_rules = defn.content.experience_rules


# ═══════════════════════════════════════════════════════════════════
# Equipment stat modifiers (from equipment.yaml)
# ═══════════════════════════════════════════════════════════════════

# T1 Heavy
T1_HEAVY_SWORD   = CombatStatModifier(attack=5)
T1_HEAVY_SHIELD  = CombatStatModifier(defense=4, hp=10)
T1_HEAVY_ARMOR   = CombatStatModifier(defense=5, hp=20)
T1_HEAVY_HELMET  = CombatStatModifier(defense=3, hp=8)
T1_HEAVY_BOOTS   = CombatStatModifier(defense=2, hp=5, speed=1)

# T1 Light
T1_LIGHT_BOW     = CombatStatModifier(attack=7, speed=2)
T1_LIGHT_ARMOR   = CombatStatModifier(defense=2, speed=2, hp=10)
T1_LIGHT_HELMET  = CombatStatModifier(speed=1, attack=2, hp=5)
T1_LIGHT_BOOTS   = CombatStatModifier(speed=3, defense=1)
T1_LIGHT_CHARM   = CombatStatModifier(recovery=6, mp_recovery=12, mp=5)

# T1 Specials
T1_STAFF         = CombatStatModifier(attack=3, mp=15)
T1_PRAYER_BEADS  = CombatStatModifier(recovery=12, mp_recovery=24, defense=2, mp=3)
T1_HUNTER_BRACER = CombatStatModifier(attack=3, speed=4)

# T2 Heavy
T2_HEAVY_SWORD   = CombatStatModifier(attack=10, defense=2)
T2_HEAVY_SHIELD  = CombatStatModifier(defense=7, hp=20)
T2_HEAVY_ARMOR   = CombatStatModifier(defense=8, hp=30)
T2_HEAVY_HELMET  = CombatStatModifier(defense=5, hp=15)
T2_HEAVY_BOOTS   = CombatStatModifier(defense=4, hp=10, speed=2)

# T2 Light
T2_LIGHT_BOW     = CombatStatModifier(attack=12, speed=4)
T2_LIGHT_ARMOR   = CombatStatModifier(defense=4, speed=3, hp=15)
T2_LIGHT_HELMET  = CombatStatModifier(speed=2, attack=3, hp=8)
T2_LIGHT_BOOTS   = CombatStatModifier(speed=5, defense=2, mp=5)
T2_LIGHT_CHARM   = CombatStatModifier(attack=4, mp=10)

# T2 Specials
T2_WARHAMMER     = CombatStatModifier(attack=18)
T2_MAGE_ORB      = CombatStatModifier(attack=3, mp=20)
T2_CANNON_KIT    = CombatStatModifier(attack=4, mp=12)
T2_BLOOD_RING    = CombatStatModifier(attack=8, speed=3)

# T3 Heavy
T3_HEAVY_SWORD   = CombatStatModifier(attack=16, defense=4, hp=10)
T3_HEAVY_SHIELD  = CombatStatModifier(defense=12, hp=35, recovery=6, mp_recovery=12)
T3_HEAVY_ARMOR   = CombatStatModifier(defense=14, hp=50)
T3_HEAVY_HELMET  = CombatStatModifier(defense=7, hp=20, recovery=4, mp_recovery=8)
T3_HEAVY_BOOTS   = CombatStatModifier(defense=5, hp=15, speed=2)

# T3 Light
T3_LIGHT_BOW     = CombatStatModifier(attack=18, speed=5)
T3_LIGHT_ARMOR   = CombatStatModifier(defense=6, speed=5, hp=25)
T3_LIGHT_HELMET  = CombatStatModifier(speed=3, attack=5, mp=8)
T3_LIGHT_BOOTS   = CombatStatModifier(speed=8, defense=3, hp=10)
T3_LIGHT_CHARM   = CombatStatModifier(attack=6, hp=15, recovery=8, mp_recovery=16)

# T3 Specials
T3_DRAGON_GREATSWORD = CombatStatModifier(attack=28, speed=1)
T3_WISDOM_CROWN  = CombatStatModifier(attack=5, mp=35, recovery=10, mp_recovery=20)
T3_SIEGE_CANNON  = CombatStatModifier(attack=22, mp=15)
T3_DRAGON_RESOLVE = CombatStatModifier(defense=20, hp=70, recovery=8, mp_recovery=16)


def sum_mods(*mods):
    """Sum multiple CombatStatModifiers."""
    result = CombatStatModifier()
    for m in mods:
        result = CombatStatModifier(
            hp=result.hp + m.hp,
            mp=result.mp + m.mp,
            attack=result.attack + m.attack,
            defense=result.defense + m.defense,
            speed=result.speed + m.speed,
            recovery=result.recovery + m.recovery,
            mp_recovery=result.mp_recovery + m.mp_recovery,
        )
    return result


# ═══════════════════════════════════════════════════════════════════
# Equipment skill IDs (need to look up from defn)
# ═══════════════════════════════════════════════════════════════════

def get_equipment_skills(*skill_ids):
    """Get Skill objects from defn by skill_id."""
    skills = []
    for sid in skill_ids:
        if sid is None:
            continue
        for s in defn.content.skills:
            if s.skill_id == sid:
                skills.append(s)
                break
    return tuple(skills)


# ═══════════════════════════════════════════════════════════════════
# Class definitions with stage equipment
# ═══════════════════════════════════════════════════════════════════

def build_class_configs():
    """Return list of class configs with equipment sets for each stage."""

    configs = []

    # ── 佣兵战士 (mercenary_warrior) ──
    # Heavy DPS: sword+shield → warhammer → dragon_greatsword
    configs.append({
        "id": "mercenary_warrior",
        "name": "佣兵战士",
        "base_stats": CombatStats(hp=120, mp=0, attack=18, defense=8, speed=10, recovery=10, mp_recovery=20),
        "growth": CombatStatModifier(hp=14, attack=3, defense=2, speed=1, recovery=2, mp_recovery=4),
        "stages": {
            "A": {"equip_mods": [], "equip_skills": []},
            "B": {"equip_mods": [T1_HEAVY_SWORD, T1_HEAVY_ARMOR, T1_HEAVY_BOOTS],
                  "equip_skills": []},
            "C": {"equip_mods": [T1_HEAVY_SWORD, T1_HEAVY_SHIELD, T1_HEAVY_ARMOR, T1_HEAVY_HELMET, T1_HEAVY_BOOTS],
                  "equip_skills": []},
            "D": {"equip_mods": [T2_WARHAMMER, T1_HEAVY_ARMOR, T1_HEAVY_HELMET, T1_HEAVY_BOOTS],
                  "equip_skills": ["shatter_strike"]},
            "E": {"equip_mods": [T2_WARHAMMER, T2_HEAVY_ARMOR, T2_HEAVY_HELMET, T2_HEAVY_BOOTS],
                  "equip_skills": ["shatter_strike", "steady_stance"]},
            "F": {"equip_mods": [T3_DRAGON_GREATSWORD, T2_HEAVY_ARMOR, T2_HEAVY_HELMET, T2_HEAVY_BOOTS],
                  "equip_skills": ["dragon_cleave", "steady_stance"]},
            "G": {"equip_mods": [T3_DRAGON_GREATSWORD, T3_HEAVY_ARMOR, T3_HEAVY_HELMET, T3_HEAVY_BOOTS],
                  "equip_skills": ["dragon_cleave", "dragon_hide"]},
        },
    })

    # ── 步行骑士 (foot_knight) ──
    # Tank: sword+shield all the way, T3 gets dragon_resolve armor
    configs.append({
        "id": "foot_knight",
        "name": "步行骑士",
        "base_stats": CombatStats(hp=150, mp=0, attack=14, defense=12, speed=7, recovery=12, mp_recovery=24),
        "growth": CombatStatModifier(hp=18, attack=2, defense=3, speed=1, recovery=2, mp_recovery=4),
        "stages": {
            "A": {"equip_mods": [], "equip_skills": []},
            "B": {"equip_mods": [T1_HEAVY_SHIELD, T1_HEAVY_ARMOR, T1_HEAVY_HELMET],
                  "equip_skills": []},
            "C": {"equip_mods": [T1_HEAVY_SWORD, T1_HEAVY_SHIELD, T1_HEAVY_ARMOR, T1_HEAVY_HELMET, T1_HEAVY_BOOTS],
                  "equip_skills": []},
            "D": {"equip_mods": [T1_HEAVY_SWORD, T2_HEAVY_SHIELD, T2_HEAVY_ARMOR, T1_HEAVY_HELMET, T1_HEAVY_BOOTS],
                  "equip_skills": ["steady_stance"]},
            "E": {"equip_mods": [T2_HEAVY_SWORD, T2_HEAVY_SHIELD, T2_HEAVY_ARMOR, T2_HEAVY_HELMET, T2_HEAVY_BOOTS],
                  "equip_skills": ["steady_stance"]},
            "F": {"equip_mods": [T3_HEAVY_SWORD, T3_HEAVY_SHIELD, T2_HEAVY_ARMOR, T2_HEAVY_HELMET, T2_HEAVY_BOOTS],
                  "equip_skills": ["steady_stance"]},
            "G": {"equip_mods": [T3_HEAVY_SWORD, T3_HEAVY_SHIELD, T3_DRAGON_RESOLVE, T3_HEAVY_HELMET, T3_HEAVY_BOOTS],
                  "equip_skills": ["fortify"]},
        },
    })

    # ── 林地射手 (woodland_archer) ──
    configs.append({
        "id": "woodland_archer",
        "name": "林地射手",
        "base_stats": CombatStats(hp=95, mp=0, attack=19, defense=5, speed=15, recovery=8, mp_recovery=16),
        "growth": CombatStatModifier(hp=10, attack=3, defense=1, speed=3, recovery=2, mp_recovery=4),
        "stages": {
            "A": {"equip_mods": [], "equip_skills": []},
            "B": {"equip_mods": [T1_LIGHT_BOW, T1_LIGHT_ARMOR, T1_HUNTER_BRACER],
                  "equip_skills": []},
            "C": {"equip_mods": [T1_LIGHT_BOW, T1_LIGHT_ARMOR, T1_LIGHT_HELMET, T1_LIGHT_BOOTS, T1_HUNTER_BRACER],
                  "equip_skills": []},
            "D": {"equip_mods": [T2_LIGHT_BOW, T2_LIGHT_ARMOR, T1_LIGHT_HELMET, T1_LIGHT_BOOTS, T1_HUNTER_BRACER],
                  "equip_skills": []},
            "E": {"equip_mods": [T2_LIGHT_BOW, T2_LIGHT_ARMOR, T2_LIGHT_HELMET, T2_LIGHT_BOOTS, T2_LIGHT_CHARM],
                  "equip_skills": []},
            "F": {"equip_mods": [T3_LIGHT_BOW, T3_LIGHT_ARMOR, T2_LIGHT_HELMET, T2_LIGHT_BOOTS, T2_LIGHT_CHARM],
                  "equip_skills": []},
            "G": {"equip_mods": [T3_LIGHT_BOW, T3_LIGHT_ARMOR, T3_LIGHT_HELMET, T3_LIGHT_BOOTS, T3_LIGHT_CHARM],
                  "equip_skills": ["shadow_step"]},
        },
    })

    # ── 魔弹法师 (spellshot_mage) ──
    configs.append({
        "id": "spellshot_mage",
        "name": "魔弹法师",
        "base_stats": CombatStats(hp=85, mp=24, attack=15, defense=4, speed=10, recovery=8, mp_recovery=16),
        "growth": CombatStatModifier(hp=8, mp=4, attack=4, defense=1, speed=1, recovery=2, mp_recovery=4),
        "stages": {
            "A": {"equip_mods": [], "equip_skills": []},
            "B": {"equip_mods": [T1_STAFF, T1_LIGHT_ARMOR, T1_LIGHT_CHARM],
                  "equip_skills": []},
            "C": {"equip_mods": [T1_STAFF, T1_LIGHT_ARMOR, T1_LIGHT_HELMET, T1_LIGHT_BOOTS, T1_LIGHT_CHARM],
                  "equip_skills": []},
            "D": {"equip_mods": [T1_STAFF, T2_LIGHT_ARMOR, T2_LIGHT_HELMET, T2_LIGHT_BOOTS, T2_MAGE_ORB],
                  "equip_skills": ["arcane_surge"]},
            "E": {"equip_mods": [T1_STAFF, T2_LIGHT_ARMOR, T2_LIGHT_HELMET, T2_LIGHT_BOOTS, T2_MAGE_ORB],
                  "equip_skills": ["arcane_surge"]},
            "F": {"equip_mods": [T1_STAFF, T3_LIGHT_ARMOR, T3_WISDOM_CROWN, T3_LIGHT_BOOTS, T2_MAGE_ORB],
                  "equip_skills": ["arcane_surge", "plague_touch"]},
            "G": {"equip_mods": [T1_STAFF, T3_LIGHT_ARMOR, T3_WISDOM_CROWN, T3_LIGHT_BOOTS, T2_MAGE_ORB],
                  "equip_skills": ["arcane_surge", "plague_touch", "shadow_step"]},
        },
    })

    # ── 神官 (cleric) ──
    configs.append({
        "id": "cleric",
        "name": "神官",
        "base_stats": CombatStats(hp=110, mp=36, attack=13, defense=8, speed=8, recovery=16, mp_recovery=32),
        "growth": CombatStatModifier(hp=12, mp=5, attack=2, defense=2, speed=1, recovery=4, mp_recovery=8),
        "stages": {
            "A": {"equip_mods": [], "equip_skills": []},
            "B": {"equip_mods": [T1_PRAYER_BEADS, T1_HEAVY_ARMOR, T1_HEAVY_HELMET],
                  "equip_skills": ["healing_pulse"]},
            "C": {"equip_mods": [T1_HEAVY_SWORD, T1_HEAVY_SHIELD, T1_HEAVY_ARMOR, T1_HEAVY_HELMET, T1_PRAYER_BEADS],
                  "equip_skills": ["healing_pulse"]},
            "D": {"equip_mods": [T2_HEAVY_SWORD, T2_HEAVY_SHIELD, T2_HEAVY_ARMOR, T1_HEAVY_HELMET, T1_PRAYER_BEADS],
                  "equip_skills": ["healing_pulse", "steady_stance"]},
            "E": {"equip_mods": [T2_HEAVY_SWORD, T2_HEAVY_SHIELD, T2_HEAVY_ARMOR, T2_HEAVY_HELMET, T1_PRAYER_BEADS],
                  "equip_skills": ["healing_pulse", "steady_stance"]},
            "F": {"equip_mods": [T3_HEAVY_SWORD, T3_HEAVY_SHIELD, T3_HEAVY_ARMOR, T2_HEAVY_HELMET, T1_PRAYER_BEADS],
                  "equip_skills": ["healing_pulse", "dragon_hide"]},
            "G": {"equip_mods": [T3_HEAVY_SWORD, T3_HEAVY_SHIELD, T3_HEAVY_ARMOR, T3_HEAVY_HELMET, T1_PRAYER_BEADS],
                  "equip_skills": ["healing_pulse", "dragon_hide"]},
        },
    })

    # ── 小丑 (jester) ──
    configs.append({
        "id": "jester",
        "name": "小丑",
        "base_stats": CombatStats(hp=90, mp=18, attack=16, defense=5, speed=14, recovery=8, mp_recovery=16),
        "growth": CombatStatModifier(hp=9, mp=2, attack=3, defense=1, speed=3, recovery=2, mp_recovery=4),
        "stages": {
            "A": {"equip_mods": [], "equip_skills": []},
            "B": {"equip_mods": [T1_LIGHT_BOW, T1_LIGHT_ARMOR, T1_LIGHT_BOOTS],
                  "equip_skills": []},
            "C": {"equip_mods": [T1_LIGHT_BOW, T1_LIGHT_ARMOR, T1_LIGHT_HELMET, T1_LIGHT_BOOTS, T1_LIGHT_CHARM],
                  "equip_skills": []},
            "D": {"equip_mods": [T2_LIGHT_BOW, T2_LIGHT_ARMOR, T1_LIGHT_HELMET, T1_LIGHT_BOOTS, T2_BLOOD_RING],
                  "equip_skills": ["venom_coat"]},
            "E": {"equip_mods": [T2_LIGHT_BOW, T2_LIGHT_ARMOR, T2_LIGHT_HELMET, T2_LIGHT_BOOTS, T2_BLOOD_RING],
                  "equip_skills": ["venom_coat"]},
            "F": {"equip_mods": [T3_LIGHT_BOW, T3_LIGHT_ARMOR, T2_LIGHT_HELMET, T2_LIGHT_BOOTS, T2_BLOOD_RING],
                  "equip_skills": ["venom_coat"]},
            "G": {"equip_mods": [T3_LIGHT_BOW, T3_LIGHT_ARMOR, T3_LIGHT_HELMET, T3_LIGHT_BOOTS, T2_BLOOD_RING],
                  "equip_skills": ["venom_coat", "shadow_step"]},
        },
    })

    # ── 苦行僧 (ascetic_monk) ──
    configs.append({
        "id": "ascetic_monk",
        "name": "苦行僧",
        "base_stats": CombatStats(hp=125, mp=0, attack=13, defense=9, speed=8, recovery=14, mp_recovery=28),
        "growth": CombatStatModifier(hp=15, attack=3, defense=2, speed=1, recovery=4, mp_recovery=8),
        "stages": {
            "A": {"equip_mods": [], "equip_skills": []},
            "B": {"equip_mods": [T1_HEAVY_SWORD, T1_HEAVY_ARMOR, T1_PRAYER_BEADS],
                  "equip_skills": ["healing_pulse"]},
            "C": {"equip_mods": [T1_HEAVY_SWORD, T1_HEAVY_SHIELD, T1_HEAVY_ARMOR, T1_HEAVY_HELMET, T1_PRAYER_BEADS],
                  "equip_skills": ["healing_pulse"]},
            "D": {"equip_mods": [T2_HEAVY_SWORD, T2_HEAVY_SHIELD, T2_HEAVY_ARMOR, T1_HEAVY_HELMET, T1_PRAYER_BEADS],
                  "equip_skills": ["healing_pulse", "steady_stance"]},
            "E": {"equip_mods": [T2_HEAVY_SWORD, T2_HEAVY_SHIELD, T2_HEAVY_ARMOR, T2_HEAVY_HELMET, T1_PRAYER_BEADS],
                  "equip_skills": ["healing_pulse", "steady_stance"]},
            "F": {"equip_mods": [T3_HEAVY_SWORD, T3_HEAVY_SHIELD, T3_DRAGON_RESOLVE, T2_HEAVY_HELMET, T1_PRAYER_BEADS],
                  "equip_skills": ["healing_pulse", "fortify"]},
            "G": {"equip_mods": [T3_HEAVY_SWORD, T3_HEAVY_SHIELD, T3_DRAGON_RESOLVE, T3_HEAVY_HELMET, T1_PRAYER_BEADS],
                  "equip_skills": ["healing_pulse", "fortify"]},
        },
    })

    # ── 吸血魔 (bloodfiend) ──
    configs.append({
        "id": "bloodfiend",
        "name": "吸血魔",
        "base_stats": CombatStats(hp=105, mp=22, attack=17, defense=5, speed=11, recovery=6, mp_recovery=12),
        "growth": CombatStatModifier(hp=12, mp=3, attack=4, defense=1, speed=2, recovery=2, mp_recovery=4),
        "stages": {
            "A": {"equip_mods": [], "equip_skills": []},
            "B": {"equip_mods": [T1_HEAVY_SWORD, T1_LIGHT_ARMOR, T1_LIGHT_BOOTS],
                  "equip_skills": []},
            "C": {"equip_mods": [T1_HEAVY_SWORD, T1_HEAVY_SHIELD, T1_LIGHT_ARMOR, T1_LIGHT_HELMET, T1_LIGHT_BOOTS],
                  "equip_skills": []},
            "D": {"equip_mods": [T2_WARHAMMER, T1_LIGHT_ARMOR, T1_LIGHT_HELMET, T1_LIGHT_BOOTS, T2_BLOOD_RING],
                  "equip_skills": ["shatter_strike", "venom_coat"]},
            "E": {"equip_mods": [T2_WARHAMMER, T2_LIGHT_ARMOR, T2_LIGHT_HELMET, T2_LIGHT_BOOTS, T2_BLOOD_RING],
                  "equip_skills": ["shatter_strike", "venom_coat"]},
            "F": {"equip_mods": [T3_DRAGON_GREATSWORD, T2_LIGHT_ARMOR, T2_LIGHT_HELMET, T2_LIGHT_BOOTS, T2_BLOOD_RING],
                  "equip_skills": ["dragon_cleave", "venom_coat"]},
            "G": {"equip_mods": [T3_DRAGON_GREATSWORD, T3_LIGHT_ARMOR, T3_LIGHT_HELMET, T3_LIGHT_BOOTS, T2_BLOOD_RING],
                  "equip_skills": ["dragon_cleave", "venom_coat", "shadow_step"]},
        },
    })

    # ── 炮手 (cannoneer) ──
    configs.append({
        "id": "cannoneer",
        "name": "炮手",
        "base_stats": CombatStats(hp=100, mp=18, attack=13, defense=6, speed=7, recovery=8, mp_recovery=16),
        "growth": CombatStatModifier(hp=11, mp=2, attack=3, defense=2, speed=1, recovery=2, mp_recovery=4),
        "stages": {
            "A": {"equip_mods": [], "equip_skills": []},
            "B": {"equip_mods": [T1_HEAVY_SWORD, T1_HEAVY_ARMOR, T1_HUNTER_BRACER],
                  "equip_skills": []},
            "C": {"equip_mods": [T1_HEAVY_SWORD, T1_HEAVY_SHIELD, T1_HEAVY_ARMOR, T1_HEAVY_HELMET, T1_HUNTER_BRACER],
                  "equip_skills": []},
            "D": {"equip_mods": [T2_WARHAMMER, T2_HEAVY_ARMOR, T1_HEAVY_HELMET, T1_HEAVY_BOOTS, T2_CANNON_KIT],
                  "equip_skills": ["shatter_strike", "rapid_reload", "steady_stance"]},
            "E": {"equip_mods": [T2_WARHAMMER, T2_HEAVY_ARMOR, T2_HEAVY_HELMET, T2_HEAVY_BOOTS, T2_CANNON_KIT],
                  "equip_skills": ["shatter_strike", "rapid_reload", "steady_stance"]},
            "F": {"equip_mods": [T3_SIEGE_CANNON, T2_HEAVY_ARMOR, T2_HEAVY_HELMET, T2_HEAVY_BOOTS, T2_CANNON_KIT],
                  "equip_skills": ["siege_barrage", "rapid_reload", "steady_stance"]},
            "G": {"equip_mods": [T3_SIEGE_CANNON, T3_HEAVY_ARMOR, T3_HEAVY_HELMET, T3_HEAVY_BOOTS, T2_CANNON_KIT],
                  "equip_skills": ["siege_barrage", "rapid_reload", "dragon_hide"]},
        },
    })

    # ── 瘟疫法师 (plague_mage) ──
    configs.append({
        "id": "plague_mage",
        "name": "瘟疫法师",
        "base_stats": CombatStats(hp=90, mp=30, attack=13, defense=4, speed=10, recovery=8, mp_recovery=16),
        "growth": CombatStatModifier(hp=8, mp=5, attack=3, defense=1, speed=2, recovery=2, mp_recovery=4),
        "stages": {
            "A": {"equip_mods": [], "equip_skills": []},
            "B": {"equip_mods": [T1_STAFF, T1_LIGHT_ARMOR, T1_LIGHT_CHARM],
                  "equip_skills": []},
            "C": {"equip_mods": [T1_STAFF, T1_LIGHT_ARMOR, T1_LIGHT_HELMET, T1_LIGHT_BOOTS, T1_LIGHT_CHARM],
                  "equip_skills": []},
            "D": {"equip_mods": [T1_STAFF, T2_LIGHT_ARMOR, T2_LIGHT_HELMET, T2_LIGHT_BOOTS, T2_MAGE_ORB],
                  "equip_skills": ["arcane_surge"]},
            "E": {"equip_mods": [T1_STAFF, T2_LIGHT_ARMOR, T2_LIGHT_HELMET, T2_LIGHT_BOOTS, T2_MAGE_ORB],
                  "equip_skills": ["arcane_surge"]},
            "F": {"equip_mods": [T1_STAFF, T3_LIGHT_ARMOR, T3_WISDOM_CROWN, T3_LIGHT_BOOTS, T2_MAGE_ORB],
                  "equip_skills": ["arcane_surge", "plague_touch"]},
            "G": {"equip_mods": [T1_STAFF, T3_LIGHT_ARMOR, T3_WISDOM_CROWN, T3_LIGHT_BOOTS, T2_MAGE_ORB],
                  "equip_skills": ["arcane_surge", "plague_touch", "shadow_step"]},
        },
    })

    return configs


# ═══════════════════════════════════════════════════════════════════
# Stage definitions
# ═══════════════════════════════════════════════════════════════════

STAGES = {
    "A": {"level": 1, "turn": 1,  "label": "白板新人 T1/L1"},
    "B": {"level": 3, "turn": 5,  "label": "新兵+T1装 T5/L3"},
    "C": {"level": 5, "turn": 8,  "label": "主力前期 T8/L5"},
    "D": {"level": 6, "turn": 12, "label": "主力中期 T12/L6"},
    "E": {"level": 8, "turn": 18, "label": "主力后期 T18/L8"},
    "F": {"level": 9, "turn": 25, "label": "精锐 T25/L9"},
    "G": {"level": 12, "turn": 35, "label": "满配 T35/L12"},
}

# Monster groups by turn range
EARLY_MONSTERS  = ["goblin", "slime", "ogre", "orc_warrior", "warg", "goblin_shaman"]
MID_MONSTERS    = ["orc_captain", "dark_mage", "giant_spider", "skeleton_knight", "wraith", "hellhound"]
LATE_MONSTERS   = ["gargoyle", "wyvern", "lich", "demon_guard", "dragon", "death_knight"]


# ═══════════════════════════════════════════════════════════════════
# Core simulation logic
# ═══════════════════════════════════════════════════════════════════

def get_class_skills(cfg, level):
    """Get all skills for a class at a given level (from adventurers.yaml)."""
    # Find adventurer template
    template = None
    for t in defn.content.recruitable_adventurers:
        if t.template_id == cfg["id"]:
            template = t
            break
    if template is None:
        return ()

    skills = list(template.skills)
    for unlock in template.level_skill_unlocks:
        if level >= unlock.level:
            skills.extend(unlock.skills)
    return tuple(skills)


def get_equipment_skills_obj(skill_ids):
    """Get Skill objects for equipment skill IDs by searching equipment templates."""
    result = []
    for sid in skill_ids:
        found = False
        for tmpl in defn.content.equipment_templates:
            for sk in tmpl.skills:
                if sk.skill_id == sid:
                    result.append(sk)
                    found = True
                    break
            if found:
                break
    return tuple(result)


def make_combatant(cfg, stage_key, monster_arch, turn):
    """Build adventurer Combatant for a given class+stage."""
    stage = STAGES[stage_key]
    level = stage["level"]
    stage_cfg = cfg["stages"][stage_key]

    # Base stats + level growth
    growth = level_stat_modifier(level, exp_rules, stat_growth_per_level=cfg["growth"])
    stats = apply_stat_modifier(cfg["base_stats"], growth)

    # Apply equipment
    for mod in stage_cfg["equip_mods"]:
        stats = apply_stat_modifier(stats, mod)

    # Get skills
    skills = list(get_class_skills(cfg, level))
    skills.extend(get_equipment_skills_obj(stage_cfg["equip_skills"]))

    return Combatant(
        combatant_id="adv",
        stats=stats,
        resources=CombatResources.full(stats),
        skills=tuple(skills),
    )


def make_monster_combatant(archetype_id, turn):
    """Build monster Combatant at a given turn."""
    for arch in defn.content.monster_archetypes:
        if arch.archetype_id == archetype_id:
            factor = defn.rules.monster_spawn.stat_growth_curve.value_at(turn)
            stats = apply_stat_modifier(arch.base_stats, scale_stat_modifier(arch.stat_growth, factor))
            return Combatant(
                combatant_id="mon",
                stats=stats,
                resources=CombatResources.full(stats),
                skills=arch.skills,
            )
    raise ValueError(f"Unknown monster archetype: {archetype_id}")


def simulate(adv, mon):
    """Run a single battle, return (won, remaining_hp_pct, actions)."""
    result = run_auto_battle(adv, mon)
    won = result.outcome == "left_win"
    hp_pct = result.left_resources.current_hp / adv.stats.hp * 100 if won else 0
    return won, hp_pct, result.actions_taken


# ═══════════════════════════════════════════════════════════════════
# Monster difficulty classification
# ═══════════════════════════════════════════════════════════════════

MONSTER_DIFFICULTY = {
    # Early - 弱
    "goblin": "弱", "slime": "弱",
    # Early - 中
    "orc_warrior": "中", "warg": "中", "goblin_shaman": "中",
    # Early - 强
    "ogre": "强",

    # Mid - 弱
    "giant_spider": "弱", "wraith": "弱",
    # Mid - 中
    "orc_captain": "中", "dark_mage": "中", "hellhound": "中",
    # Mid - 强
    "skeleton_knight": "强",

    # Late - 弱
    "gargoyle": "弱", "wyvern": "弱",
    # Late - 中
    "lich": "中", "demon_guard": "中",
    # Late - 强
    "dragon": "强", "death_knight": "强",
}


def get_monster_display_name(archetype_id):
    for arch in defn.content.monster_archetypes:
        if arch.archetype_id == archetype_id:
            return arch.name
    return archetype_id


# ═══════════════════════════════════════════════════════════════════
# Main evaluation
# ═══════════════════════════════════════════════════════════════════

def main():
    configs = build_class_configs()

    # For each stage, determine which monsters to test against
    stage_monsters = {
        "A": EARLY_MONSTERS,
        "B": EARLY_MONSTERS,
        "C": EARLY_MONSTERS + MID_MONSTERS,
        "D": MID_MONSTERS,
        "E": MID_MONSTERS + LATE_MONSTERS,
        "F": LATE_MONSTERS,
        "G": LATE_MONSTERS,
    }

    print("=" * 120)
    print("全职业平衡评估 - 各阶段对抗怪物胜率与剩余HP%")
    print("=" * 120)

    for stage_key in ["A", "B", "C", "D", "E", "F", "G"]:
        stage = STAGES[stage_key]
        monster_list = stage_monsters[stage_key]

        print(f"\n{'─' * 120}")
        print(f"阶段 {stage_key}: {stage['label']} | 对抗回合={stage['turn']} | 等级={stage['level']}")
        print(f"{'─' * 120}")

        # Header
        header = f"{'职业':<8}"
        for mid in monster_list:
            diff = MONSTER_DIFFICULTY.get(mid, "?")
            name = get_monster_display_name(mid)
            header += f" {name}({diff}):{'':<4}"
        header += f" {'胜率':<6} {'均剩%':<7}"
        print(header)
        print("-" * 120)

        for cfg in configs:
            row = f"{cfg['name']:<8}"
            wins = 0
            total_hp = 0
            details = []

            for mid in monster_list:
                try:
                    adv = make_combatant(cfg, stage_key, mid, stage["turn"])
                    mon = make_monster_combatant(mid, stage["turn"])
                    won, hp_pct, actions = simulate(adv, mon)

                    if won:
                        wins += 1
                        total_hp += hp_pct
                        row += f" {'W'+str(int(hp_pct))+'%':<10}"
                    else:
                        row += f" {'L':<10}"
                    details.append((mid, won, hp_pct, actions, adv.stats, mon.stats))
                except Exception as e:
                    row += f" {'ERR':<10}"
                    details.append((mid, False, 0, 0, None, None))

            n = len(monster_list)
            win_rate = wins / n * 100 if n > 0 else 0
            avg_hp = total_hp / wins if wins > 0 else 0
            row += f" {win_rate:.0f}%{'':<3} {avg_hp:.0f}%"
            print(row)

    # ═══════════════════════════════════════════════════════════════
    # Detailed matchup analysis
    # ═══════════════════════════════════════════════════════════════
    print("\n\n" + "=" * 120)
    print("详细分析: 各职业对抗强怪 (ogre / skeleton_knight / dragon)")
    print("=" * 120)

    key_matchups = [
        ("A", "ogre", "Early强-食人魔"),
        ("C", "ogre", "Early强-食人魔"),
        ("D", "skeleton_knight", "Mid强-骷髅骑士"),
        ("E", "skeleton_knight", "Mid强-骷髅骑士"),
        ("F", "dragon", "Late强-巨龙"),
        ("G", "dragon", "Late强-巨龙"),
    ]

    for stage_key, monster_id, label in key_matchups:
        stage = STAGES[stage_key]
        print(f"\n--- 阶段{stage_key} vs {label} (T{stage['turn']}/L{stage['level']}) ---")
        print(f"{'职业':<8} {'HP':>5} {'ATK':>5} {'DEF':>5} {'SPD':>5} │ {'怪HP':>5} {'怪ATK':>5} {'怪DEF':>5} {'怪SPD':>5} │ {'结果':<6} {'剩余%':>6} {'回合':>4}")
        print("-" * 110)

        for cfg in configs:
            adv = make_combatant(cfg, stage_key, monster_id, stage["turn"])
            mon = make_monster_combatant(monster_id, stage["turn"])
            won, hp_pct, actions = simulate(adv, mon)

            result_str = "WIN" if won else "LOSS"
            print(f"{cfg['name']:<8} {adv.stats.hp:>5} {adv.stats.attack:>5} {adv.stats.defense:>5} {adv.stats.speed:>5} │ "
                  f"{mon.stats.hp:>5} {mon.stats.attack:>5} {mon.stats.defense:>5} {mon.stats.speed:>5} │ "
                  f"{result_str:<6} {hp_pct:>5.0f}% {actions:>4}")

    # ═══════════════════════════════════════════════════════════════
    # Summary: per-class progression curve
    # ═══════════════════════════════════════════════════════════════
    print("\n\n" + "=" * 120)
    print("职业成长曲线: 各阶段总胜率变化")
    print("=" * 120)

    stage_order = ["A", "B", "C", "D", "E", "F", "G"]
    header = f"{'职业':<8}"
    for sk in stage_order:
        header += f" {sk}:{'':<3}"
    print(header)
    print("-" * 80)

    for cfg in configs:
        row = f"{cfg['name']:<8}"
        for sk in stage_order:
            stage = STAGES[sk]
            monster_list = stage_monsters[sk]
            wins = 0
            for mid in monster_list:
                try:
                    adv = make_combatant(cfg, sk, mid, stage["turn"])
                    mon = make_monster_combatant(mid, stage["turn"])
                    won, _, _ = simulate(adv, mon)
                    if won:
                        wins += 1
                except Exception:
                    pass
            n = len(monster_list)
            pct = wins / n * 100 if n > 0 else 0
            row += f" {pct:.0f}%{'':<1}"
        print(row)


if __name__ == "__main__":
    main()
