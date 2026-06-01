from __future__ import annotations

import copyreg
import random
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, replace
from functools import lru_cache
from types import MappingProxyType
from typing import Any, Mapping

from guild_manager_bench.game.combat import Combatant, run_auto_battle
from guild_manager_bench.game.engine import effective_adventurer_skills, effective_adventurer_stats
from guild_manager_bench.game.models import CombatResources, CombatStats, apply_stat_modifier, scale_combat_stats, scale_stat_modifier
from guild_manager_bench.game.skills import Skill, SkillCondition, SkillEffect, StatusDefinition
from guild_manager_bench.game.state import AdventurerState, GameDefinition, GameState, MonsterArchetype, ScoringRules


# MappingProxyType 不可 pickle，ProcessPoolExecutor 需要序列化参数。
def _pickle_mappingproxy(mp: MappingProxyType) -> tuple[type[dict], tuple[dict[str, Any], ...]]:  # pyright: ignore[reportUnusedParameter]
    return dict, (dict(mp),)


copyreg.pickle(MappingProxyType, _pickle_mappingproxy)


@dataclass(frozen=True, slots=True)
class AdventurerScoreBreakdown:
    """单个冒险者在终局评分匹配中的贡献。"""

    adventurer_id: str
    name: str
    assignments: int
    average_score: float
    win_rate: float
    rank_score: float = 0.0
    rank_score_share: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "adventurer_id": self.adventurer_id,
            "name": self.name,
            "assignments": self.assignments,
            "average_score": self.average_score,
            "win_rate": self.win_rate,
            "rank_score": self.rank_score,
            "rank_score_contribution": self.rank_score,
            "rank_score_share": self.rank_score_share,
        }


