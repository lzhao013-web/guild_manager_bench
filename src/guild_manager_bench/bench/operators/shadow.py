"""操作者内部的影子状态跟踪工具。

用于在不调用游戏引擎的情况下，跟踪金币、材料等资源变化，
确保操作者只生成合法的动作序列。
"""
from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import dataclass, field
from math import ceil
from typing import Any

from guild_manager_bench.game.combat import Combatant, run_auto_battle
from guild_manager_bench.game.models import CombatResources, CombatStats
from guild_manager_bench.game.skills import Skill, SkillCondition, SkillEffect, StatusDefinition


@dataclass(slots=True)
class ShadowState:
    """操作者内部的资源跟踪。"""

    gold: int
    materials: dict[str, int]
    experience_pool: int
    party_size: int
    party_size_limit: int
    # adventurer_id -> {slot: instance_id}
    equipped: dict[str, dict[str, str]]
    unlocked_upgrade_ids: set[str]
    # 已经招募的候选 ID（防止重复招募）
    recruited_ids: set[str]
    # 下一个装备实例编号（用于合成）
    next_eq_num: int
    # 下一个冒险者编号（用于招募，引擎会分配 recruit_NNNN）
    next_adv_num: int
    # instance_id -> equipment dict from observation
    inventory: dict[str, dict[str, Any]]
    # adventurer_id -> adventurer dict from observation
    adventurers: dict[str, dict[str, Any]]

    @classmethod
    def from_observation(cls, obs: dict[str, Any]) -> ShadowState:
        """从观察数据创建影子状态。"""

        equipped: dict[str, dict[str, str]] = {}
        adventurers: dict[str, dict[str, Any]] = {}
        for adv in obs["adventurers"]:
            aid = adv["adventurer_id"]
            equipped[aid] = {
                item["slot"]: item["instance_id"]
                for item in adv["equipment"]
            }
            adventurers[aid] = adv

        inventory: dict[str, dict[str, Any]] = {}
        for item in obs["equipment_inventory"]:
            inventory[item["instance_id"]] = item

        unlocked = {
            upg["upgrade_id"]
            for upg in obs["global_upgrades"]
            if upg["unlocked"]
        }

        # 推算下一个装备编号
        max_num = 0
        for item in obs.get("equipment_inventory", []):
            iid = item.get("instance_id", "")
            if iid.startswith("eq_"):
                try:
                    max_num = max(max_num, int(iid[3:]))
                except ValueError:
                    pass

        # 推算下一个冒险者编号
        max_adv = 0
        for adv in obs.get("adventurers", []):
            aid = adv.get("adventurer_id", "")
            if aid.startswith("recruit_"):
                try:
                    max_adv = max(max_adv, int(aid[8:]))
                except ValueError:
                    pass

        return cls(
            gold=obs["gold"],
            materials=dict(obs["materials"]),
            experience_pool=obs["experience_pool"],
            party_size=obs["party_size"],
            party_size_limit=obs["party_size_limit"],
            equipped=equipped,
            unlocked_upgrade_ids=unlocked,
            recruited_ids=set(),
            next_eq_num=max_num + 1,
            next_adv_num=max_adv + 1,
            inventory=inventory,
            adventurers=adventurers,
        )

    def clone(self) -> ShadowState:
        """创建影子状态的深拷贝。"""

        return ShadowState(
            gold=self.gold,
            materials=dict(self.materials),
            experience_pool=self.experience_pool,
            party_size=self.party_size,
            party_size_limit=self.party_size_limit,
            equipped=deepcopy(self.equipped),
            unlocked_upgrade_ids=set(self.unlocked_upgrade_ids),
            recruited_ids=set(self.recruited_ids),
            next_eq_num=self.next_eq_num,
            next_adv_num=self.next_adv_num,
            inventory=deepcopy(self.inventory),
            adventurers=deepcopy(self.adventurers),
        )

    # ── 查询方法 ──────────────────────────────────────────

    def can_afford(self, gold_cost: int) -> bool:
        return self.gold >= gold_cost

    def has_materials(self, costs: dict[str, int]) -> bool:
        return all(
            self.materials.get(mat, 0) >= qty
            for mat, qty in costs.items()
        )

    def can_recruit(self) -> bool:
        return self.party_size < self.party_size_limit

    def is_recruited(self, candidate_id: str) -> bool:
        return candidate_id in self.recruited_ids

    def can_craft_recipe(self, recipe: dict[str, Any]) -> bool:
        return (
            self.can_afford(recipe["gold_cost"])
            and self.has_materials(recipe["material_costs"])
        )

    def can_purchase_upgrade(self, upgrade: dict[str, Any]) -> bool:
        if upgrade["upgrade_id"] in self.unlocked_upgrade_ids:
            return False
        if not self.can_afford(upgrade["gold_cost"]):
            return False
        return all(
            req in self.unlocked_upgrade_ids
            for req in upgrade["required_upgrade_ids"]
        )

    def unequipped_items(self) -> list[dict[str, Any]]:
        """返回当前未被装备的物品列表。"""

        all_equipped_ids: set[str] = set()
        for slots in self.equipped.values():
            all_equipped_ids.update(slots.values())
        return [
            item for item in self.inventory.values()
            if item["instance_id"] not in all_equipped_ids
        ]

    def can_equip(
        self,
        adventurer_id: str,
        instance_id: str,
    ) -> bool:
        """检查冒险者是否可以装备指定物品。"""

        item = self.inventory.get(instance_id)
        if item is None:
            return False
        adv = self.adventurers.get(adventurer_id)
        if adv is None:
            return False
        # 职业限制
        allowed = item.get("allowed_classes", [])
        if allowed and adv["template_id"] not in allowed:
            return False
        return True

    # ── 状态变更方法 ──────────────────────────────────────

    def apply_craft(
        self,
        recipe: dict[str, Any],
        instance_id: str,
    ) -> None:
        """记录合成动作的资源变化。"""

        self.gold -= recipe["gold_cost"]
        for mat, qty in recipe["material_costs"].items():
            self.materials[mat] = self.materials.get(mat, 0) - qty
        # 新物品进入背包
        self.inventory[instance_id] = {
            "instance_id": instance_id,
            "template_id": recipe["output_template_id"],
            "name": recipe["output_name"],
            "slot": recipe["output_slot"],
            "stats": dict(recipe["output_stats"]),
            "skills": list(recipe["output_skills"]),
            "allowed_classes": list(recipe["output_allowed_classes"]),
        }
        self.next_eq_num += 1

    def next_craft_instance_id(self) -> str:
        """返回下一个装备实例 ID 并递增计数器。"""

        iid = f"eq_{self.next_eq_num:04d}"
        return iid

    def apply_recruit(self, candidate: dict[str, Any]) -> None:
        """记录招募动作的资源变化。"""

        self.gold -= candidate["recruit_gold"]
        self.party_size += 1
        cid = candidate["candidate_id"]
        self.recruited_ids.add(cid)
        # 预测引擎分配的冒险者 ID
        adv_id = f"recruit_{self.next_adv_num:04d}"
        self.next_adv_num += 1
        self.adventurers[adv_id] = {
            "adventurer_id": adv_id,
            "name": candidate["name"],
            "template_id": candidate["template_id"],
            "base_stats": dict(candidate["base_stats"]),
            "effective_stats": dict(candidate["base_stats"]),
            "stat_growth_per_level": dict(candidate.get("stat_growth_per_level", {})),
            "resources": {
                "current_hp": candidate["base_stats"]["hp"],
                "current_mp": candidate["base_stats"]["mp"],
            },
            "skills": list(candidate["skills"]),
            "equipment": [],
            "level": 1,
            "experience": 0,
            "next_level": {},
            "level_skill_unlocks": list(candidate.get("level_skill_unlocks", [])),
        }
        self.equipped[adv_id] = {}

    def apply_upgrade(self, upgrade: dict[str, Any]) -> None:
        """记录购买升级的资源变化。"""

        self.gold -= upgrade["gold_cost"]
        self.unlocked_upgrade_ids.add(upgrade["upgrade_id"])
        if upgrade["party_size_bonus"] > 0:
            self.party_size_limit += upgrade["party_size_bonus"]

        # 全局升级的属性修正应用于所有冒险者
        upgrade_stats = upgrade.get("stats", {})
        for adv in self.adventurers.values():
            eff = dict(adv.get("effective_stats", adv.get("base_stats", {})))
            for key in ("hp", "mp", "attack", "defense", "speed", "recovery", "mp_recovery"):
                eff[key] = eff.get(key, 0) + upgrade_stats.get(key, 0)
            adv["effective_stats"] = eff

    # 共用的属性字段列表
    _STAT_KEYS = ("hp", "mp", "attack", "defense", "speed", "recovery", "mp_recovery")

    def apply_equip(
        self,
        adventurer_id: str,
        instance_id: str,
    ) -> None:
        """记录装备动作的状态变化。"""

        item = self.inventory[instance_id]
        slot = item["slot"]
        adv_slots = self.equipped[adventurer_id]

        # 收集被移除的装备实例 ID（冲突槽位 + 同槽位旧装备）
        removed_instance_ids: set[str] = set()

        # 处理双手冲突：装备 two_hand 时先卸下 main_hand 和 off_hand
        if slot == "two_hand":
            for hand_slot in ("main_hand", "off_hand"):
                if hand_slot in adv_slots:
                    removed_instance_ids.add(adv_slots[hand_slot])
                    del adv_slots[hand_slot]
        # 处理单手冲突：装备 main_hand 或 off_hand 时先卸下 two_hand
        elif slot in ("main_hand", "off_hand"):
            if "two_hand" in adv_slots:
                removed_instance_ids.add(adv_slots["two_hand"])
                del adv_slots["two_hand"]

        # 替换同槽位
        if slot in adv_slots:
            removed_instance_ids.add(adv_slots[slot])
        adv_slots[slot] = instance_id

        # 从 effective_stats 中减去被移除装备的属性，再加上新装备属性
        adv = self.adventurers.get(adventurer_id)
        if adv is not None:
            eff = dict(adv.get("effective_stats", adv.get("base_stats", {})))
            for removed_id in removed_instance_ids:
                removed_item = self.inventory.get(removed_id)
                if removed_item:
                    old_stats = removed_item.get("stats", {})
                    for key in self._STAT_KEYS:
                        eff[key] = eff.get(key, 0) - old_stats.get(key, 0)
            item_stats = item.get("stats", {})
            for key in self._STAT_KEYS:
                eff[key] = eff.get(key, 0) + item_stats.get(key, 0)
            adv["effective_stats"] = eff

    def apply_unequip(
        self,
        adventurer_id: str,
        slot: str,
    ) -> None:
        """记录卸下装备的状态变化。"""

        instance_id = self.equipped[adventurer_id].pop(slot, None)

        # 从 effective_stats 减去被卸下装备的属性
        if instance_id is not None:
            adv = self.adventurers.get(adventurer_id)
            if adv is not None:
                item = self.inventory.get(instance_id)
                if item:
                    eff = dict(adv.get("effective_stats", adv.get("base_stats", {})))
                    for key in self._STAT_KEYS:
                        eff[key] = eff.get(key, 0) - item.get("stats", {}).get(key, 0)
                    adv["effective_stats"] = eff

    def apply_xp_allocation(
        self,
        adventurer_id: str,
        amount: int,
        obs: dict[str, Any] | None = None,
    ) -> None:
        """记录经验分配的状态变化。"""

        self.experience_pool -= amount

        adv = self.adventurers.get(adventurer_id)
        if adv is None:
            return

        # 计算升级
        rules = obs.get("experience_rules", {}) if obs else {}
        max_level = rules.get("max_level", 12)
        base_req = rules.get("base_required_experience", 100)
        req_growth = rules.get("required_experience_growth", 35)

        old_level = adv.get("level", 1)
        experience = adv.get("experience", 0) + amount
        level = old_level

        while level < max_level:
            required = base_req + (level - 1) * req_growth
            if experience < required:
                break
            experience -= required
            level += 1

        if level >= max_level:
            level = max_level
            experience = 0

        adv["level"] = level
        adv["experience"] = experience

        # 应用升级带来的属性成长
        levels_gained = level - old_level
        if levels_gained > 0:
            growth = adv.get("stat_growth_per_level", {})
            eff = dict(adv.get("effective_stats", adv.get("base_stats", {})))
            for key in ("hp", "mp", "attack", "defense", "speed", "recovery", "mp_recovery"):
                eff[key] = eff.get(key, 0) + growth.get(key, 0) * levels_gained
            adv["effective_stats"] = eff


