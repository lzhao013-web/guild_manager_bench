"""贪心启发式操作者：按照优先级系统做出局部最优决策。"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from guild_manager_bench.bench.operators.shadow import (
    ShadowState,
    best_assignment,
    estimate_matchup_score,
)


@dataclass(slots=True)
class GreedyOperator:
    """贪心启发式操作者：每回合按优先级执行局部最优决策。

    决策优先级：招募 > 升级 > 合成 > 装备 > 分配经验 > 狩猎分配。
    确定性操作者：给定相同观察数据，总是做出相同选择。
    """

    seed: int = 0  # 仅作为后备 tiebreaker
    _action_queue: list[dict[str, Any]] = field(init=False, default_factory=list)

    def choose_action(self, observation: dict[str, Any]) -> dict[str, Any]:
        if not self._action_queue:
            self._action_queue = list(self._plan_turn(observation))
        return self._action_queue.pop(0)

    def _plan_turn(self, obs: dict[str, Any]) -> tuple[dict[str, Any], ...]:
        """规划当前回合的所有动作。"""

        shadow = ShadowState.from_observation(obs)
        actions: list[dict[str, Any]] = []

        # 阶段 1：招募
        self._phase_recruit(obs, shadow, actions)
        # 阶段 2：升级
        self._phase_upgrade(obs, shadow, actions)
        # 阶段 3：合成
        self._phase_craft(obs, shadow, actions)
        # 阶段 4：装备
        self._phase_equip(obs, shadow, actions)
        # 阶段 5：分配经验
        self._phase_xp(obs, shadow, actions)
        # 阶段 6：狩猎分配
        hunts = self._optimal_hunts(obs)
        actions.append({"type": "end_turn", "hunts": hunts})

        return tuple(actions)

    # ── 阶段 1：招募 ──────────────────────────────────────

    def _phase_recruit(
        self,
        obs: dict[str, Any],
        shadow: ShadowState,
        actions: list[dict[str, Any]],
    ) -> None:
        """贪心招募：优先招募综合评分最高的候选项。"""

        for _ in range(3):  # 每回合最多尝试招募 3 次
            if not shadow.can_recruit():
                break
            candidates = self._scored_recruits(obs, shadow)
            if not candidates:
                break
            # 选择最高分的
            candidates.sort(key=lambda x: x[0], reverse=True)
            _, candidate = candidates[0]
            shadow.apply_recruit(candidate)
            actions.append({
                "type": "recruit",
                "candidate_id": candidate["candidate_id"],
            })

    def _scored_recruits(
        self,
        obs: dict[str, Any],
        shadow: ShadowState,
    ) -> list[tuple[float, dict[str, Any]]]:
        """为每个可招募候选项计算评分。"""

        results = []
        for candidate in obs["recruit_candidates"]:
            if shadow.is_recruited(candidate["candidate_id"]):
                continue
            if not candidate["can_recruit"]:
                continue
            if not shadow.can_afford(candidate["recruit_gold"]):
                continue
            if not shadow.can_recruit():
                continue
            score = self._score_recruit(candidate)
            results.append((score, candidate))
        return results

    @staticmethod
    def _score_recruit(candidate: dict[str, Any]) -> float:
        """评估招募候选项的综合价值。"""

        stats = candidate["base_stats"]
        current_total = (
            stats["hp"] * 0.3
            + stats["attack"] * 2.0
            + stats["defense"] * 1.5
            + stats["speed"] * 1.8
            + stats["recovery"] * 0.5
        )
        growth = candidate.get("stat_growth_per_level", {})
        growth_total = (
            growth.get("attack", 0) * 2.0
            + growth.get("defense", 0) * 1.5
            + growth.get("speed", 0) * 1.8
            + growth.get("hp", 0) * 0.3
        )
        # 技能数量加分
        skill_bonus = len(candidate.get("skills", [])) * 5.0
        # 等级解锁技能加分
        unlock_bonus = sum(
            len(u.get("skills", []))
            for u in candidate.get("level_skill_unlocks", [])
        ) * 3.0

        composite = 0.6 * current_total + 0.4 * growth_total + skill_bonus + unlock_bonus
        return composite / max(candidate["recruit_gold"], 1)

    # ── 阶段 2：升级 ──────────────────────────────────────

    def _phase_upgrade(
        self,
        obs: dict[str, Any],
        shadow: ShadowState,
        actions: list[dict[str, Any]],
    ) -> None:
        """贪心购买升级：优先购买综合评分最高的升级。"""

        for _ in range(3):
            candidates = self._scored_upgrades(obs, shadow)
            if not candidates:
                break
            candidates.sort(key=lambda x: x[0], reverse=True)
            _, upgrade = candidates[0]
            shadow.apply_upgrade(upgrade)
            actions.append({
                "type": "purchase_upgrade",
                "upgrade_id": upgrade["upgrade_id"],
            })

    def _scored_upgrades(
        self,
        obs: dict[str, Any],
        shadow: ShadowState,
    ) -> list[tuple[float, dict[str, Any]]]:
        results = []
        for upgrade in obs["global_upgrades"]:
            if not shadow.can_purchase_upgrade(upgrade):
                continue
            score = self._score_upgrade(upgrade, shadow.party_size)
            results.append((score, upgrade))
        return results

    @staticmethod
    def _score_upgrade(upgrade: dict[str, Any], party_size: int) -> float:
        """评估升级的综合价值。"""

        stats = upgrade["stats"]
        stat_sum = (
            stats.get("hp", 0) * 0.3
            + stats.get("attack", 0) * 2.0
            + stats.get("defense", 0) * 1.5
            + stats.get("speed", 0) * 1.8
            + stats.get("recovery", 0) * 0.5 + stats.get("mp_recovery", 0) * 0.5
        )
        # 升级效果应用于全队
        party_wide = stat_sum * party_size
        # 技能价值
        skill_value = len(upgrade.get("skills", [])) * 10.0
        # 组队人数奖励
        size_bonus = upgrade.get("party_size_bonus", 0) * 30.0
        return party_wide + skill_value + size_bonus

    # ── 阶段 3：合成 ──────────────────────────────────────

    def _phase_craft(
        self,
        obs: dict[str, Any],
        shadow: ShadowState,
        actions: list[dict[str, Any]],
    ) -> None:
        """贪心合成：优先合成综合评分最高的装备。"""

        for _ in range(3):
            candidates = self._scored_recipes(obs, shadow)
            if not candidates:
                break
            candidates.sort(key=lambda x: x[0], reverse=True)
            _, recipe = candidates[0]
            instance_id = shadow.next_craft_instance_id()
            shadow.apply_craft(recipe, instance_id)
            actions.append({
                "type": "craft",
                "recipe_id": recipe["recipe_id"],
            })

    def _scored_recipes(
        self,
        obs: dict[str, Any],
        shadow: ShadowState,
    ) -> list[tuple[float, dict[str, Any]]]:
        results = []
        for recipe in obs["crafting_recipes"]:
            if not shadow.can_craft_recipe(recipe):
                continue
            # 检查是否有冒险者能装备
            if not self._has_compatible_adventurer(obs, recipe, shadow):
                continue
            score = self._score_recipe(recipe)
            results.append((score, recipe))
        return results

    @staticmethod
    def _has_compatible_adventurer(
        obs: dict[str, Any],
        recipe: dict[str, Any],
        shadow: ShadowState,
    ) -> bool:
        """检查是否有冒险者能装备合成产出的物品。"""

        allowed = recipe.get("output_allowed_classes", [])
        for adv in obs["adventurers"]:
            if allowed and adv["template_id"] not in allowed:
                continue
            # 检查槽位是否可用（没有被双手武器阻塞）
            slot = recipe["output_slot"]
            adv_slots = shadow.equipped.get(adv["adventurer_id"], {})
            if slot in ("main_hand", "off_hand") and "two_hand" in adv_slots:
                continue
            return True
        return not allowed  # 没有职业限制则总是可以

    @staticmethod
    def _score_recipe(recipe: dict[str, Any]) -> float:
        """评估合成配方的综合价值。"""

        stats = recipe["output_stats"]
        stat_sum = (
            stats.get("attack", 0) * 2.0
            + stats.get("defense", 0) * 1.5
            + stats.get("speed", 0) * 1.8
            + stats.get("hp", 0) * 0.3
            + stats.get("mp", 0) * 0.2
        )
        skill_value = len(recipe.get("output_skills", [])) * 8.0
        cost_penalty = recipe["gold_cost"] * 0.5
        return stat_sum + skill_value - cost_penalty

    # ── 阶段 4：装备 ──────────────────────────────────────

    def _phase_equip(
        self,
        obs: dict[str, Any],
        shadow: ShadowState,
        actions: list[dict[str, Any]],
    ) -> None:
        """贪心装备：优先装备属性提升最大的配对。"""

        for _ in range(10):  # 最多处理 10 件装备
            candidates = self._scored_equips(obs, shadow)
            if not candidates:
                break
            candidates.sort(key=lambda x: x[0], reverse=True)
            _, adventurer_id, instance_id = candidates[0]
            shadow.apply_equip(adventurer_id, instance_id)
            actions.append({
                "type": "equip",
                "adventurer_id": adventurer_id,
                "equipment_instance_id": instance_id,
            })

    def _scored_equips(
        self,
        obs: dict[str, Any],
        shadow: ShadowState,
    ) -> list[tuple[float, str, str]]:
        """返回 (score, adventurer_id, instance_id) 列表。"""

        results = []
        unequipped = shadow.unequipped_items()
        for item in unequipped:
            iid = item["instance_id"]
            slot = item["slot"]
            for adv in obs["adventurers"]:
                aid = adv["adventurer_id"]
                if not shadow.can_equip(aid, iid):
                    continue
                # 检查槽位冲突
                adv_slots = shadow.equipped.get(aid, {})
                if slot in ("main_hand", "off_hand") and "two_hand" in adv_slots:
                    continue
                if slot == "two_hand" and ("main_hand" in adv_slots or "off_hand" in adv_slots):
                    # 允许，但需要考虑失去手部装备的代价
                    pass
                # 如果该槽位已有装备，计算替换提升
                current_in_slot = adv_slots.get(slot)
                improvement = self._equip_improvement(item, adv, current_in_slot, shadow)
                results.append((improvement, aid, iid))
        return results

    @staticmethod
    def _equip_improvement(
        item: dict[str, Any],
        adv: dict[str, Any],
        current_instance_id: str | None,
        shadow: ShadowState,
    ) -> float:
        """计算装备此物品对冒险者的属性提升。"""

        item_stats = item.get("stats", {})
        improvement = (
            item_stats.get("attack", 0) * 2.0
            + item_stats.get("defense", 0) * 1.5
            + item_stats.get("speed", 0) * 1.8
            + item_stats.get("hp", 0) * 0.3
        )
        # 减去被替换物品的属性
        if current_instance_id and current_instance_id in shadow.inventory:
            old = shadow.inventory[current_instance_id]
            old_stats = old.get("stats", {})
            improvement -= (
                old_stats.get("attack", 0) * 2.0
                + old_stats.get("defense", 0) * 1.5
                + old_stats.get("speed", 0) * 1.8
                + old_stats.get("hp", 0) * 0.3
            )
        # 附带技能加分
        improvement += len(item.get("skills", [])) * 5.0
        return improvement

    # ── 阶段 5：分配经验 ──────────────────────────────────

    def _phase_xp(
        self,
        obs: dict[str, Any],
        shadow: ShadowState,
        actions: list[dict[str, Any]],
    ) -> None:
        """贪心分配经验：优先分配给接近升级且有技能解锁的冒险者。"""

        while shadow.experience_pool > 0:
            candidates = self._scored_xp_targets(obs, shadow)
            if not candidates:
                break
            candidates.sort(key=lambda x: x[0], reverse=True)
            _, adventurer_id, amount = candidates[0]
            if amount <= 0:
                break
            actual = min(amount, shadow.experience_pool)
            shadow.apply_xp_allocation(adventurer_id, actual, obs)
            actions.append({
                "type": "allocate_experience",
                "adventurer_id": adventurer_id,
                "amount": actual,
            })

    def _scored_xp_targets(
        self,
        obs: dict[str, Any],
        shadow: ShadowState,
    ) -> list[tuple[float, str, int]]:
        """返回 (score, adventurer_id, amount) 列表。"""

        results = []
        for adv in obs["adventurers"]:
            if adv["resources"]["current_hp"] <= 0:
                continue
            next_level = adv.get("next_level", {})
            if next_level.get("max_level"):
                continue
            remaining = next_level.get("remaining", 0)
            if remaining <= 0:
                continue

            # 评分：接近升级的程度 + 升级后技能解锁价值
            proximity = 1.0 - (remaining / max(next_level.get("required", 1), 1))
            unlock_value = 0.0
            for unlock in next_level.get("preview_level_skill_unlocks", []):
                unlock_value += len(unlock.get("skills", [])) * 10.0

            # 属性提升价值
            preview_stats = next_level.get("preview_stats", {})
            current_stats = adv.get("effective_stats", {})
            stat_gain = (
                (preview_stats.get("attack", 0) - current_stats.get("attack", 0)) * 2.0
                + (preview_stats.get("defense", 0) - current_stats.get("defense", 0)) * 1.5
                + (preview_stats.get("speed", 0) - current_stats.get("speed", 0)) * 1.8
                + (preview_stats.get("hp", 0) - current_stats.get("hp", 0)) * 0.3
            )

            score = proximity * 50.0 + unlock_value + stat_gain
            amount = min(remaining, shadow.experience_pool)
            results.append((score, adv["adventurer_id"], amount))
        return results

    # ── 阶段 6：狩猎分配 ──────────────────────────────────

    def _optimal_hunts(self, obs: dict[str, Any]) -> list[dict[str, str]]:
        """使用启发式评分 + 最优分配算法选择狩猎配对。"""

        adventurers = [
            a for a in obs["adventurers"]
            if a["resources"]["current_hp"] > 0
        ]
        monsters = list(obs["monsters"])
        if not adventurers or not monsters:
            return []

        # 构建评分矩阵
        matrix = [
            [
                estimate_matchup_score(
                    adv["effective_stats"],
                    mon["stats"],
                )
                for mon in monsters
            ]
            for adv in adventurers
        ]

        # 求解最优分配
        pairs = best_assignment(matrix)
        return [
            {
                "adventurer_id": adventurers[ai]["adventurer_id"],
                "monster_id": monsters[mi]["monster_id"],
            }
            for ai, mi in pairs
        ]

    # ── 辅助方法 ──────────────────────────────────────────

