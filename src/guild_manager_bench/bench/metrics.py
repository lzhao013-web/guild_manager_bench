from __future__ import annotations

import random
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from guild_manager_bench.game.combat import Combatant, run_auto_battle
from guild_manager_bench.game.engine import effective_adventurer_skills, effective_adventurer_stats
from guild_manager_bench.game.models import CombatResources, CombatStats, apply_stat_modifier, scale_stat_modifier
from guild_manager_bench.game.state import AdventurerState, GameDefinition, GameState, MonsterArchetype


@dataclass(frozen=True, slots=True)
class AdventurerScoreBreakdown:
    """单个冒险者在终局评分匹配中的贡献。"""

    adventurer_id: str
    name: str
    assignments: int
    average_score: float
    win_rate: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "adventurer_id": self.adventurer_id,
            "name": self.name,
            "assignments": self.assignments,
            "average_score": self.average_score,
            "win_rate": self.win_rate,
        }


@dataclass(frozen=True, slots=True)
class ScoreReport:
    """终局 Arena 评分结果。"""

    score: float
    mode: str
    seed: int
    waves: int
    wave_size: int
    difficulty_factors: tuple[int, ...]
    resource_mode: str
    aggregation: str
    simulated_battles: int
    chosen_battles: int
    chosen_win_rate: float
    per_adventurer: tuple[AdventurerScoreBreakdown, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "score": self.score,
            "mode": self.mode,
            "seed": self.seed,
            "waves": self.waves,
            "wave_size": self.wave_size,
            "difficulty_factors": list(self.difficulty_factors),
            "resource_mode": self.resource_mode,
            "aggregation": self.aggregation,
            "simulated_battles": self.simulated_battles,
            "chosen_battles": self.chosen_battles,
            "chosen_win_rate": self.chosen_win_rate,
            "per_adventurer": [item.to_dict() for item in self.per_adventurer],
        }


@dataclass(frozen=True, slots=True)
class _ArenaMonster:
    monster_id: str
    archetype_id: str
    stats: CombatStats
    skills: tuple[Any, ...]


@dataclass(frozen=True, slots=True)
class _BattleEvaluation:
    score: float
    won: bool


def total_effective_level(state: GameState) -> int:
    """返回队伍等级总和，作为临时运行统计。"""

    return sum(adventurer.level for adventurer in state.adventurers)


def score_final_state(definition: GameDefinition, state: GameState) -> ScoreReport:
    """通过固定 Arena 大量模拟评估终局队伍战力。"""

    rules = definition.scoring
    rng = random.Random(rules.seed)
    adventurers = tuple(state.adventurers)
    per_adventurer_score = {item.adventurer_id: 0.0 for item in adventurers}
    per_adventurer_wins = {item.adventurer_id: 0 for item in adventurers}
    per_adventurer_assignments = {item.adventurer_id: 0 for item in adventurers}

    total_score = 0.0
    chosen_wins = 0
    chosen_battles = 0
    simulated_battles = 0
    max_pairs_per_wave = min(len(adventurers), rules.wave_size)

    for wave_index in range(rules.waves):
        difficulty = rules.difficulty_factors[wave_index % len(rules.difficulty_factors)]
        monsters = tuple(
            _sample_arena_monster(
                definition,
                rng,
                wave_index=wave_index + 1,
                index=index + 1,
                difficulty=difficulty,
            )
            for index in range(rules.wave_size)
        )
        matrix = [
            [
                _evaluate_battle(definition, state, adventurer, monster)
                for monster in monsters
            ]
            for adventurer in adventurers
        ]
        simulated_battles += len(adventurers) * len(monsters)
        for adventurer_index, monster_index in _best_assignment(matrix):
            evaluation = matrix[adventurer_index][monster_index]
            adventurer = adventurers[adventurer_index]
            total_score += evaluation.score
            chosen_wins += 1 if evaluation.won else 0
            chosen_battles += 1
            per_adventurer_score[adventurer.adventurer_id] += evaluation.score
            per_adventurer_wins[adventurer.adventurer_id] += 1 if evaluation.won else 0
            per_adventurer_assignments[adventurer.adventurer_id] += 1

    denominator = rules.waves * max_pairs_per_wave * 100
    score = _round_score(100 * total_score / denominator) if denominator else 0.0
    per_adventurer = tuple(
        AdventurerScoreBreakdown(
            adventurer_id=adventurer.adventurer_id,
            name=adventurer.name,
            assignments=per_adventurer_assignments[adventurer.adventurer_id],
            average_score=_average(
                per_adventurer_score[adventurer.adventurer_id],
                per_adventurer_assignments[adventurer.adventurer_id],
            ),
            win_rate=_average(
                per_adventurer_wins[adventurer.adventurer_id],
                per_adventurer_assignments[adventurer.adventurer_id],
            ),
        )
        for adventurer in adventurers
    )
    return ScoreReport(
        score=score,
        mode=rules.mode,
        seed=rules.seed,
        waves=rules.waves,
        wave_size=rules.wave_size,
        difficulty_factors=rules.difficulty_factors,
        resource_mode=rules.resource_mode,
        aggregation=rules.aggregation,
        simulated_battles=simulated_battles,
        chosen_battles=chosen_battles,
        chosen_win_rate=_average(chosen_wins, chosen_battles),
        per_adventurer=per_adventurer,
    )