# ── 启发式工具函数 ────────────────────────────────────────


def estimate_matchup_score(
    attacker_stats: dict[str, int],
    defender_stats: dict[str, int],
) -> float:
    """启发式估算攻击方对阵防御方的战斗评分。

    返回值越大，表示攻击方优势越大。
    """

    dmg_per_round = max(1, attacker_stats["attack"] - defender_stats["defense"])
    counter_dmg = max(1, defender_stats["attack"] - attacker_stats["defense"])
    rounds_to_kill = ceil(defender_stats["hp"] / dmg_per_round)
    rounds_to_die = ceil(attacker_stats["hp"] / counter_dmg) if counter_dmg > 0 else 100
    return (rounds_to_die - rounds_to_kill) / max(rounds_to_die, 1)


# ── 战斗模拟函数 ────────────────────────────────────────


def _dict_to_combat_stats(d: dict[str, Any]) -> CombatStats:
    return CombatStats(
        hp=max(1, int(d.get("hp", 1))),
        mp=max(0, int(d.get("mp", 0))),
        attack=max(0, int(d.get("attack", 0))),
        defense=max(0, int(d.get("defense", 0))),
        speed=max(0, int(d.get("speed", 0))),
        recovery=max(0, int(d.get("recovery", 0))),
        mp_recovery=max(0, int(d.get("mp_recovery", 0))),
    )


