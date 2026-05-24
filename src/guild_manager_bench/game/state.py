from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Mapping

from guild_manager_bench.game.crafting import CraftingRecipe
from guild_manager_bench.game.equipment import EquipmentInstance, EquipmentLoadout, EquipmentTemplate
from guild_manager_bench.game.models import CombatResources, CombatStatModifier, CombatStats
from guild_manager_bench.game.progression import ExperienceRules
from guild_manager_bench.game.skills import Skill
from guild_manager_bench.game.upgrades import GlobalUpgrade


@dataclass(frozen=True, slots=True)
class RewardBundle:
    """战斗胜利后获得的奖励。"""

    gold: int = 0
    experience: int = 0
    materials: Mapping[str, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_at_least("gold", self.gold, 0)
        _require_at_least("experience", self.experience, 0)
        object.__setattr__(self, "materials", _freeze_non_negative_mapping(self.materials))

    def __add__(self, other: RewardBundle) -> RewardBundle:
        if not isinstance(other, RewardBundle):
            return NotImplemented
        materials = dict(self.materials)
        for material_id, quantity in other.materials.items():
            materials[material_id] = materials.get(material_id, 0) + quantity
        return RewardBundle(
            gold=self.gold + other.gold,
            experience=self.experience + other.experience,
            materials=materials,
        )


@dataclass(frozen=True, slots=True)
class IntCurve:
    """基于回合数的整数曲线。"""

    base: int
    per_turn: int = 0
    minimum: int = 0
    maximum: int | None = None

    def __post_init__(self) -> None:
        _require_at_least("base", self.base, 0)
        _require_at_least("per_turn", self.per_turn, 0)
        _require_at_least("minimum", self.minimum, 0)
        if self.maximum is not None:
            _require_at_least("maximum", self.maximum, self.minimum)

    def value_at(self, turn: int) -> int:
        """返回指定回合的曲线值。"""

        _require_at_least("turn", turn, 1)
        value = self.base + self.per_turn * (turn - 1)
        value = max(self.minimum, value)
        if self.maximum is not None:
            value = min(self.maximum, value)
        return value


@dataclass(frozen=True, slots=True)
class MonsterSpawnRules:
    """每回合怪物刷新规则。"""

    count_curve: IntCurve
    stat_growth_curve: IntCurve = field(default_factory=lambda: IntCurve(base=0, per_turn=1))
    reward_growth_curve: IntCurve = field(default_factory=lambda: IntCurve(base=0, per_turn=1))

    def __post_init__(self) -> None:
        if not isinstance(self.count_curve, IntCurve):
            raise TypeError("count_curve must be IntCurve")
        if not isinstance(self.stat_growth_curve, IntCurve):
            raise TypeError("stat_growth_curve must be IntCurve")
        if not isinstance(self.reward_growth_curve, IntCurve):
            raise TypeError("reward_growth_curve must be IntCurve")


@dataclass(frozen=True, slots=True)
class TurnRecoveryRules:
    """回合结束后的冒险者恢复规则。"""

    hp: int = 0
    mp: int = 0
    hp_percent: float = 0.0
    mp_percent: float = 0.0
    use_recovery_stat: bool = True

    def __post_init__(self) -> None:
        _require_at_least("hp", self.hp, 0)
        _require_at_least("mp", self.mp, 0)
        _require_ratio("hp_percent", self.hp_percent)
        _require_ratio("mp_percent", self.mp_percent)
        if not isinstance(self.use_recovery_stat, bool):
            raise TypeError("use_recovery_stat must be a bool")


@dataclass(frozen=True, slots=True)
class MonsterArchetype:
    """怪物原型。"""

    archetype_id: str
    name: str
    base_stats: CombatStats
    base_reward: RewardBundle
    stat_growth: CombatStatModifier = field(default_factory=CombatStatModifier)
    reward_growth: RewardBundle = field(default_factory=RewardBundle)
    skills: tuple[Skill, ...] = ()

    def __post_init__(self) -> None:
        _require_non_empty("archetype_id", self.archetype_id)
        _require_non_empty("name", self.name)
        if not isinstance(self.base_stats, CombatStats):
            raise TypeError("base_stats must be CombatStats")
        if not isinstance(self.base_reward, RewardBundle):
            raise TypeError("base_reward must be RewardBundle")
        if not isinstance(self.stat_growth, CombatStatModifier):
            raise TypeError("stat_growth must be CombatStatModifier")
        if not isinstance(self.reward_growth, RewardBundle):
            raise TypeError("reward_growth must be RewardBundle")
        object.__setattr__(self, "skills", tuple(self.skills))
        for skill in self.skills:
            if not isinstance(skill, Skill):
                raise TypeError("skills must be Skill")


@dataclass(frozen=True, slots=True)
class SpawnedMonster:
    """当前回合刷出的怪物。"""

    monster_id: str
    archetype_id: str
    name: str
    stats: CombatStats
    reward: RewardBundle
    skills: tuple[Skill, ...] = ()

    def __post_init__(self) -> None:
        _require_non_empty("monster_id", self.monster_id)
        _require_non_empty("archetype_id", self.archetype_id)
        _require_non_empty("name", self.name)
        if not isinstance(self.stats, CombatStats):
            raise TypeError("stats must be CombatStats")
        if not isinstance(self.reward, RewardBundle):
            raise TypeError("reward must be RewardBundle")
        object.__setattr__(self, "skills", tuple(self.skills))
        for skill in self.skills:
            if not isinstance(skill, Skill):
                raise TypeError("skills must be Skill")


@dataclass(frozen=True, slots=True)
class AdventurerState:
    """冒险者当前状态。"""

    adventurer_id: str
    name: str
    base_stats: CombatStats
    resources: CombatResources
    skills: tuple[Skill, ...] = ()
    level: int = 1
    experience: int = 0
    equipment: EquipmentLoadout = field(default_factory=EquipmentLoadout)

    def __post_init__(self) -> None:
        _require_non_empty("adventurer_id", self.adventurer_id)
        _require_non_empty("name", self.name)
        _require_at_least("level", self.level, 1)
        _require_at_least("experience", self.experience, 0)
        if not isinstance(self.base_stats, CombatStats):
            raise TypeError("base_stats must be CombatStats")
        if not isinstance(self.resources, CombatResources):
            raise TypeError("resources must be CombatResources")
        if not isinstance(self.equipment, EquipmentLoadout):
            raise TypeError("equipment must be EquipmentLoadout")
        object.__setattr__(self, "skills", tuple(self.skills))
        for skill in self.skills:
            if not isinstance(skill, Skill):
                raise TypeError("skills must be Skill")


@dataclass(frozen=True, slots=True)
class GameContent:
    """一局游戏使用的静态内容。"""

    adventurers: tuple[AdventurerState, ...]
    monster_archetypes: tuple[MonsterArchetype, ...]
    equipment_templates: tuple[EquipmentTemplate, ...] = ()
    crafting_recipes: tuple[CraftingRecipe, ...] = ()
    global_upgrades: tuple[GlobalUpgrade, ...] = ()
    experience_rules: ExperienceRules = field(default_factory=ExperienceRules)

    def __post_init__(self) -> None:
        object.__setattr__(self, "adventurers", tuple(self.adventurers))
        object.__setattr__(self, "monster_archetypes", tuple(self.monster_archetypes))
        object.__setattr__(self, "equipment_templates", tuple(self.equipment_templates))
        object.__setattr__(self, "crafting_recipes", tuple(self.crafting_recipes))
        object.__setattr__(self, "global_upgrades", tuple(self.global_upgrades))

        _validate_unique_ids("adventurer", (item.adventurer_id for item in self.adventurers))
        _validate_unique_ids("monster archetype", (item.archetype_id for item in self.monster_archetypes))
        _validate_unique_ids("equipment template", (item.equipment_id for item in self.equipment_templates))
        _validate_unique_ids("crafting recipe", (item.recipe_id for item in self.crafting_recipes))
        _validate_unique_ids("global upgrade", (item.upgrade_id for item in self.global_upgrades))

        if not self.adventurers:
            raise ValueError("content must have at least one adventurer")
        if not self.monster_archetypes:
            raise ValueError("content must have at least one monster archetype")
        if not isinstance(self.experience_rules, ExperienceRules):
            raise TypeError("experience_rules must be ExperienceRules")


@dataclass(frozen=True, slots=True)
class GameRules:
    """一局游戏使用的规则配置。"""

    max_turns: int
    seed: int
    monster_spawn: MonsterSpawnRules
    turn_recovery: TurnRecoveryRules = field(default_factory=TurnRecoveryRules)

    def __post_init__(self) -> None:
        _require_at_least("max_turns", self.max_turns, 1)
        if not isinstance(self.seed, int):
            raise TypeError("seed must be an int")
        if not isinstance(self.monster_spawn, MonsterSpawnRules):
            raise TypeError("monster_spawn must be MonsterSpawnRules")
        if not isinstance(self.turn_recovery, TurnRecoveryRules):
            raise TypeError("turn_recovery must be TurnRecoveryRules")


@dataclass(frozen=True, slots=True)
class GameDefinition:
    """创建和推进一局游戏所需的静态定义。"""

    content: GameContent
    rules: GameRules
    starting_gold: int = 0
    starting_materials: Mapping[str, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.content, GameContent):
            raise TypeError("content must be GameContent")
        if not isinstance(self.rules, GameRules):
            raise TypeError("rules must be GameRules")
        _require_at_least("starting_gold", self.starting_gold, 0)
        object.__setattr__(
            self,
            "starting_materials",
            _freeze_non_negative_mapping(self.starting_materials),
        )


@dataclass(frozen=True, slots=True)
class GameState:
    """一局游戏的动态状态。"""

    turn: int
    max_turns: int
    seed: int
    gold: int
    materials: Mapping[str, int]
    experience_pool: int
    adventurers: tuple[AdventurerState, ...]
    equipment_inventory: tuple[EquipmentInstance, ...]
    unlocked_upgrade_ids: frozenset[str]
    current_monsters: tuple[SpawnedMonster, ...]
    next_equipment_instance_number: int = 1

    def __post_init__(self) -> None:
        _require_at_least("turn", self.turn, 1)
        _require_at_least("max_turns", self.max_turns, 1)
        if not isinstance(self.seed, int):
            raise TypeError("seed must be an int")
        _require_at_least("gold", self.gold, 0)
        _require_at_least("experience_pool", self.experience_pool, 0)
        _require_at_least("next_equipment_instance_number", self.next_equipment_instance_number, 1)
        object.__setattr__(self, "materials", _freeze_non_negative_mapping(self.materials))
        object.__setattr__(self, "adventurers", tuple(self.adventurers))
        object.__setattr__(self, "equipment_inventory", tuple(self.equipment_inventory))
        object.__setattr__(self, "current_monsters", tuple(self.current_monsters))
        object.__setattr__(self, "unlocked_upgrade_ids", frozenset(self.unlocked_upgrade_ids))

        _validate_unique_ids("adventurer", (item.adventurer_id for item in self.adventurers))
        _validate_unique_ids("equipment instance", (item.instance_id for item in self.equipment_inventory))
        _validate_unique_ids("monster", (item.monster_id for item in self.current_monsters))


def _freeze_non_negative_mapping(values: Mapping[str, int]) -> Mapping[str, int]:
    frozen_values: dict[str, int] = {}
    for key, value in values.items():
        _require_non_empty("mapping key", key)
        _require_at_least(f"{key}", value, 0)
        frozen_values[key] = value
    return MappingProxyType(frozen_values)


def _validate_unique_ids(name: str, values: object) -> None:
    seen: set[str] = set()
    for value in values:
        if not isinstance(value, str):
            raise TypeError(f"{name} id must be a str")
        if value in seen:
            raise ValueError(f"duplicate {name} id: {value}")
        seen.add(value)


def _require_at_least(name: str, value: int, minimum: int) -> None:
    if not isinstance(value, int):
        raise TypeError(f"{name} must be an int")
    if value < minimum:
        raise ValueError(f"{name} must be >= {minimum}")


def _require_ratio(name: str, value: float) -> None:
    if not isinstance(value, int | float):
        raise TypeError(f"{name} must be a number")
    if value < 0 or value > 1:
        raise ValueError(f"{name} must be between 0 and 1")


def _require_non_empty(name: str, value: str) -> None:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a str")
    if not value:
        raise ValueError(f"{name} must not be empty")
