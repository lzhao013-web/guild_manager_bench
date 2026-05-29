"""束搜索上界操作者：探索多条动作路径，选择最优序列。"""
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

    每回合在有限搜索宽度内探索不同的准备动作组合，
    选择启发式评估值最高的动作序列。
    """

    seed: int = 0
    beam_width: int = 20
    max_prep_per_turn: int = 8
    _action_queue: list[dict[str, Any]] = field(init=False, default_factory=list)

    def choose_action(self, observation: dict[str, Any]) -> dict[str, Any]:
        if not self._action_queue:
            self._action_queue = list(self._plan_turn(observation))
        return self._action_queue.pop(0)

    def _plan_turn(self, obs: dict[str, Any]) -> tuple[dict[str, Any], ...]:
        """通过束搜索规划当前回合的最优动作序列。"""

        shadow = ShadowState.from_observation(obs)

        # 初始节点：不做任何准备动作
        initial = _SearchNode(
            shadow=shadow,
            actions=(),
            value=self._heuristic_value(shadow, obs),
        )

        # 束搜索
        beam = [initial]
        completed: list[_SearchNode] = [initial]  # STOP 也是候选

        for _ in range(self.max_prep_per_turn):
            expanded = self._expand_beam(beam, obs)
            if not expanded:
                break

            # 分离继续搜索的节点和选择 STOP 的节点
            continuing: list[_SearchNode] = []
            for node in expanded:
                if node.actions and node.actions[-1].get("_stop"):
                    # 移除 _stop 标记，加入完成列表
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

            # 剪枝：保留 top beam_width
            continuing.sort(key=lambda n: n.value, reverse=True)
            beam = continuing[:self.beam_width]

        # 选择最优的完成节点
        completed.sort(key=lambda n: n.value, reverse=True)
        best = completed[0]

        # 添加狩猎分配
        hunts = self._optimal_hunts(obs, best.shadow)
        actions = list(best.actions) + [{"type": "end_turn", "hunts": hunts}]
        return tuple(actions)

    def _expand_beam(
        self,
        beam: list[_SearchNode],
        obs: dict[str, Any],
    ) -> list[_SearchNode]:
        """扩展当前束中的所有节点。"""

        expanded: list[_SearchNode] = []

        for node in beam:
            # 生成候选动作
            candidates = self._generate_candidates(node.shadow, obs)
            for action, new_shadow in candidates:
                value = self._heuristic_value(new_shadow, obs)
                expanded.append(_SearchNode(
                    shadow=new_shadow,
                    actions=node.actions + (action,),
                    value=value,
                ))

            # STOP 候选：选择不再做任何准备
            stop_action: dict[str, Any] = {"_stop": True}
            expanded.append(_SearchNode(
                shadow=node.shadow,
                actions=node.actions + (stop_action,),
                value=node.value,  # 值不变
            ))

        return expanded

    def _generate_candidates(
        self,
        shadow: ShadowState,
        obs: dict[str, Any],
    ) -> list[tuple[dict[str, Any], ShadowState]]:
        """为当前影子状态生成所有合法的下一动作。"""

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

        # 装备候选（只考虑未装备物品）
        unequipped = shadow.unequipped_items()
        # 合并 obs 和 shadow 中的冒险者
        all_adventurers = list(obs["adventurers"])
        for aid, adv in shadow.adventurers.items():
            if aid not in {a["adventurer_id"] for a in all_adventurers}:
                all_adventurers.append(adv)
        for item in unequipped:
            for adv in all_adventurers:
                aid = adv["adventurer_id"]
                if shadow.can_equip(aid, item["instance_id"]):
                    # 检查槽位冲突
                    slot = item["slot"]
                    adv_slots = shadow.equipped.get(aid, {})
                    if slot in ("main_hand", "off_hand") and "two_hand" in adv_slots:
                        continue
                    new_shadow = shadow.clone()
                    new_shadow.apply_equip(aid, item["instance_id"])
                    candidates.append((
                        {
                            "type": "equip",
                            "adventurer_id": aid,
                            "equipment_instance_id": item["instance_id"],
                        },
                        new_shadow,
                    ))
                    break  # 每件物品只配一个冒险者

        # 经验分配候选（分配给最有价值的冒险者）
        if shadow.experience_pool > 0:
            for adv in all_adventurers:
                if adv.get("resources", {}).get("current_hp", 0) <= 0:
                    continue
                next_level = adv.get("next_level", {})
                if next_level.get("max_level"):
                    continue
                remaining = next_level.get("remaining", 0)
                if remaining <= 0:
                    continue
                amount = min(remaining, shadow.experience_pool)
                new_shadow = shadow.clone()
                new_shadow.apply_xp_allocation(adv["adventurer_id"], amount)
                candidates.append((
                    {
                        "type": "allocate_experience",
                        "adventurer_id": adv["adventurer_id"],
                        "amount": amount,
                    },
                    new_shadow,
                ))

        return candidates

    @staticmethod
    def _heuristic_value(shadow: ShadowState, obs: dict[str, Any]) -> float:
        """启发式值函数：估计当前状态的终局 Arena 潜力。"""

        total_attack = 0.0
        total_defense = 0.0
        total_speed = 0.0
        total_hp = 0.0
        healers = 0
        party_size = shadow.party_size

        # 优先使用 shadow 中跟踪的冒险者（包括已招募的）
        all_advs = list(shadow.adventurers.values()) if shadow.adventurers else obs.get("adventurers", [])

        for adv in all_advs:
            stats = adv.get("effective_stats", adv.get("base_stats", {}))
            total_attack += stats.get("attack", 0)
            total_defense += stats.get("defense", 0)
            total_speed += stats.get("speed", 0)
            total_hp += stats.get("hp", 0)
            # 检查是否有治疗技能
            for skill in adv.get("skills", []):
                for effect in skill.get("effects", []):
                    if effect.get("type") in ("heal", "heal_percent"):
                        healers += 1
                        break

        avg_stats = (
            (total_attack + total_defense + total_speed + total_hp)
            / max(party_size, 1)
        )

        value = (
            10.0 * total_attack
            + 8.0 * total_defense
            + 5.0 * total_speed
            + 3.0 * total_hp
            + 15.0 * healers
            + 20.0 * party_size * avg_stats / 100.0
            + 100.0 * party_size  # 强烈奖励更多冒险者
            - 0.5 * shadow.gold  # 惩罚未消费的金币
            - 0.3 * shadow.experience_pool  # 惩罚未分配的经验
        )
        return value

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