def _sample_arena_monster(
    definition: GameDefinition,
    rng: random.Random,
    *,
    wave_index: int,
    index: int,
    difficulty: int,
) -> _ArenaMonster:
    archetype = rng.choice(definition.content.monster_archetypes)
    return _arena_monster(archetype, wave_index=wave_index, index=index, difficulty=difficulty)


def _arena_monster(
    archetype: MonsterArchetype,
    *,
    wave_index: int,
    index: int,
    difficulty: int,
) -> _ArenaMonster:
    stats = apply_stat_modifier(
        archetype.base_stats,
        scale_stat_modifier(archetype.stat_growth, difficulty),
    )
    return _ArenaMonster(
        monster_id=f"arena_{wave_index}_monster_{index}",
        archetype_id=archetype.archetype_id,
        stats=stats,
        skills=archetype.skills,
    )


def _evaluate_battle(
    definition: GameDefinition,
    state: GameState,
    adventurer: AdventurerState,
    monster: _ArenaMonster,
) -> _BattleEvaluation:
    stats = effective_adventurer_stats(definition, state, adventurer)
    result = run_auto_battle(
        Combatant(
            combatant_id=adventurer.adventurer_id,
            stats=stats,
            resources=_scoring_resources(definition, adventurer, stats),
            skills=effective_adventurer_skills(definition, state, adventurer),
        ),
        Combatant(
            combatant_id=monster.monster_id,
            stats=monster.stats,
            resources=CombatResources.full(monster.stats),
            skills=monster.skills,
        ),
    )
    enemy_progress = 1 - result.right_resources.current_hp / monster.stats.hp
    survival_margin = result.left_resources.current_hp / stats.hp
    outcome_score = {
        "left_win": 1.0,
        "draw": 0.4,
        "right_win": 0.0,
    }[result.outcome]
    score = 70 * outcome_score + 20 * enemy_progress + 10 * survival_margin
    return _BattleEvaluation(
        score=_round_score(max(0.0, min(100.0, score))),
        won=result.outcome == "left_win",
    )


def _scoring_resources(
    definition: GameDefinition,
    adventurer: AdventurerState,
    stats: CombatStats,
) -> CombatResources:
    if definition.scoring.resource_mode == "current":
        return CombatResources(
            current_hp=min(adventurer.resources.current_hp, stats.hp),
            current_mp=min(adventurer.resources.current_mp, stats.mp),
        )
    return CombatResources.full(stats)


def _best_assignment(matrix: list[list[_BattleEvaluation]]) -> tuple[tuple[int, int], ...]:
    if not matrix or not matrix[0]:
        return ()
    adventurer_count = len(matrix)
    monster_count = len(matrix[0])
    target_pairs = min(adventurer_count, monster_count)

    @lru_cache(maxsize=None)
    def search(
        adventurer_index: int,
        used_monsters: int,
        assigned: int,
    ) -> tuple[float, tuple[tuple[int, int], ...]]:
        if assigned == target_pairs:
            return 0.0, ()
        if adventurer_index >= adventurer_count:
            return float("-inf"), ()
        remaining_adventurers = adventurer_count - adventurer_index
        if assigned + remaining_adventurers < target_pairs:
            return float("-inf"), ()

        best_score = float("-inf")
        best_pairs: tuple[tuple[int, int], ...] = ()
        if assigned + remaining_adventurers > target_pairs:
            best_score, best_pairs = search(adventurer_index + 1, used_monsters, assigned)

        for monster_index in range(monster_count):
            bit = 1 << monster_index
            if used_monsters & bit:
                continue
            rest_score, rest_pairs = search(
                adventurer_index + 1,
                used_monsters | bit,
                assigned + 1,
            )
            candidate_score = matrix[adventurer_index][monster_index].score + rest_score
            if candidate_score > best_score:
                best_score = candidate_score
                best_pairs = ((adventurer_index, monster_index),) + rest_pairs
        return best_score, best_pairs

    return search(0, 0, 0)[1]


def _average(total: float, count: int) -> float:
    if count <= 0:
        return 0.0
    return _round_score(total / count)


def _round_score(value: float) -> float:
    return round(value, 4)
