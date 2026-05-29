"""随机全动作操作者：随机执行所有类型的准备动作。"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any

from guild_manager_bench.bench.operators.shadow import ShadowState


@dataclass(slots=True)
class RandomFullOperator:
    """随机选择所有准备动作，包括招募、合成、装备、升级和经验分配。

    与 RandomHuntOperator 不同，此操作者会在每回合随机执行准备动作，
    建立比纯随机狩猎更高的地板基线。
    """

    seed: int = 0
    _rng: random.Random = field(init=False)
    _action_queue: list[dict[str, Any]] = field(init=False, default_factory=list)

    def __post_init__(self) -> None:
        self._rng = random.Random(self.seed)

    def choose_action(self, observation: dict[str, Any]) -> dict[str, Any]:
        if not self._action_queue:
            self._action_queue = list(self._plan_turn(observation))
        return self._action_queue.pop(0)

    def _plan_turn(self, obs: dict[str, Any]) -> tuple[dict[str, Any], ...]:
        """规划当前回合的所有动作。"""

        shadow = ShadowState.from_observation(obs)
        actions: list[dict[str, Any]] = []

        # 收集所有候选准备动作
        candidates = self._collect_candidates(obs, shadow)
        self._rng.shuffle(candidates)

        # 逐一尝试候选动作，跳过因资源不足而失效的
        for gen_func in candidates:
            action = gen_func(shadow, obs)
            if action is not None:
                actions.append(action)

        # 随机狩猎配对
        hunts = self._random_hunts(obs)
        actions.append({"type": "end_turn", "hunts": hunts})

        return tuple(actions)

    def _collect_candidates(
        self,
        obs: dict[str, Any],
        shadow: ShadowState,
    ) -> list[Any]:
        """收集所有可能的候选动作生成函数。"""

        candidates: list[Any] = []

        # 招募候选
        for candidate in obs["recruit_candidates"]:
            if candidate["can_recruit"]:
                candidates.append(self._make_recruit(candidate))

        # 合成候选
        for recipe in obs["crafting_recipes"]:
            if recipe["can_craft"]:
                candidates.append(self._make_craft(recipe))

        # 购买升级候选
        for upgrade in obs["global_upgrades"]:
            if upgrade["can_purchase"]:
                candidates.append(self._make_upgrade(upgrade))

        # 装备候选
        for item in shadow.unequipped_items():
            for adv in obs["adventurers"]:
                if shadow.can_equip(adv["adventurer_id"], item["instance_id"]):
                    candidates.append(self._make_equip(adv, item))
                    break  # 每件物品只配一个冒险者

        # 经验分配
        if shadow.experience_pool > 0:
            alive_adventurers = [
                a for a in obs["adventurers"]
                if a["resources"]["current_hp"] > 0
            ]
            if alive_adventurers:
                candidates.append(self._make_xp(alive_adventurers))

        return candidates

    def _make_recruit(self, candidate: dict[str, Any]) -> Any:
        def gen(shadow: ShadowState, obs: dict[str, Any]) -> dict[str, Any] | None:
            cid = candidate["candidate_id"]
            if shadow.is_recruited(cid):
                return None
            if not shadow.can_recruit():
                return None
            if not shadow.can_afford(candidate["recruit_gold"]):
                return None
            shadow.apply_recruit(candidate)
            return {"type": "recruit", "candidate_id": cid}
        return gen

    def _make_craft(self, recipe: dict[str, Any]) -> Any:
        def gen(shadow: ShadowState, obs: dict[str, Any]) -> dict[str, Any] | None:
            if not shadow.can_craft_recipe(recipe):
                return None
            instance_id = shadow.next_craft_instance_id()
            shadow.apply_craft(recipe, instance_id)
            return {"type": "craft", "recipe_id": recipe["recipe_id"]}
        return gen

    def _make_upgrade(self, upgrade: dict[str, Any]) -> Any:
        def gen(shadow: ShadowState, obs: dict[str, Any]) -> dict[str, Any] | None:
            if not shadow.can_purchase_upgrade(upgrade):
                return None
            shadow.apply_upgrade(upgrade)
            return {"type": "purchase_upgrade", "upgrade_id": upgrade["upgrade_id"]}
        return gen

    def _make_equip(self, adv: dict[str, Any], item: dict[str, Any]) -> Any:
        def gen(shadow: ShadowState, obs: dict[str, Any]) -> dict[str, Any] | None:
            aid = adv["adventurer_id"]
            iid = item["instance_id"]
            if not shadow.can_equip(aid, iid):
                return None
            shadow.apply_equip(aid, iid)
            return {
                "type": "equip",
                "adventurer_id": aid,
                "equipment_instance_id": iid,
            }
        return gen

    def _make_xp(self, adventurers: list[dict[str, Any]]) -> Any:
        def gen(shadow: ShadowState, obs: dict[str, Any]) -> dict[str, Any] | None:
            if shadow.experience_pool <= 0:
                return None
            # 随机选择冒险者
            adv = self._rng.choice(adventurers)
            # 随机分配 0~经验池全部
            amount = self._rng.randint(0, shadow.experience_pool)
            if amount <= 0:
                return None
            shadow.apply_xp_allocation(adv["adventurer_id"], amount)
            return {
                "type": "allocate_experience",
                "adventurer_id": adv["adventurer_id"],
                "amount": amount,
            }
        return gen

    def _random_hunts(self, obs: dict[str, Any]) -> list[dict[str, str]]:
        """随机配对冒险者和怪物。"""

        adventurers = [
            item for item in obs["adventurers"]
            if item["resources"]["current_hp"] > 0
        ]
        monsters = list(obs["monsters"])
        self._rng.shuffle(adventurers)
        self._rng.shuffle(monsters)
        return [
            {
                "adventurer_id": adv["adventurer_id"],
                "monster_id": mon["monster_id"],
            }
            for adv, mon in zip(adventurers, monsters, strict=False)
        ]