def _dict_to_resources(stats: CombatStats, d: dict[str, Any] | None) -> CombatResources:
    if d is None:
        return CombatResources.full(stats)
    return CombatResources(
        current_hp=min(d.get("current_hp", stats.hp), stats.hp),
        current_mp=min(d.get("current_mp", stats.mp), stats.mp),
    )


def _dict_to_status(d: dict[str, Any]) -> StatusDefinition:
    effects = d.get("effects", [])
    return StatusDefinition(
        status_id=str(d.get("status_id") or d.get("name") or "status"),
        name=str(d.get("name") or d.get("status_id") or "status"),
        duration=max(1, d.get("duration", 1)),
        effects=tuple(
            _dict_to_skill_effect(e) for e in effects if isinstance(e, dict)
        ),
        polarity=d.get("polarity", "neutral"),
        stack_mode=d.get("stack_mode", "refresh"),
    )


def _dict_to_skill_effect(d: dict[str, Any]) -> SkillEffect:
    status = d.get("status")
    return SkillEffect(
        effect_type=d.get("type"),
        value=d.get("value", 0),
        stat=d.get("stat"),
        target=d.get("target") or "target",
        status=_dict_to_status(status) if isinstance(status, dict) else None,
    )


def _dict_to_condition(d: Any) -> SkillCondition:
    if not isinstance(d, dict):
        return SkillCondition(condition_type="always")
    children = d.get("conditions", [])
    return SkillCondition(
        condition_type=d.get("type", "always"),
        value=d.get("value"),
        conditions=tuple(_dict_to_condition(c) for c in children if isinstance(c, dict)),
    )


