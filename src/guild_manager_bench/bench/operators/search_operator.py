"""束搜索上界操作者：探索多条动作路径，选择最优序列。

混合策略：beam search 做战略决策（招募、升级、合成），
greedy 做战术执行（装备、经验分配）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from guild_manager_bench.bench.operators.shadow import (
    ShadowState,
    best_assignment,
    estimate_matchup_score,
)


@dataclass(slots=True)
class _SearchNode:
    """束搜索的搜索节点。"""

    shadow: ShadowState
    actions: tuple[dict[str, Any], ...]
    value: float


@dataclass(slots=True)
class SearchOperator:
    """基于束搜索的上界操作者：探索多条动作路径，选择最优序列。

    混合策略：
    - Beam search 负责战略决策（招募、升级、合成）
    - Greedy 负责战术执行（装备分配、经验分配）
    - 最优分配算法负责狩猎配对
    """

    seed: int = 0
    beam_width: int = 30
    max_prep_per_turn: int = 10
    _action_queue: list[dict[str, Any]] = field(init=False, default_factory=list)

    def choose_action(self, observation: dict[str, Any]) -> dict[str, Any]:
        if not self._action_queue:
            self._action_queue = list(self._plan_turn(observation))
        return self._action_queue.pop(0)

    def _plan_turn(self, obs: dict[str, Any]) -> tuple[dict[str, Any], ...]:
        """通过混合策略规划当前回合的最优动作序列。"""

        shadow = ShadowState.from_observation(obs)

        # 阶段 1：Beam search 做战略决策（招募、升级、合成）
        beam_actions, shadow = self._beam_search(obs, shadow)

        # 阶段 2：Greedy 装备分配
        equip_actions = self._greedy_equip(obs, shadow)

        # 阶段 3：Greedy 经验分配
        xp_actions = self._greedy_xp(obs, shadow)

        # 阶段 4：最优狩猎配对
        hunts = self._optimal_hunts(obs, shadow)

        actions = beam_actions + equip_actions + xp_actions
        actions.append({"type": "end_turn", "hunts": hunts})
        return tuple(actions)

    # ── 阶段 1：Beam Search ───────────────────────────────

    def _beam_search(
        self,
        obs: dict[str, Any],
        shadow: ShadowState,
    ) -> tuple[list[dict[str, Any]], ShadowState]:
        """Beam search 规划招募/升级/合成动作。"""

        initial = _SearchNode(
            shadow=shadow,
            actions=(),
            value=self._heuristic_value(shadow, obs),
        )

        beam = [initial]
        completed: list[_SearchNode] = [initial]

        for _ in range(self.max_prep_per_turn):
            expanded = self._expand_beam(beam, obs)
            if not expanded:
                break

            continuing: list[_SearchNode] = []
            for node in expanded:
                if node.actions and node.actions[-1].get("_stop"):
                    clean_actions = node.actions[:-1]
                    completed.append(_SearchNode(
                        shadow=node.shadow,
                        actions=clean_actions,
                        value=node.value,
                    ))
                else:
                    continuing.append(node)

            if not continuing:
                break

            continuing.sort(key=lambda n: n.value, reverse=True)
            beam = continuing[:self.beam_width]

        completed.sort(key=lambda n: n.value, reverse=True)
        best = completed[0]
        return list(best.actions), best.shadow

    def _expand_beam(
        self,
        beam: list[_SearchNode],
        obs: dict[str, Any],
    ) -> list[_SearchNode]:
        """扩展当前束中的所有节点。"""

        expanded: list[_SearchNode] = []

        for node in beam:
            candidates = self._generate_candidates(node.shadow, obs)
            for action, new_shadow in candidates:
                value = self._heuristic_value(new_shadow, obs)
                expanded.append(_SearchNode(
                    shadow=new_shadow,
                    actions=node.actions + (action,),
                    value=value,
                ))

            # STOP 候选
            stop_action: dict[str, Any] = {"_stop": True}
            expanded.append(_SearchNode(
                shadow=node.shadow,
                actions=node.actions + (stop_action,),
                value=node.value,
            ))

        return expanded

    def _generate_candidates(
        self,
        shadow: ShadowState,
        obs: dict[str, Any],
    ) -> list[tuple[dict[str, Any], ShadowState]]:
        """生成战略候选动作：招募、升级、合成。"""

        candidates: list[tuple[dict[str, Any], ShadowState]] = []

        # 合成候选
        for recipe in obs["crafting_recipes"]:
            if shadow.can_craft_recipe(recipe):
                new_shadow = shadow.clone()
                instance_id = new_shadow.next_craft_instance_id()
                new_shadow.apply_craft(recipe, instance_id)
                candidates.append((
                    {"type": "craft", "recipe_id": recipe["recipe_id"]},
                    new_shadow,
                ))

        # 升级候选
        for upgrade in obs["global_upgrades"]:
            if shadow.can_purchase_upgrade(upgrade):
                new_shadow = shadow.clone()
                new_shadow.apply_upgrade(upgrade)
                candidates.append((
                    {"type": "purchase_upgrade", "upgrade_id": upgrade["upgrade_id"]},
                    new_shadow,
                ))

        # 招募候选
        if shadow.can_recruit():
            for candidate in obs["recruit_candidates"]:
                if shadow.is_recruited(candidate["candidate_id"]):
                    continue
                if candidate["can_recruit"] and shadow.can_afford(candidate["recruit_gold"]):
                    new_shadow = shadow.clone()
                    new_shadow.apply_recruit(candidate)
                    candidates.append((
                        {"type": "recruit", "candidate_id": candidate["candidate_id"]},
                        new_shadow,
                    ))

        return candidates

    # ── 阶段 2：Greedy 装备 ──────────────────────────────

    @staticmethod
    def _greedy_equip(
        obs: dict[str, Any],
        shadow: ShadowState,
    ) -> list[dict[str, Any]]:
        """贪心装备：每步选属性提升最大的物品-冒险者配对。"""

        actions: list[dict[str, Any]] = []

        for _ in range(10):
            best_score = 0.0
            best_aid = None
            best_iid = None

            unequipped = shadow.unequipped_items()
            all_advs = list(shadow.adventurers.values())

            for item in unequipped:
                iid = item["instance_id"]
                slot = item["slot"]
                item_stats = item.get("stats", {})
                item_value = (
                    item_stats.get("attack", 0) * 2.0
                    + item_stats.get("defense", 0) * 1.5
                    + item_stats.get("speed", 0) * 1.8
                    + item_stats.get("hp", 0) * 0.3
                )

                for adv in all_advs:
                    aid = adv["adventurer_id"]
                    if not shadow.can_equip(aid, iid):
                        continue
                    adv_slots = shadow.equipped.get(aid, {})
                    if slot in ("main_hand", "off_hand") and "two_hand" in adv_slots:
                        continue

                    # 减去被替换物品的属性
                    current_iid = adv_slots.get(slot)
                    replacement_cost = 0.0
                    if current_iid and current_iid in shadow.inventory:
                        old_stats = shadow.inventory[current_iid].get("stats", {})
                        replacement_cost = (
                            old_stats.get("attack", 0) * 2.0
                            + old_stats.get("defense", 0) * 1.5
                            + old_stats.get("speed", 0) * 1.8
                            + old_stats.get("hp", 0) * 0.3
                        )

                    score = item_value - replacement_cost
                    if score > best_score:
                        best_score = score
                        best_aid = aid
                        best_iid = iid

            if best_aid is None:
                break

            shadow.apply_equip(best_aid, best_iid)
            actions.append({
                "type": "equip",
                "adventurer_id": best_aid,
                "equipment_instance_id": best_iid,
            })

        return actions

    # ── 阶段 3：Greedy 经验 ──────────────────────────────

    @staticmethod
    def _greedy_xp(
        obs: dict[str, Any],
        shadow: ShadowState,
    ) -> list[dict[str, Any]]:
        """贪心经验分配：优先分配给接近升级的冒险者。"""

        actions: list[dict[str, Any]] = []
        rules = obs.get("experience_rules", {})
        max_level = rules.get("max_level", 12)
        base_req = rules.get("base_required_experience", 100)
        req_growth = rules.get("required_experience_growth", 35)

        while shadow.experience_pool > 0:
            best_score = -1.0
            best_aid = None
            best_amount = 0

            for adv in shadow.adventurers.values():
                if adv.get("resources", {}).get("current_hp", 0) <= 0:
                    continue
                level = adv.get("level", 1)
                if level >= max_level:
                    continue
                required = base_req + (level - 1) * req_growth
                experience = adv.get("experience", 0)
                remaining = required - experience
                if remaining <= 0:
                    continue
                if shadow.experience_pool < remaining:
                    continue

                # 评分：接近升级 + 升级属性收益
                proximity = 1.0 - (remaining / max(required, 1))
                growth = adv.get("stat_growth_per_level", {})
                growth_value = (
                    growth.get("attack", 0) * 2.0
                    + growth.get("defense", 0) * 1.5
                    + growth.get("speed", 0) * 1.8
                    + growth.get("hp", 0) * 0.3
                )
                score = proximity * 50.0 + growth_value

                if score > best_score:
                    best_score = score
                    best_aid = adv["adventurer_id"]
                    best_amount = remaining

            if best_aid is None:
                break

            shadow.apply_xp_allocation(best_aid, best_amount, obs)
            actions.append({
                "type": "allocate_experience",
                "adventurer_id": best_aid,
                "amount": best_amount,
            })

        return actions

    # ── 阶段 4：狩猎分配 ─────────────────────────────────

    def _optimal_hunts(
        self,
        obs: dict[str, Any],
        shadow: ShadowState,
    ) -> list[dict[str, str]]:
        """使用启发式评分 + 最优分配算法选择狩猎配对。"""

        adventurers = [
            a for a in obs["adventurers"]
            if a["resources"]["current_hp"] > 0
        ]
        obs_ids = {a["adventurer_id"] for a in adventurers}
        for aid, adv in shadow.adventurers.items():
            if aid not in obs_ids and adv.get("resources", {}).get("current_hp", 0) > 0:
                adventurers.append(adv)

        monsters = list(obs["monsters"])
        if not adventurers or not monsters:
            return []

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

        pairs = best_assignment(matrix)
        return [
            {
                "adventurer_id": adventurers[ai]["adventurer_id"],
                "monster_id": monsters[mi]["monster_id"],
            }
            for ai, mi in pairs
        ]

    # ── 启发式评估 ────────────────────────────────────────

    @staticmethod
    def _heuristic_value(shadow: ShadowState, obs: dict[str, Any]) -> float:
        """启发式值函数：基于战斗属性评估当前状态的竞技潜力。"""

        total_attack = 0.0
        total_defense = 0.0
        total_speed = 0.0
        total_hp = 0.0
        total_recovery = 0.0
        total_mp_recovery = 0.0
        healers = 0

        all_advs = list(shadow.adventurers.values()) if shadow.adventurers else obs.get("adventurers", [])

        for adv in all_advs:
            stats = adv.get("effective_stats", adv.get("base_stats", {}))
            total_attack += stats.get("attack", 0)
            total_defense += stats.get("defense", 0)
            total_speed += stats.get("speed", 0)
            total_hp += stats.get("hp", 0)
            total_recovery += stats.get("recovery", 0)
            total_mp_recovery += stats.get("mp_recovery", 0)
            for skill in adv.get("skills", []):
                for effect in skill.get("effects", []):
                    if effect.get("type") in ("heal", "heal_percent"):
                        healers += 1
                        break

        # 属性权重对齐 GreedyOperator 已验证的评分
        stat_power = (
            total_attack * 2.0
            + total_speed * 1.8
            + total_defense * 1.5
            + total_recovery * 0.5
            + total_mp_recovery * 0.5
            + total_hp * 0.3
        )

        # 队伍人数奖励
        party_bonus = 10.0 * shadow.party_size
        # 人口上限价值：按已有人均战力估算，确保无属性的扩容升级也能在 beam 中存活
        avg_member_power = stat_power / max(shadow.party_size, 1)
        capacity_per_slot = max(avg_member_power * 0.8, 80.0)
        capacity_bonus = capacity_per_slot * shadow.party_size_limit

        # 剩余资源的轻微正价值
        resource_value = 0.1 * shadow.gold + 0.2 * shadow.experience_pool

        # 治疗者奖励
        healer_bonus = 8.0 * healers

        # 未装备物品的潜在属性价值（半权重，桥接 craft→equip 步骤）
        item_potential = 0.0
        for item in shadow.unequipped_items():
            item_stats = item.get("stats", {})
            item_potential += (
                item_stats.get("attack", 0) * 1.0
                + item_stats.get("defense", 0) * 0.75
                + item_stats.get("speed", 0) * 0.9
                + item_stats.get("hp", 0) * 0.15
            )

        return stat_power + party_bonus + capacity_bonus + healer_bonus + resource_value + item_potential
