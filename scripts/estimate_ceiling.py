"""Estimate the scoring ceiling by building an "optimal" team from a resource
budget and testing it against escalating difficulty factors.

The core idea: instead of solving the full sequential game (which is NP-hard),
we give ourselves a resource budget slightly above what normal good play can
achieve, then solve for the strongest possible team within that budget.

This gives us a practical upper bound on what score any agent could achieve,
which in turn tells us where to set difficulty factors for proper
differentiation.

Usage:
    python scripts/estimate_ceiling.py --preset default
    python scripts/estimate_ceiling.py --preset full
    python scripts/estimate_ceiling.py --preset default --gold 2500 --xp 8000
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from itertools import combinations
from pathlib import Path
from typing import Any, Mapping

# ── project root ──────────────────────────────────────────────────────────
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from guild_manager_bench.game.loader import load_game_definition
from guild_manager_bench.game.presets import resolve_data_preset
from guild_manager_bench.game.models import (
    CombatResources,
    CombatStatModifier,
    CombatStats,
    apply_stat_modifier,
)
from guild_manager_bench.game.state import (
    AdventurerState,
    GameContent,
    GameDefinition,
    GameState,
    GameRules,
    LlmToolRules,
    MonsterSpawnRules,
    RecruitmentRules,
    ScoringRules,
    TurnRecoveryRules,
)
from guild_manager_bench.game.equipment import (
    EQUIPMENT_SLOTS,
    EquippedItem,
    EquipmentInstance,
    EquipmentLoadout,
    EquipmentTemplate,
    combine_equipment_modifier,
)
from guild_manager_bench.game.progression import (
    ExperienceRules,
    level_stat_modifier,
    required_experience_for_next_level,
)
from guild_manager_bench.game.upgrades import GlobalUpgrade
from guild_manager_bench.game.crafting import CraftingRecipe
from guild_manager_bench.bench.metrics import score_final_state


# ── stat weights (mirrors SearchOperator heuristic) ──────────────────────
STAT_WEIGHTS = {
    "attack": 2.0,
    "speed": 1.8,
    "defense": 1.5,
    "hp": 0.3,
    "recovery": 0.5,
    "mp_recovery": 0.5,
    "mp": 0.0,  # not weighted directly, but skills need MP
}


def stat_power(stats: CombatStats) -> float:
    """Weighted stat sum for comparing combat effectiveness."""
    return (
        stats.attack * STAT_WEIGHTS["attack"]
        + stats.speed * STAT_WEIGHTS["speed"]
        + stats.defense * STAT_WEIGHTS["defense"]
        + stats.hp * STAT_WEIGHTS["hp"]
        + stats.recovery * STAT_WEIGHTS["recovery"]
        + stats.mp_recovery * STAT_WEIGHTS["mp_recovery"]
    )


def modifier_power(mod: CombatStatModifier) -> float:
    """Weighted sum of a stat modifier."""
    return (
        mod.attack * STAT_WEIGHTS["attack"]
        + mod.speed * STAT_WEIGHTS["speed"]
        + mod.defense * STAT_WEIGHTS["defense"]
        + mod.hp * STAT_WEIGHTS["hp"]
        + mod.recovery * STAT_WEIGHTS["recovery"]
        + mod.mp_recovery * STAT_WEIGHTS["mp_recovery"]
    )


# ── resource budget ──────────────────────────────────────────────────────

@dataclass
class Budget:
    gold: int
    xp_pool: int
    materials: dict[str, int]


def default_budget(definition: GameDefinition) -> Budget:
    """Estimate a "generous but not infinite" resource budget.

    Computes the theoretical maximum resources by averaging across all
    monster archetypes over all turns, then applies a ~1.4x generous multiplier.
    """
    rules = definition.rules
    max_turns = rules.max_turns
    archetypes = definition.content.monster_archetypes
    n = max(len(archetypes), 1)

    # ── per‑monster averages ──────────────────────────────────────────
    avg_base_gold = sum(a.base_reward.gold for a in archetypes) / n
    avg_growth_gold = sum(a.reward_growth.gold for a in archetypes) / n
    avg_base_xp = sum(a.base_reward.experience for a in archetypes) / n
    avg_growth_xp = sum(a.reward_growth.experience for a in archetypes) / n

    avg_base_mats: dict[str, float] = {}
    avg_growth_mats: dict[str, float] = {}
    for a in archetypes:
        for mat_id, qty in a.base_reward.materials.items():
            avg_base_mats[mat_id] = avg_base_mats.get(mat_id, 0) + qty / n
        for mat_id, qty in a.reward_growth.materials.items():
            avg_growth_mats[mat_id] = avg_growth_mats.get(mat_id, 0) + qty / n

    # ── accumulate across turns ────────────────────────────────────────
    total_gold = definition.starting_gold
    total_xp = 0.0
    total_materials: dict[str, float] = {}

    for turn in range(1, max_turns + 1):
        count = rules.monster_spawn.count_curve.value_at(turn)
        scale = rules.monster_spawn.reward_growth_curve.value_at(turn)

        total_gold += int(count * (avg_base_gold + avg_growth_gold * scale))
        total_xp += count * (avg_base_xp + avg_growth_xp * scale)

        for mat_id, base_qty in avg_base_mats.items():
            growth_qty = avg_growth_mats.get(mat_id, 0)
            total_materials[mat_id] = total_materials.get(mat_id, 0) + count * (base_qty + growth_qty * scale)

    # Add starting materials
    for mat_id, qty in definition.starting_materials.items():
        total_materials[mat_id] = total_materials.get(mat_id, 0) + qty

    # ── generous multiplier ─────────────────────────────────────────────
    gold = int(total_gold * 1.4)
    xp = int(total_xp * 1.4)
    materials = {k: max(1, int(v * 1.4)) for k, v in total_materials.items()}

    return Budget(gold=gold, xp_pool=xp, materials=materials)


# ── team builder ──────────────────────────────────────────────────────────

@dataclass
class TeamBuild:
    adventurers: list[AdventurerState]
    upgrade_ids: set[str]
    equipment_instances: list[EquipmentInstance]
    total_gold_spent: int
    total_xp_spent: int
    materials_spent: dict[str, int]


def build_team(definition: GameDefinition, budget: Budget) -> TeamBuild:
    """Greedily build the strongest possible team within budget."""

    content = definition.content
    exp_rules = content.experience_rules
    party_limit = definition.rules.recruitment.maximum_party_size_limit

    remaining_gold = budget.gold
    remaining_xp = budget.xp_pool
    remaining_materials = dict(budget.materials)

    # ── Phase 1: pick global upgrades (benefit all party members) ─────────
    # We use a greedy set-cover style: repeatedly pick the upgrade with best
    # power/cost among those whose prerequisites are already met.
    chosen_upgrades: set[str] = set()
    unlocked: set[str] = set()  # all upgrades whose prereqs are satisfied

    all_upgrades = list(content.global_upgrades)
    upgrade_by_id = {u.upgrade_id: u for u in all_upgrades}

    # Find upgrades with no prerequisites
    for u in all_upgrades:
        req = set(u.required_upgrade_ids)
        if not req or req.issubset(chosen_upgrades):
            unlocked.add(u.upgrade_id)

    while unlocked:
        # Pick the best value upgrade we can afford
        best = None
        best_value = 0.0
        for uid in unlocked:
            if uid in chosen_upgrades:
                continue
            u = upgrade_by_id[uid]
            if u.gold_cost > remaining_gold:
                continue
            # Value: stat bonus × party_size (it benefits everyone) + party_size_bonus value
            mod_value = modifier_power(u.stat_modifier) * party_limit
            # Rough value of +1 party size: ~80 power per slot (from search heuristic)
            mod_value += u.party_size_bonus * 80.0
            value_per_gold = mod_value / max(u.gold_cost, 1)
            if value_per_gold > best_value:
                best_value = value_per_gold
                best = uid

        if best is None:
            break

        u = upgrade_by_id[best]
        chosen_upgrades.add(best)
        remaining_gold -= u.gold_cost
        unlocked.discard(best)

        # Unlock upgrades whose prerequisites are now satisfied
        for u2 in all_upgrades:
            if u2.upgrade_id not in chosen_upgrades:
                req2 = set(u2.required_upgrade_ids)
                if not req2 or req2.issubset(chosen_upgrades):
                    unlocked.add(u2.upgrade_id)

    # ── Phase 2: pick recruits ────────────────────────────────────────────
    recruitable = list(content.recruitable_adventurers)

    # Compute "fully leveled" stats for each template to compare them
    def template_final_power(template, level: int) -> float:
        """Estimate final stat power at a given level."""
        growth = scale_growth_to_level(template.stat_growth_per_level, level, exp_rules)
        stats = apply_stat_modifier(template.base_stats, growth)
        # Apply upgrade bonuses
        for uid in chosen_upgrades:
            stats = apply_stat_modifier(stats, upgrade_by_id[uid].stat_modifier)
        return stat_power(stats)

    # Score each template at level 1 as baseline
    scored_templates = []
    for t in recruitable:
        base_power = stat_power(t.base_stats)
        scored_templates.append((base_power, t))

    scored_templates.sort(key=lambda x: -x[0])

    # Pick the top N that fit in party and budget
    chosen_templates = []
    for _, t in scored_templates:
        if len(chosen_templates) >= party_limit:
            break
        if t.recruit_gold > remaining_gold:
            continue
        chosen_templates.append(t)
        remaining_gold -= t.recruit_gold

    if not chosen_templates:
        raise ValueError("Cannot afford any adventurers!")

    # ── Phase 3: allocate XP ──────────────────────────────────────────────
    # Distribute XP evenly among recruits, then level each one up.
    adventurers: list[AdventurerState] = []
    xp_per = remaining_xp // len(chosen_templates)

    total_xp_spent = 0
    for i, template in enumerate(chosen_templates):
        # Determine max affordable level
        level = 1
        xp_spent = 0
        while True:
            next_xp = required_experience_for_next_level(level + 1, exp_rules)
            if xp_spent + next_xp > xp_per:
                break
            if level + 1 > exp_rules.max_level:
                break
            xp_spent += next_xp
            level += 1

        total_xp_spent += xp_spent

        # Build the adventurer
        growth = scale_growth_to_level(template.stat_growth_per_level, level, exp_rules)
        final_base = apply_stat_modifier(template.base_stats, growth)

        # Unlock level skills
        level_skills = []
        for unlock in template.level_skill_unlocks:
            if level >= unlock.level:
                level_skills.extend(unlock.skills)

        adv = AdventurerState(
            adventurer_id=f"ceiling_{i:02d}",
            name=template.name,
            base_stats=final_base,
            resources=CombatResources.full(final_base),
            skills=tuple(template.skills) + tuple(level_skills),
            level_skill_unlocks=template.level_skill_unlocks,
            stat_growth_per_level=template.stat_growth_per_level,
            level=level,
            experience=xp_spent,
            template_id=template.template_id,
        )
        adventurers.append(adv)

    # ── Phase 4: equip adventurers ────────────────────────────────────────
    equipment_templates = list(content.equipment_templates)
    recipes = list(content.crafting_recipes)
    recipe_by_output: dict[str, CraftingRecipe] = {}
    for r in recipes:
        recipe_by_output[r.output_template_id] = r

    equipment_instances: list[EquipmentInstance] = []
    eq_counter = 1

    for adv in adventurers:
        # Find the best equipment set this adventurer can use within remaining budget.
        # We model this as: for each slot, pick the best affordable item.
        # Special case: compare two_hand vs main_hand+off_hand.
        best_loadout, cost_gold, cost_mats = _pick_best_loadout(
            adv,
            equipment_templates,
            recipe_by_output,
            remaining_gold,
            remaining_materials,
        )

        # Apply loadout
        items = []
        for tmpl in best_loadout:
            recipe = recipe_by_output.get(tmpl.equipment_id)
            if recipe is None:
                continue
            instance_id = f"eq_{eq_counter:04d}"
            eq_counter += 1
            equipment_instances.append(
                EquipmentInstance(instance_id=instance_id, template_id=tmpl.equipment_id)
            )
            items.append(EquippedItem(slot=tmpl.slot, instance_id=instance_id))

        adv = AdventurerState(
            adventurer_id=adv.adventurer_id,
            name=adv.name,
            base_stats=adv.base_stats,
            resources=adv.resources,
            skills=adv.skills,
            level_skill_unlocks=adv.level_skill_unlocks,
            stat_growth_per_level=adv.stat_growth_per_level,
            level=adv.level,
            experience=adv.experience,
            equipment=EquipmentLoadout(items=tuple(items)),
            template_id=adv.template_id,
        )
        # Replace in list
        for j, a in enumerate(adventurers):
            if a.adventurer_id == adv.adventurer_id:
                adventurers[j] = adv
                break

        remaining_gold -= cost_gold
        for mat_id, qty in cost_mats.items():
            remaining_materials[mat_id] = remaining_materials.get(mat_id, 0) - qty

    total_gold_spent = budget.gold - remaining_gold
    materials_spent = {
        k: budget.materials.get(k, 0) - remaining_materials.get(k, 0)
        for k in set(budget.materials) | set(remaining_materials)
    }
    # Filter out zero/negative entries
    materials_spent = {k: v for k, v in materials_spent.items() if v > 0}

    return TeamBuild(
        adventurers=adventurers,
        upgrade_ids=chosen_upgrades,
        equipment_instances=equipment_instances,
        total_gold_spent=total_gold_spent,
        total_xp_spent=total_xp_spent,
        materials_spent=materials_spent,
    )


def _pick_best_loadout(
    adv: AdventurerState,
    all_equipment: list[EquipmentTemplate],
    recipe_by_output: dict[str, CraftingRecipe],
    gold_budget: int,
    material_budget: dict[str, int],
) -> tuple[list[EquipmentTemplate], int, dict[str, int]]:
    """Pick the best equipment loadout for an adventurer within budget."""

    # Group equipment by slot
    by_slot: dict[str, list[EquipmentTemplate]] = {s: [] for s in EQUIPMENT_SLOTS}
    for tmpl in all_equipment:
        # Check class restriction
        if tmpl.allowed_classes and adv.template_id not in tmpl.allowed_classes:
            continue
        by_slot[tmpl.slot].append(tmpl)

    def item_value(tmpl: EquipmentTemplate) -> float:
        v = modifier_power(tmpl.stat_modifier)
        # Rough skill value
        v += len(tmpl.skills) * 5.0
        return v

    def can_afford(tmpl: EquipmentTemplate) -> bool:
        recipe = recipe_by_output.get(tmpl.equipment_id)
        if recipe is None:
            return False
        if recipe.gold_cost > gold_budget:
            return False
        for cost in recipe.material_costs:
            if material_budget.get(cost.material_id, 0) < cost.quantity:
                return False
        return True

    def item_cost(tmpl: EquipmentTemplate) -> tuple[int, dict[str, int]]:
        recipe = recipe_by_output.get(tmpl.equipment_id)
        if recipe is None:
            return (0, {})
        return (
            recipe.gold_cost,
            {cost.material_id: cost.quantity for cost in recipe.material_costs},
        )

    # Pick best for each non-hand slot
    chosen: list[EquipmentTemplate] = []
    total_gold = 0
    total_mats: dict[str, int] = {}

    for slot in ["armor", "helmet", "boots", "accessory"]:
        candidates = [t for t in by_slot.get(slot, []) if can_afford(t)]
        if not candidates:
            continue
        best = max(candidates, key=item_value)
        chosen.append(best)
        g, m = item_cost(best)
        total_gold += g
        for mat_id, qty in m.items():
            total_mats[mat_id] = total_mats.get(mat_id, 0) + qty
        gold_budget -= g
        for mat_id, qty in m.items():
            material_budget[mat_id] = material_budget.get(mat_id, 0) - qty

    # Compare two_hand vs main_hand + off_hand
    two_hand_candidates = [t for t in by_slot.get("two_hand", []) if can_afford(t)]
    main_hand_candidates = [t for t in by_slot.get("main_hand", []) if can_afford(t)]
    off_hand_candidates = [t for t in by_slot.get("off_hand", []) if can_afford(t)]

    best_two_hand = max(two_hand_candidates, key=item_value) if two_hand_candidates else None
    best_mh = max(main_hand_candidates, key=item_value) if main_hand_candidates else None
    best_oh = max(off_hand_candidates, key=item_value) if off_hand_candidates else None

    two_hand_value = item_value(best_two_hand) if best_two_hand else -1
    dual_value = (
        (item_value(best_mh) + item_value(best_oh))
        if (best_mh and best_oh)
        else -1
    )

    if two_hand_value >= dual_value and best_two_hand is not None:
        chosen.append(best_two_hand)
        g, m = item_cost(best_two_hand)
        total_gold += g
        for mat_id, qty in m.items():
            total_mats[mat_id] = total_mats.get(mat_id, 0) + qty
    elif dual_value >= 0 and best_mh is not None and best_oh is not None:
        chosen.append(best_mh)
        g, m = item_cost(best_mh)
        total_gold += g
        for mat_id, qty in m.items():
            total_mats[mat_id] = total_mats.get(mat_id, 0) + qty
        chosen.append(best_oh)
        g, m = item_cost(best_oh)
        total_gold += g
        for mat_id, qty in m.items():
            total_mats[mat_id] = total_mats.get(mat_id, 0) + qty

    return chosen, total_gold, total_mats


def scale_growth_to_level(
    growth: CombatStatModifier,
    level: int,
    exp_rules: ExperienceRules,
) -> CombatStatModifier:
    """Scale per-level growth to the total bonus at a given level."""
    if level <= 1:
        return CombatStatModifier()
    levels_gained = level - 1
    return CombatStatModifier(
        hp=growth.hp * levels_gained,
        mp=growth.mp * levels_gained,
        attack=growth.attack * levels_gained,
        defense=growth.defense * levels_gained,
        speed=growth.speed * levels_gained,
        recovery=growth.recovery * levels_gained,
        mp_recovery=growth.mp_recovery * levels_gained,
    )


# ── difficulty sweep ─────────────────────────────────────────────────────

def sweep_difficulty(
    definition: GameDefinition,
    build: TeamBuild,
    *,
    seed: int = 20260526,
    waves: int = 500,
    wave_size: int = 6,
    min_diff: int = 2,
    max_diff: int = 200,
    steps: int = 20,
) -> list[tuple[int, float]]:
    """Test the built team against escalating difficulty factors.

    Returns a list of (max_difficulty_factor, score) pairs.
    """

    # Build a minimal GameState from the team
    state = GameState(
        turn=definition.rules.max_turns,
        max_turns=definition.rules.max_turns,
        seed=definition.rules.seed,
        gold=0,
        materials={},
        experience_pool=0,
        adventurers=tuple(build.adventurers),
        equipment_inventory=tuple(build.equipment_instances),
        unlocked_upgrade_ids=frozenset(build.upgrade_ids),
        current_monsters=(),
        recruit_candidates=(),
    )

    results = []
    seen_diffs: set[int] = set()

    # Generate difficulty steps — use exponential spacing to cover a wide range

    for i in range(steps):
        t = i / max(steps - 1, 1)
        # Exponentially spaced: from min_diff to max_diff
        max_d = int(min_diff + (max_diff - min_diff) * (t ** 1.5))
        max_d = max(min_diff, min(max_d, max_diff))
        # Avoid duplicates
        if max_d in seen_diffs:
            max_d += 1
            while max_d in seen_diffs and max_d <= max_diff:
                max_d += 1
        if max_d > max_diff or max_d in seen_diffs:
            continue
        seen_diffs.add(max_d)

        # Create a difficulty factor tuple: spread around the target
        if max_d <= 5:
            diffs = tuple(range(1, max_d + 1))
        else:
            # 4 factors roughly spanning from max_d/2 to max_d
            diffs = (
                max(1, max_d // 4),
                max(2, max_d // 2),
                max(3, max_d * 3 // 4),
                max_d,
            )

        scoring = ScoringRules(
            mode="endgame_arena",
            seed=seed,
            waves=waves,
            wave_size=wave_size,
            difficulty_factors=diffs,
            resource_mode="full",
            aggregation="best_assignment",
            elite_chance=definition.scoring.elite_chance,
            elite_stat_multiplier=definition.scoring.elite_stat_multiplier,
            boss_chance=definition.scoring.boss_chance,
            boss_stat_multiplier=definition.scoring.boss_stat_multiplier,
        )

        test_def = GameDefinition(
            content=definition.content,
            rules=definition.rules,
            starting_gold=definition.starting_gold,
            starting_materials=dict(definition.starting_materials),
            scoring=scoring,
        )

        report = score_final_state(test_def, state, waves=waves)
        results.append((max_d, report.score))
        print(
            f"  diff ~{max_d:4d}  →  score={report.score:6.2f}  "
            f"(win_rate={report.chosen_win_rate:.2%}, "
            f"battles={report.chosen_battles})"
        )

    return results


# ── main ──────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Estimate scoring ceiling for a game preset."
    )
    parser.add_argument(
        "--preset",
        default="default",
        choices=["default", "full"],
        help="Game preset to analyze",
    )
    parser.add_argument(
        "--gold",
        type=int,
        default=None,
        help="Gold budget override (default: auto-estimate 1.4x max)",
    )
    parser.add_argument(
        "--xp",
        type=int,
        default=None,
        help="XP pool override (default: auto-estimate 1.4x max)",
    )
    parser.add_argument(
        "--max-diff",
        type=int,
        default=200,
        help="Maximum difficulty factor to sweep to (default: 200)",
    )
    parser.add_argument(
        "--steps",
        type=int,
        default=20,
        help="Number of difficulty steps (default: 20)",
    )
    parser.add_argument(
        "--waves",
        type=int,
        default=500,
        help="Arena waves per evaluation (default: 500)",
    )
    parser.add_argument(
        "--no-elites",
        action="store_true",
        help="Disable elite/boss spawns in scoring for cleaner signal",
    )
    args = parser.parse_args()

    print(f"=== Loading preset: {args.preset} ===")
    preset_path = resolve_data_preset(preset=args.preset).data_dir
    definition = load_game_definition(str(preset_path))

    # Budget
    auto_budget = default_budget(definition)
    gold = args.gold if args.gold is not None else auto_budget.gold
    xp = args.xp if args.xp is not None else auto_budget.xp_pool
    budget = Budget(gold=gold, xp_pool=xp, materials=dict(auto_budget.materials))

    print(f"\nBudget:")
    print(f"  Gold:      {budget.gold}")
    print(f"  XP pool:   {budget.xp_pool}")
    print(f"  Materials: {budget.materials}")
    print(f"  Max turns: {definition.rules.max_turns}")
    print(f"  Max party: {definition.rules.recruitment.maximum_party_size_limit}")

    # Build team
    print(f"\n=== Building optimal team ===")
    build = build_team(definition, budget)

    print(f"Team built:")
    print(f"  Upgrades: {len(build.upgrade_ids)} — {sorted(build.upgrade_ids)}")
    print(f"  Adventurers: {len(build.adventurers)}")
    for adv in build.adventurers:
        # Compute effective stats with upgrades
        stats = adv.base_stats
        for uid in build.upgrade_ids:
            for u in definition.content.global_upgrades:
                if u.upgrade_id == uid:
                    stats = apply_stat_modifier(stats, u.stat_modifier)
                    break
        # Add equipment
        from guild_manager_bench.game.equipment import apply_equipment_stats
        eq_templates = [
            t for t in definition.content.equipment_templates
            if any(
                ei.template_id == t.equipment_id
                for ei in build.equipment_instances
                if any(
                    item.instance_id == ei.instance_id
                    for item in adv.equipment.items
                )
            )
        ]
        stats = apply_equipment_stats(stats, eq_templates)
        print(
            f"    Lv{adv.level:2d} {adv.name:12s}  "
            f"ATK={stats.attack:3d}  DEF={stats.defense:3d}  "
            f"SPD={stats.speed:3d}  HP={stats.hp:4d}  "
            f"power={stat_power(stats):.1f}"
        )
    print(f"  Equipment: {len(build.equipment_instances)} pieces")
    print(f"  Gold spent: {build.total_gold_spent} / {budget.gold}")
    print(f"  XP spent:   {build.total_xp_spent} / {budget.xp_pool}")

    # Sweep difficulty
    print(f"\n=== Sweeping difficulty (1–{args.max_diff}, {args.steps} steps) ===")
    if args.no_elites:
        definition = GameDefinition(
            content=definition.content,
            rules=definition.rules,
            starting_gold=definition.starting_gold,
            starting_materials=dict(definition.starting_materials),
            scoring=ScoringRules(
                mode="endgame_arena",
                seed=definition.scoring.seed,
                waves=definition.scoring.waves,
                wave_size=definition.scoring.wave_size,
                difficulty_factors=definition.scoring.difficulty_factors,
                resource_mode="full",
                aggregation="best_assignment",
                elite_chance=0.0,
                boss_chance=0.0,
            ),
        )

    results = sweep_difficulty(
        definition,
        build,
        seed=definition.scoring.seed,
        waves=args.waves,
        max_diff=args.max_diff,
        steps=args.steps,
    )

    # Summary
    print(f"\n=== Summary ===")
    print(f"{'MaxDiff':>8s}  {'Score':>8s}")
    print("-" * 20)
    for max_d, score in results:
        print(f"{max_d:8d}  {score:8.2f}")

    # Find where score drops below thresholds
    thresholds = [95, 90, 80, 70, 60, 50]
    print(f"\nDifficulty at score thresholds:")
    for threshold in thresholds:
        for max_d, score in results:
            if score < threshold:
                print(f"  Score < {threshold:2d}: difficulty ~{max_d}")
                break
        else:
            print(f"  Score < {threshold:2d}: > {results[-1][0] if results else '?'}")


if __name__ == "__main__":
    main()