def _dict_to_skill(d: dict[str, Any]) -> Skill:
    effects = d.get("effects", [])
    return Skill(
        skill_id=str(d.get("skill_id") or d.get("name") or "skill"),
        name=str(d.get("name") or d.get("skill_id") or "skill"),
        kind=d.get("kind", "active"),
        condition=_dict_to_condition(d.get("condition")),
        effects=tuple(
            _dict_to_skill_effect(e) for e in effects if isinstance(e, dict)
        ),
        mp_cost=d.get("mp_cost", 0) or 0,
        priority=d.get("priority", 0) or 0,
        once_per_battle=bool(d.get("once_per_battle", False)),
        free=bool(d.get("free", False)),
    )


def simulate_battle_score(
    adv_stats: dict[str, Any],
    adv_resources: dict[str, Any] | None,
    adv_skills: list[Any],
    monster_stats: dict[str, Any],
    monster_skills: list[Any],
    *,
    _cache: dict[str, float] | None = None,
) -> float:
    """模拟 1v1 战斗并返回 0-100 分数。

    分数公式与 metrics.py 一致：70% 胜负 + 20% 伤害进度 + 10% 存活率。
    _cache: 可选缓存字典，跨调用复用以避免重复模拟。
    """
    cache_key: str | None = None
    if _cache is not None:
        cache_key = json.dumps(
            [adv_stats, adv_resources, adv_skills, monster_stats, monster_skills],
            sort_keys=True, separators=(",", ":"),
        )
        if cache_key in _cache:
            return _cache[cache_key]

    stats = _dict_to_combat_stats(adv_stats)
    resources = _dict_to_resources(stats, adv_resources)
    skills = tuple(_dict_to_skill(s) for s in adv_skills if isinstance(s, dict))

    mon_stats = _dict_to_combat_stats(monster_stats)
    mon_skills = tuple(_dict_to_skill(s) for s in monster_skills if isinstance(s, dict))

    result = run_auto_battle(
        Combatant(combatant_id="adv", stats=stats, resources=resources, skills=skills),
        Combatant(
            combatant_id="mon",
            stats=mon_stats,
            resources=CombatResources.full(mon_stats),
            skills=mon_skills,
        ),
    )
    enemy_progress = 1 - result.right_resources.current_hp / max(mon_stats.hp, 1)
    survival_margin = result.left_resources.current_hp / max(stats.hp, 1)
    outcome_score = {"left_win": 1.0, "draw": 0.4, "right_win": 0.0}[result.outcome]
    score = 70 * outcome_score + 20 * enemy_progress + 10 * survival_margin
    score = max(0.0, min(100.0, round(score, 2)))

    if _cache is not None and cache_key is not None:
        _cache[cache_key] = score
    return score


