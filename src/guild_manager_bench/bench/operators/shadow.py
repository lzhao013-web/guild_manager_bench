"""操作者内部的影子状态跟踪工具。

用于在不调用游戏引擎的情况下，跟踪金币、材料等资源变化，
确保操作者只生成合法的动作序列。
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from math import ceil
from typing import Any


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
            "resources": {
                "current_hp": candidate["base_stats"]["hp"],
                "current_mp": candidate["base_stats"]["mp"],
            },
            "skills": list(candidate["skills"]),
            "equipment": [],
            "level": 1,
            "experience": 0,
            "next_level": {},
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