@dataclass(frozen=True, slots=True)
class ScoreReport:
    """终局 Arena 评分结果。"""

    score: float
    rank_score: float
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
    rank_score_per_adventurer: tuple[dict[str, Any], ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "score": self.score,
            "rank_score": self.rank_score,
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
            "rank_score_per_adventurer": [
                dict(item) for item in self.rank_score_per_adventurer
            ],
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


@dataclass(frozen=True, slots=True)
class _ObservationAdventurer:
    adventurer_id: str
    name: str
    stats: CombatStats
    resources: CombatResources
    skills: tuple[Skill, ...]


@dataclass(frozen=True, slots=True)
class _RankScoreResult:
    rank_score: float
    per_adventurer_score: dict[str, float]
    per_adventurer_assignments: dict[str, int]


def total_effective_level(state: GameState) -> int:
    """返回队伍等级总和，作为临时运行统计。"""

    return sum(adventurer.level for adventurer in state.adventurers)


def score_final_state(
    definition: GameDefinition,
    state: GameState,
    *,
    waves: int | None = None,
) -> ScoreReport:
    """通过固定 Arena 大量模拟评估终局队伍战力。"""

    rules = definition.scoring
    wave_count = waves if waves is not None else rules.waves
    arena_result = _run_arena(definition, state, waves=wave_count)
    rank_result = compute_rank_score_breakdown(definition, state)
    adventurers = tuple(state.adventurers)
    rank_score = rank_result.rank_score

    per_adventurer = tuple(
        AdventurerScoreBreakdown(
            adventurer_id=adventurer.adventurer_id,
            name=adventurer.name,
            assignments=arena_result.per_adventurer_assignments[adventurer.adventurer_id],
            average_score=_average(
                arena_result.per_adventurer_score[adventurer.adventurer_id],
                arena_result.per_adventurer_assignments[adventurer.adventurer_id],
            ),
            win_rate=_average(
                arena_result.per_adventurer_wins[adventurer.adventurer_id],
                arena_result.per_adventurer_assignments[adventurer.adventurer_id],
            ),
            rank_score=rank_result.per_adventurer_score.get(adventurer.adventurer_id, 0.0),
            rank_score_share=_rank_score_share(
                rank_result.per_adventurer_score.get(adventurer.adventurer_id, 0.0),
                rank_score,
            ),
        )
        for adventurer in adventurers
    )
    return ScoreReport(
        score=arena_result.score,
        rank_score=rank_score,
        mode=rules.mode,
        seed=rules.seed,
        waves=rules.waves,
        wave_size=rules.wave_size,
        difficulty_factors=rules.difficulty_factors,
        resource_mode=rules.resource_mode,
        aggregation=rules.aggregation,
        simulated_battles=arena_result.simulated_battles,
        chosen_battles=arena_result.chosen_battles,
        chosen_win_rate=arena_result.chosen_win_rate,
        per_adventurer=per_adventurer,
        rank_score_per_adventurer=_rank_score_contribution_items(
            adventurers,
            rank_result,
        ),
    )


def _compute_difficulty_tier(
    args: tuple[int, GameDefinition, tuple[_ObservationAdventurer, ...], int],
) -> tuple[int, float, dict[str, float], dict[str, int]]:
    """Compute arena score for a single difficulty level.

    Top-level function so it can be pickled by ProcessPoolExecutor.
    """
    difficulty, definition, adventurers, rank_waves = args
    tier_scoring = ScoringRules(
        mode="endgame_arena",
        seed=definition.scoring.seed,
        waves=rank_waves,
        wave_size=definition.scoring.wave_size,
        difficulty_factors=(difficulty,),
        resource_mode=definition.scoring.resource_mode,
        aggregation="best_assignment",
        elite_chance=definition.scoring.elite_chance,
        elite_stat_multiplier=definition.scoring.elite_stat_multiplier,
        boss_chance=definition.scoring.boss_chance,
        boss_stat_multiplier=definition.scoring.boss_stat_multiplier,
    )
    tier_definition = replace(definition, scoring=tier_scoring)
    tier_result = _run_observation_arena(tier_definition, adventurers, waves=rank_waves)
    return (
        difficulty,
        tier_result.score,
        tier_result.per_adventurer_score,
        tier_result.per_adventurer_assignments,
    )


def _compute_difficulty_tier_state(
    args: tuple[int, GameDefinition, GameState, int],
) -> tuple[int, float, dict[str, float], dict[str, int]]:
    """Compute arena score for a single difficulty level (live state version).

    Top-level function so it can be pickled by ProcessPoolExecutor.
    """
    difficulty, definition, state, rank_waves = args
    tier_scoring = ScoringRules(
        mode="endgame_arena",
        seed=definition.scoring.seed,
        waves=rank_waves,
        wave_size=definition.scoring.wave_size,
        difficulty_factors=(difficulty,),
        resource_mode=definition.scoring.resource_mode,
        aggregation="best_assignment",
        elite_chance=definition.scoring.elite_chance,
        elite_stat_multiplier=definition.scoring.elite_stat_multiplier,
        boss_chance=definition.scoring.boss_chance,
        boss_stat_multiplier=definition.scoring.boss_stat_multiplier,
    )
    tier_definition = replace(definition, scoring=tier_scoring)
    tier_result = _run_arena(tier_definition, state, waves=rank_waves)
    return (
        difficulty,
        tier_result.score,
        tier_result.per_adventurer_score,
        tier_result.per_adventurer_assignments,
    )


def rank_score_from_final_observation(
    definition: GameDefinition,
    observation: Mapping[str, Any],
) -> float:
    """从 replay 的终局 observation 估算段位积分。"""

    return rank_score_breakdown_from_final_observation(
        definition,
        observation,
    )["rank_score"]


def rank_score_breakdown_from_final_observation(
    definition: GameDefinition,
    observation: Mapping[str, Any],
) -> dict[str, Any]:
    """从 replay 终局 observation 计算总段位分和冒险者贡献。"""

    adventurers = _observation_adventurers(observation)
    if not adventurers:
        return {
            "rank_score": 0.0,
            "per_adventurer": [],
        }

    rank_result = _compute_observation_rank_score_breakdown(
        definition,
        adventurers,
    )
    return {
        "rank_score": rank_result.rank_score,
        "per_adventurer": list(
            _rank_score_contribution_items(adventurers, rank_result)
        ),
    }


def _compute_observation_rank_score_breakdown(
    definition: GameDefinition,
    adventurers: tuple[_ObservationAdventurer, ...],
) -> _RankScoreResult:
    rules = definition.scoring
    difficulties = list(range(rules.rank_min_diff, rules.rank_max_diff + 1, rules.rank_step))
    if not adventurers or not difficulties:
        return _empty_rank_score_result(adventurers)

    args_iter = (
        (d, definition, adventurers, rules.rank_waves) for d in difficulties
    )
    with ProcessPoolExecutor() as executor:
        rows = list(executor.map(_compute_difficulty_tier, args_iter))

    return _aggregate_rank_score_rows(
        tuple(item.adventurer_id for item in adventurers),
        rows,
    )


@dataclass(frozen=True, slots=True)
class _ArenaResult:
    """Arena 模拟的原始结果（不含 rank_score）。"""

    score: float
    simulated_battles: int
    chosen_battles: int
    chosen_win_rate: float
    per_adventurer_score: dict[str, float]
    per_adventurer_wins: dict[str, int]
    per_adventurer_assignments: dict[str, int]


def _run_arena(
    definition: GameDefinition,
    state: GameState,
    *,
    waves: int,
) -> _ArenaResult:
    """运行 Arena 模拟并返回原始评分数据。"""

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

    for wave_index in range(waves):
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

    denominator = waves * rules.wave_size * 100
    score = _round_score(100 * total_score / denominator) if denominator else 0.0
    return _ArenaResult(
        score=score,
        simulated_battles=simulated_battles,
        chosen_battles=chosen_battles,
        chosen_win_rate=_average(chosen_wins, chosen_battles),
        per_adventurer_score=per_adventurer_score,
        per_adventurer_wins=per_adventurer_wins,
        per_adventurer_assignments=per_adventurer_assignments,
    )


def _run_observation_arena(
    definition: GameDefinition,
    adventurers: tuple[_ObservationAdventurer, ...],
    *,
    waves: int,
) -> _ArenaResult:
    """用 observation 中的最终阵容快照运行 Arena 模拟。"""

    rules = definition.scoring
    rng = random.Random(rules.seed)
    per_adventurer_score = {item.adventurer_id: 0.0 for item in adventurers}
    per_adventurer_wins = {item.adventurer_id: 0 for item in adventurers}
    per_adventurer_assignments = {item.adventurer_id: 0 for item in adventurers}

    total_score = 0.0
    chosen_wins = 0
    chosen_battles = 0
    simulated_battles = 0

    for wave_index in range(waves):
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
                _evaluate_observation_battle(definition, adventurer, monster)
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

    denominator = waves * rules.wave_size * 100
    score = _round_score(100 * total_score / denominator) if denominator else 0.0
    return _ArenaResult(
        score=score,
        simulated_battles=simulated_battles,
        chosen_battles=chosen_battles,
        chosen_win_rate=_average(chosen_wins, chosen_battles),
        per_adventurer_score=per_adventurer_score,
        per_adventurer_wins=per_adventurer_wins,
        per_adventurer_assignments=per_adventurer_assignments,
    )


def compute_rank_score(
    definition: GameDefinition,
    state: GameState,
    *,
    executor: ProcessPoolExecutor | None = None,
) -> float:
    """通过线性难度扫描计算段位积分（并行版）。

    在每个难度等级上运行 Arena 模拟，计算 performance × difficulty 的加权和。
    rank = Σ performance(d) × d  for d in [rank_min_diff .. rank_max_diff, step=rank_step]

    如果传入 *executor*，则复用该进程池，避免反复创建/销毁的开销。
    """
    return compute_rank_score_breakdown(
        definition,
        state,
        executor=executor,
    ).rank_score


def compute_rank_score_breakdown(
    definition: GameDefinition,
    state: GameState,
    *,
    executor: ProcessPoolExecutor | None = None,
) -> _RankScoreResult:
    """计算段位积分，并拆出每个冒险者的加权贡献。"""

    rules = definition.scoring
    adventurers = tuple(state.adventurers)
    if not adventurers:
        return _empty_rank_score_result(adventurers)

    difficulties = list(range(rules.rank_min_diff, rules.rank_max_diff + 1, rules.rank_step))
    if not difficulties:
        return _empty_rank_score_result(adventurers)

    args_iter = (
        (d, definition, state, rules.rank_waves) for d in difficulties
    )
    if executor is not None:
        rows = list(executor.map(_compute_difficulty_tier_state, args_iter))
    else:
        with ProcessPoolExecutor() as pool:
            rows = list(pool.map(_compute_difficulty_tier_state, args_iter))

    return _aggregate_rank_score_rows(
        tuple(item.adventurer_id for item in adventurers),
        rows,
    )


def _aggregate_rank_score_rows(
    adventurer_ids: tuple[str, ...],
    rows: list[tuple[int, float, dict[str, float], dict[str, int]]],
) -> _RankScoreResult:
    per_adventurer_score = {adventurer_id: 0.0 for adventurer_id in adventurer_ids}
    per_adventurer_assignments = {adventurer_id: 0 for adventurer_id in adventurer_ids}
    rank_score = 0.0

    for difficulty, score, tier_scores, tier_assignments in rows:
        tier_contribution = (score / 100.0) * difficulty
        rank_score += tier_contribution
        tier_total = sum(
            value
            for value in tier_scores.values()
            if isinstance(value, (int, float))
        )
        for adventurer_id in adventurer_ids:
            adventurer_score = tier_scores.get(adventurer_id, 0.0)
            if tier_total > 0:
                per_adventurer_score[adventurer_id] += (
                    tier_contribution * adventurer_score / tier_total
                )
            per_adventurer_assignments[adventurer_id] += int(
                tier_assignments.get(adventurer_id, 0)
            )

    return _RankScoreResult(
        rank_score=_round_score(rank_score),
        per_adventurer_score={
            adventurer_id: _round_score(score)
            for adventurer_id, score in per_adventurer_score.items()
        },
        per_adventurer_assignments=per_adventurer_assignments,
    )


def _empty_rank_score_result(adventurers) -> _RankScoreResult:
    return _RankScoreResult(
        rank_score=0.0,
        per_adventurer_score={
            adventurer.adventurer_id: 0.0
            for adventurer in adventurers
        },
        per_adventurer_assignments={
            adventurer.adventurer_id: 0
            for adventurer in adventurers
        },
    )


def _rank_score_contribution_items(
    adventurers,
    rank_result: _RankScoreResult,
) -> tuple[dict[str, Any], ...]:
    return tuple(
        {
            "adventurer_id": adventurer.adventurer_id,
            "name": getattr(adventurer, "name", adventurer.adventurer_id),
            "rank_score": rank_result.per_adventurer_score.get(
                adventurer.adventurer_id,
                0.0,
            ),
            "rank_score_contribution": rank_result.per_adventurer_score.get(
                adventurer.adventurer_id,
                0.0,
            ),
            "rank_score_share": _rank_score_share(
                rank_result.per_adventurer_score.get(adventurer.adventurer_id, 0.0),
                rank_result.rank_score,
            ),
            "assignments": rank_result.per_adventurer_assignments.get(
                adventurer.adventurer_id,
                0,
            ),
        }
        for adventurer in adventurers
    )


def _rank_score_share(contribution: float, total: float) -> float:
    if total <= 0:
        return 0.0
    return _round_score(contribution / total)


def _sample_arena_monster(
    definition: GameDefinition,
    rng: random.Random,
    *,
    wave_index: int,
    index: int,
    difficulty: int,
) -> _ArenaMonster:
    eligible = [
        a for a in definition.content.monster_archetypes
        if a.min_turn <= wave_index and a.spawn_weight > 0
    ]
    if not eligible:
        eligible = list(definition.content.monster_archetypes)

    weights = [a.spawn_weight for a in eligible]
    archetype = rng.choices(eligible, weights=weights, k=1)[0]

    tier, tier_stat_multiplier = _roll_arena_tier(rng, definition.scoring)
    return _arena_monster(
        archetype,
        wave_index=wave_index,
        index=index,
        difficulty=difficulty,
        tier=tier,
        tier_stat_multiplier=tier_stat_multiplier,
    )


def _roll_arena_tier(
    rng: random.Random,
    rules: "ScoringRules",
) -> tuple[str, float]:
    if rules.boss_chance > 0 and rng.random() < rules.boss_chance:
        return ("boss", rules.boss_stat_multiplier)
    if rules.elite_chance > 0 and rng.random() < rules.elite_chance:
        return ("elite", rules.elite_stat_multiplier)
    return ("normal", 1.0)


def _arena_monster(
    archetype: MonsterArchetype,
    *,
    wave_index: int,
    index: int,
    difficulty: int,
    tier: str = "normal",
    tier_stat_multiplier: float = 1.0,
) -> _ArenaMonster:
    stats = apply_stat_modifier(
        archetype.base_stats,
        scale_stat_modifier(archetype.stat_growth, difficulty),
    )
    if tier != "normal":
        stats = scale_combat_stats(stats, tier_stat_multiplier)
    return _ArenaMonster(
        monster_id=f"arena_{wave_index}_monster_{index}",
        archetype_id=archetype.archetype_id,
        stats=stats,
        skills=archetype.skills,
    )


def _evaluate_observation_battle(
    definition: GameDefinition,
    adventurer: _ObservationAdventurer,
    monster: _ArenaMonster,
) -> _BattleEvaluation:
    stats = adventurer.stats
    result = run_auto_battle(
        Combatant(
            combatant_id=adventurer.adventurer_id,
            stats=stats,
            resources=_observation_scoring_resources(definition, adventurer),
            skills=adventurer.skills,
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


def _observation_scoring_resources(
    definition: GameDefinition,
    adventurer: _ObservationAdventurer,
) -> CombatResources:
    stats = adventurer.stats
    if definition.scoring.resource_mode == "current":
        return CombatResources(
            current_hp=min(adventurer.resources.current_hp, stats.hp),
            current_mp=min(adventurer.resources.current_mp, stats.mp),
        )
    return CombatResources.full(stats)


def _observation_adventurers(
    observation: Mapping[str, Any],
) -> tuple[_ObservationAdventurer, ...]:
    values = observation.get("adventurers")
    if not isinstance(values, list):
        raise ValueError("final_observation.adventurers must be a list")

    adventurers: list[_ObservationAdventurer] = []
    for index, data in enumerate(values, start=1):
        if not isinstance(data, Mapping):
            raise ValueError(f"final_observation.adventurers[{index}] must be an object")
        adventurer_id = str(data.get("adventurer_id") or f"adventurer_{index}")
        stats_data = data.get("effective_stats") or data.get("base_stats")
        if not isinstance(stats_data, Mapping):
            raise ValueError(f"final_observation.adventurers[{index}] missing effective_stats")
        stats = _combat_stats_from_mapping(stats_data)
        resources_data = data.get("resources")
        resources = (
            _combat_resources_from_mapping(resources_data)
            if isinstance(resources_data, Mapping)
            else CombatResources.full(stats)
        )
        skills_data = data.get("skills", [])
        if not isinstance(skills_data, list):
            raise ValueError(f"final_observation.adventurers[{index}].skills must be a list")
        adventurers.append(
            _ObservationAdventurer(
                adventurer_id=adventurer_id,
                name=str(data.get("name") or adventurer_id),
                stats=stats,
                resources=resources,
                skills=tuple(
                    _skill_from_mapping(item)
                    for item in skills_data
                    if isinstance(item, Mapping)
                ),
            )
        )
    return tuple(adventurers)


def _combat_stats_from_mapping(data: Mapping[str, Any]) -> CombatStats:
    return CombatStats(
        hp=_int_field(data, "hp"),
        mp=_int_field(data, "mp"),
        attack=_int_field(data, "attack"),
        defense=_int_field(data, "defense"),
        speed=_int_field(data, "speed"),
        recovery=_int_field(data, "recovery"),
        mp_recovery=_int_field(data, "mp_recovery", default=0),
    )


def _combat_resources_from_mapping(data: Mapping[str, Any]) -> CombatResources:
    return CombatResources(
        current_hp=_int_field(data, "current_hp"),
        current_mp=_int_field(data, "current_mp"),
    )


def _skill_from_mapping(data: Mapping[str, Any]) -> Skill:
    effects = data.get("effects", [])
    if not isinstance(effects, list):
        raise ValueError("skill.effects must be a list")
    return Skill(
        skill_id=str(data.get("skill_id") or data.get("name") or "skill"),
        name=str(data.get("name") or data.get("skill_id") or "skill"),
        kind=data.get("kind", "active"),
        condition=_skill_condition_from_mapping(data.get("condition")),
        effects=tuple(
            _skill_effect_from_mapping(item)
            for item in effects
            if isinstance(item, Mapping)
        ),
        mp_cost=_int_field(data, "mp_cost", default=0),
        priority=_int_field(data, "priority", default=0),
        once_per_battle=bool(data.get("once_per_battle", False)),
        free=bool(data.get("free", False)),
    )


def _skill_condition_from_mapping(data: Any) -> SkillCondition:
    if not isinstance(data, Mapping):
        return SkillCondition(condition_type="always")
    children = data.get("conditions", [])
    if not isinstance(children, list):
        children = []
    return SkillCondition(
        condition_type=data.get("type", "always"),
        value=data.get("value"),
        conditions=tuple(_skill_condition_from_mapping(item) for item in children),
    )


def _skill_effect_from_mapping(data: Mapping[str, Any]) -> SkillEffect:
    status_data = data.get("status")
    return SkillEffect(
        effect_type=data.get("type"),
        value=data.get("value", 0),
        stat=data.get("stat"),
        target=data.get("target") or "target",
        status=(
            _status_from_mapping(status_data)
            if isinstance(status_data, Mapping)
            else None
        ),
    )


def _status_from_mapping(data: Mapping[str, Any]) -> StatusDefinition:
    effects = data.get("effects", [])
    if not isinstance(effects, list):
        raise ValueError("status.effects must be a list")
    return StatusDefinition(
        status_id=str(data.get("status_id") or data.get("name") or "status"),
        name=str(data.get("name") or data.get("status_id") or "status"),
        duration=_int_field(data, "duration"),
        effects=tuple(
            _skill_effect_from_mapping(item)
            for item in effects
            if isinstance(item, Mapping)
        ),
        polarity=data.get("polarity", "neutral"),
        stack_mode=data.get("stack_mode", "refresh"),
    )


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


def _int_field(data: Mapping[str, Any], key: str, *, default: int | None = None) -> int:
    value = data.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{key} must be an int")
    return value