def simulate_party_value(
    adventurers: list[dict[str, Any]],
    monsters: list[dict[str, Any]],
    *,
    _cache: dict[str, float] | None = None,
) -> float:
    """通过战斗模拟 + 最优分配评估队伍对当前怪物的综合战斗力。

    对每个 (冒险者, 怪物) 对模拟战斗，然后用最优分配求最大总得分。
    """
    if not adventurers or not monsters:
        return 0.0

    matrix = [
        [
            simulate_battle_score(
                adv.get("effective_stats") or adv.get("base_stats", {}),
                adv.get("resources"),
                adv.get("skills", []),
                mon.get("stats", {}),
                mon.get("skills", []),
                _cache=_cache,
            )
            for mon in monsters
        ]
        for adv in adventurers
    ]

    pairs = best_assignment(matrix)
    return sum(matrix[a][m] for a, m in pairs)


def best_assignment(matrix: list[list[float]]) -> tuple[tuple[int, int], ...]:
    """给定评分矩阵，使用 bitmask DP 求解最优分配。

    matrix[i][j] 表示第 i 个冒险者对阵第 j 个怪物的评分。
    返回 ((adventurer_idx, monster_idx), ...) 最优配对。
    """

    if not matrix or not matrix[0]:
        return ()
    adventurer_count = len(matrix)
    monster_count = len(matrix[0])
    target_pairs = min(adventurer_count, monster_count)

    # 缓存搜索结果
    cache: dict[tuple[int, int, int], tuple[float, tuple[tuple[int, int], ...]]] = {}

    def search(
        adv_idx: int,
        used_monsters: int,
        assigned: int,
    ) -> tuple[float, tuple[tuple[int, int], ...]]:
        if assigned == target_pairs:
            return 0.0, ()
        if adv_idx >= adventurer_count:
            return float("-inf"), ()
        remaining = adventurer_count - adv_idx
        if assigned + remaining < target_pairs:
            return float("-inf"), ()

        key = (adv_idx, used_monsters, assigned)
        if key in cache:
            return cache[key]

        best_score = float("-inf")
        best_pairs: tuple[tuple[int, int], ...] = ()

        # 跳过当前冒险者
        if assigned + remaining > target_pairs:
            best_score, best_pairs = search(adv_idx + 1, used_monsters, assigned)

        # 分配当前冒险者到某个怪物
        for m_idx in range(monster_count):
            bit = 1 << m_idx
            if used_monsters & bit:
                continue
            rest_score, rest_pairs = search(
                adv_idx + 1,
                used_monsters | bit,
                assigned + 1,
            )
            candidate_score = matrix[adv_idx][m_idx] + rest_score
            if candidate_score > best_score:
                best_score = candidate_score
                best_pairs = ((adv_idx, m_idx),) + rest_pairs

        cache[key] = (best_score, best_pairs)
        return best_score, best_pairs

    return search(0, 0, 0)[1]
