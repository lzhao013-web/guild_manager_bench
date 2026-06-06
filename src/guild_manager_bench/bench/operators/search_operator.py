"""搜索操作者：枚举组合 + 战斗模拟选最优方案。

阶段 1：招募 — 优先填满队伍
阶段 2：投资 — 枚举所有可行的（合成+升级）子集，预览终态，选最优
阶段 3：经验 — 战斗模拟判断给谁升级收益最大
阶段 4：狩猎 — 战斗模拟 + 最优分配
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from guild_manager_bench.bench.operators.shadow import (
    ShadowState,
    best_assignment,
    simulate_battle_score,
    simulate_party_value,
)


@dataclass(slots=True)
class _Plan:
    shadow: ShadowState
    actions: list[dict[str, Any]]
    score: float


@dataclass(slots=True)
class SearchOperator:
    """基于战斗模拟的搜索操作者。"""

    seed: int = 0
    beam_width: int = 8
    max_prep_per_turn: int = 10
    _action_queue: list[dict[str, Any]] = field(init=False, default_factory=list)
    _battle_cache: dict[str, float] = field(init=False, default_factory=dict)

    def choose_action(self, observation: dict[str, Any]) -> dict[str, Any]:
        if not self._action_queue:
            self._action_queue = list(self._plan_turn(observation))
        return self._action_queue.pop(0)

    def _plan_turn(self, obs: dict[str, Any]) -> tuple[dict[str, Any], ...]:
        shadow = ShadowState.from_observation(obs)
        actions: list[dict[str, Any]] = []
        monsters = obs.get("monsters", [])

        # 每回合刷新战斗缓存（怪物变了）
        self._battle_cache.clear()

        self._phase_recruit(obs, shadow, actions, monsters)
        self._phase_invest(obs, shadow, actions, monsters)
        self._phase_xp(obs, shadow, actions, monsters)

        hunts = self._optimal_hunts(obs, shadow)
        actions.append({"type": "end_turn", "hunts": hunts})
        return tuple(actions)

    # ── 阶段 1：招募 ─────────────────────────────────────

    def _phase_recruit(
        self,
        obs: dict[str, Any],
        shadow: ShadowState,
        actions: list[dict[str, Any]],
        monsters: list[dict[str, Any]],
    ) -> None:
        # 先把空位招满
        for _ in range(shadow.party_size_limit):
            if not shadow.can_recruit():
                break

            baseline = _party_score(shadow, monsters, cache=self._battle_cache)
            best_delta = -1.0
            best_candidate = None

            for candidate in obs["recruit_candidates"]:
                if shadow.is_recruited(candidate["candidate_id"]):
                    continue
                if not candidate["can_recruit"]:
                    continue
                if not shadow.can_afford(candidate["recruit_gold"]):
                    continue
                if not shadow.can_recruit():
                    break

                trial = shadow.clone()
                trial.apply_recruit(candidate)
                delta = _party_score(trial, monsters, cache=self._battle_cache) - baseline
                unlock_count = sum(
                    len(u.get("skills", []))
                    for u in candidate.get("level_skill_unlocks", [])
                )
                delta += unlock_count * 2.0

                if delta > best_delta:
                    best_delta = delta
                    best_candidate = candidate

            if best_candidate is None or best_delta <= 0:
                break

            shadow.apply_recruit(best_candidate)
            actions.append({
                "type": "recruit",
                "candidate_id": best_candidate["candidate_id"],
            })

        # 队伍满员时，尝试汰换：淘汰最弱队员 + 招募更强候选人
        self._try_swaps(obs, shadow, actions, monsters)

    def _try_swaps(
        self,
        obs: dict[str, Any],
        shadow: ShadowState,
        actions: list[dict[str, Any]],
        monsters: list[dict[str, Any]],
    ) -> None:
        """终局阶段汰换：淘汰最弱队员 + 招募更强候选人。

        仅在游戏后半段（回合 >= max_turns / 2）才考虑汰换，
        避免早期频繁换人浪费资源。
        汰换时会：解散 → 返还经验 → 招募新人 → 用返还经验升级新人 → 重装装备。
        """

        # 终局门槛：至少过半才考虑
        max_turns = obs.get("max_turns", 35)
        turn = obs.get("turn", 0)
        if turn < max_turns // 2:
            return

        rules = obs.get("experience_rules", {})
        max_level = rules.get("max_level", 12)
        base_req = rules.get("base_required_experience", 100)
        req_growth = rules.get("required_experience_growth", 35)

        for _ in range(shadow.party_size_limit):
            if shadow.can_recruit():
                break

            baseline = _party_score(shadow, monsters, cache=self._battle_cache)
            best_delta = 0.0
            best_dismiss_id = None
            best_candidate = None
            best_xp_actions: list[dict[str, Any]] = []
            best_equip_actions: list[dict[str, Any]] = []

            for candidate in obs["recruit_candidates"]:
                if shadow.is_recruited(candidate["candidate_id"]):
                    continue
                if not shadow.can_afford(candidate["recruit_gold"]):
                    continue

                for adv_id, adv in list(shadow.adventurers.items()):
                    trial = shadow.clone()

                    level = adv.get("level", 1)
                    experience = adv.get("experience", 0)
                    refunded_xp = _total_invested_xp(level, experience, max_level, base_req, req_growth)

                    _dismiss_from_shadow(trial, adv_id)
                    trial.experience_pool += refunded_xp
                    trial.apply_recruit(candidate)
                    new_adv_id = max(trial.adventurers.keys()) if trial.adventurers else None

                    xp_actions: list[dict[str, Any]] = []
                    if new_adv_id and trial.experience_pool > 0:
                        for _ in range(max_level):
                            new_adv = trial.adventurers.get(new_adv_id)
                            if not new_adv:
                                break
                            lv = new_adv.get("level", 1)
                            if lv >= max_level:
                                break
                            remaining = base_req + (lv - 1) * req_growth - new_adv.get("experience", 0)
                            if remaining <= 0 or trial.experience_pool < remaining:
                                break
                            trial.apply_xp_allocation(new_adv_id, remaining, obs)
                            xp_actions.append({
                                "type": "allocate_experience",
                                "adventurer_id": new_adv_id,
                                "amount": remaining,
                            })

                    equip_actions = _auto_equip_fast(trial)

                    delta = _party_score(trial, monsters, cache=self._battle_cache) - baseline

                    if delta > best_delta:
                        best_delta = delta
                        best_dismiss_id = adv_id
                        best_candidate = candidate
                        best_xp_actions = xp_actions
                        best_equip_actions = equip_actions

            if best_dismiss_id is None or best_candidate is None:
                break

            # 执行汰换
            dismissed = shadow.adventurers[best_dismiss_id]
            level = dismissed.get("level", 1)
            experience = dismissed.get("experience", 0)
            refunded_xp = _total_invested_xp(level, experience, max_level, base_req, req_growth)

            _dismiss_from_shadow(shadow, best_dismiss_id)
            shadow.experience_pool += refunded_xp
            shadow.apply_recruit(best_candidate)

            # 立刻给新人分配返还的经验
            new_adv_id = max(shadow.adventurers.keys()) if shadow.adventurers else None
            if new_adv_id:
                for xp_act in best_xp_actions:
                    shadow.apply_xp_allocation(xp_act["adventurer_id"], xp_act["amount"], obs)

            # 立刻装备
            for eq_act in best_equip_actions:
                shadow.apply_equip(eq_act["adventurer_id"], eq_act["equipment_instance_id"])

            actions.append({"type": "dismiss", "adventurer_id": best_dismiss_id})
            actions.append({
                "type": "recruit",
                "candidate_id": best_candidate["candidate_id"],
            })
            actions.extend(best_xp_actions)
            actions.extend(best_equip_actions)

    # ── 阶段 2：投资（枚举组合）─────────────────────────

    def _phase_invest(
        self,
        obs: dict[str, Any],
        shadow: ShadowState,
        actions: list[dict[str, Any]],
        monsters: list[dict[str, Any]],
    ) -> None:
        available = self._collect_actions(shadow, obs)

        if available:
            best = self._enumerate_plans(shadow, available, monsters)
            if best.score > _party_score(shadow, monsters, cache=self._battle_cache):
                _apply_shadow(shadow, best.shadow)
                actions.extend(best.actions)
                return

        # 没有战略动作或组合没有正收益，贪心装备已有物品
        self._greedy_equip_existing(shadow, actions, monsters)

    @staticmethod
    def _collect_actions(
        shadow: ShadowState,
        obs: dict[str, Any],
    ) -> list[tuple[str, dict[str, Any]]]:
        result: list[tuple[str, dict[str, Any]]] = []
        for recipe in obs["crafting_recipes"]:
            if shadow.can_craft_recipe(recipe):
                result.append(("craft", recipe))
        for upgrade in obs["global_upgrades"]:
            if shadow.can_purchase_upgrade(upgrade):
                result.append(("upgrade", upgrade))
        return result

    def _enumerate_plans(
        self,
        shadow: ShadowState,
        available: list[tuple[str, dict[str, Any]]],
        monsters: list[dict[str, Any]],
    ) -> _Plan:
        """组合枚举：只按 start_idx 递增遍历，避免重复排列。"""

        n = len(available)
        max_depth = min(self.max_prep_per_turn, n, 4)
        baseline = _party_score(shadow, monsters, cache=self._battle_cache)
        best = _Plan(shadow=shadow, actions=[], score=baseline)
        stack: list[tuple[str, dict[str, Any]]] = []

        def dfs(start: int, cur: ShadowState) -> None:
            # 评估当前子集：快速自动装备 + 战斗模拟
            snap = cur.clone()
            equip_acts = _auto_equip_fast(snap)
            score = _party_score(snap, monsters, cache=self._battle_cache)

            if score > best.score:
                best.score = score
                best.shadow = snap
                best.actions = [
                    _action_dict(k, d) for k, d in stack
                ] + equip_acts

            if len(stack) >= max_depth:
                return

            for i in range(start, n):
                kind, data = available[i]
                trial = cur.clone()
                ok = False
                if kind == "craft" and trial.can_craft_recipe(data):
                    trial.apply_craft(data, trial.next_craft_instance_id())
                    ok = True
                elif kind == "upgrade" and trial.can_purchase_upgrade(data):
                    trial.apply_upgrade(data)
                    ok = True
                if not ok:
                    continue
                stack.append((kind, data))
                dfs(i + 1, trial)
                stack.pop()

        dfs(0, shadow)
        return best

    def _greedy_equip_existing(
        self,
        shadow: ShadowState,
        actions: list[dict[str, Any]],
        monsters: list[dict[str, Any]],
    ) -> None:
        baseline = _party_score(shadow, monsters, cache=self._battle_cache)
        for _ in range(10):
            best_delta = 0.0
            best_aid = None
            best_iid = None

            for item in shadow.unequipped_items():
                iid = item["instance_id"]
                slot = item["slot"]
                for adv in shadow.adventurers.values():
                    aid = adv["adventurer_id"]
                    if not shadow.can_equip(aid, iid):
                        continue
                    adv_slots = shadow.equipped.get(aid, {})
                    if slot in ("main_hand", "off_hand") and "two_hand" in adv_slots:
                        continue
                    if slot == "two_hand" and ("main_hand" in adv_slots or "off_hand" in adv_slots):
                        continue

                    trial = shadow.clone()
                    trial.apply_equip(aid, iid)
                    delta = _party_score(trial, monsters, cache=self._battle_cache) - baseline

                    if delta > best_delta:
                        best_delta = delta
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
            baseline += best_delta

    # ── 阶段 3：经验分配 ────────────────────────────────

    def _phase_xp(
        self,
        obs: dict[str, Any],
        shadow: ShadowState,
        actions: list[dict[str, Any]],
        monsters: list[dict[str, Any]],
    ) -> None:
        rules = obs.get("experience_rules", {})
        max_level = rules.get("max_level", 12)
        base_req = rules.get("base_required_experience", 100)
        req_growth = rules.get("required_experience_growth", 35)

        while shadow.experience_pool > 0:
            baseline = _party_score(shadow, monsters, cache=self._battle_cache)
            best_delta = 0.0
            best_aid = None
            best_amount = 0

            for adv in shadow.adventurers.values():
                if adv.get("resources", {}).get("current_hp", 0) <= 0:
                    continue
                level = adv.get("level", 1)
                if level >= max_level:
                    continue
                experience = adv.get("experience", 0)
                required = base_req + (level - 1) * req_growth
                remaining = required - experience
                if remaining <= 0:
                    continue

                amount = min(remaining, shadow.experience_pool)
                trial = shadow.clone()
                trial.apply_xp_allocation(adv["adventurer_id"], amount, obs)
                delta = _party_score(trial, monsters, cache=self._battle_cache) - baseline

                if delta > best_delta:
                    best_delta = delta
                    best_aid = adv["adventurer_id"]
                    best_amount = amount

            if best_aid is None:
                best_prox = -1.0
                for adv in shadow.adventurers.values():
                    if adv.get("resources", {}).get("current_hp", 0) <= 0:
                        continue
                    level = adv.get("level", 1)
                    if level >= max_level:
                        continue
                    experience = adv.get("experience", 0)
                    required = base_req + (level - 1) * req_growth
                    remaining = required - experience
                    if remaining <= 0:
                        continue
                    prox = 1.0 - (remaining / max(required, 1))
                    if prox > best_prox:
                        best_prox = prox
                        best_aid = adv["adventurer_id"]
                        best_amount = min(remaining, shadow.experience_pool)

            if best_aid is None:
                break

            shadow.apply_xp_allocation(best_aid, best_amount, obs)
            actions.append({
                "type": "allocate_experience",
                "adventurer_id": best_aid,
                "amount": best_amount,
            })

    # ── 阶段 4：狩猎分配 ────────────────────────────────

    def _optimal_hunts(
        self,
        obs: dict[str, Any],
        shadow: ShadowState,
    ) -> list[dict[str, str]]:
        adventurers = [
            adv for adv in shadow.adventurers.values()
            if adv.get("resources", {}).get("current_hp", 0) > 0
        ]
        monsters = list(obs["monsters"])
        if not adventurers or not monsters:
            return []

        matrix = [
            [
                simulate_battle_score(
                    adv.get("effective_stats") or adv.get("base_stats", {}),
                    adv.get("resources"),
                    adv.get("skills", []),
                    mon.get("stats", {}),
                    mon.get("skills", []),
                    _cache=self._battle_cache,
                )
                for mon in monsters
            ]
            for adv in adventurers
        ]

        pairs = best_assignment(matrix)
        return [
            {"adventurer_id": adventurers[ai]["adventurer_id"],
             "monster_id": monsters[mi]["monster_id"]}
            for ai, mi in pairs
        ]


# ── 模块级辅助 ──────────────────────────────────────


def _party_score(shadow: ShadowState, monsters: list[dict[str, Any]], *, cache: dict[str, float] | None = None) -> float:
    advs = list(shadow.adventurers.values()) if shadow.adventurers else []
    return simulate_party_value(advs, monsters, _cache=cache)


def _auto_equip_fast(shadow: ShadowState) -> list[dict[str, Any]]:
    """快速贪心装备：用属性权重（非战斗模拟）选配对。"""
    actions: list[dict[str, Any]] = []
    for _ in range(10):
        best_delta = 0.0
        best_aid = None
        best_iid = None

        for item in shadow.unequipped_items():
            iid = item["instance_id"]
            slot = item["slot"]
            s = item.get("stats", {})
            val = s.get("attack", 0) * 2.0 + s.get("speed", 0) * 1.5 + s.get("defense", 0) * 1.0 + s.get("hp", 0) * 0.2
            for adv in shadow.adventurers.values():
                aid = adv["adventurer_id"]
                if not shadow.can_equip(aid, iid):
                    continue
                adv_slots = shadow.equipped.get(aid, {})
                if slot in ("main_hand", "off_hand") and "two_hand" in adv_slots:
                    continue
                if slot == "two_hand" and ("main_hand" in adv_slots or "off_hand" in adv_slots):
                    continue
                cur_iid = adv_slots.get(slot)
                replace = 0.0
                if cur_iid and cur_iid in shadow.inventory:
                    o = shadow.inventory[cur_iid].get("stats", {})
                    replace = o.get("attack", 0) * 2.0 + o.get("speed", 0) * 1.5 + o.get("defense", 0) * 1.0 + o.get("hp", 0) * 0.2
                delta = val - replace
                if delta > best_delta:
                    best_delta = delta
                    best_aid = aid
                    best_iid = iid

        if best_aid is None:
            break
        shadow.apply_equip(best_aid, best_iid)
        actions.append({"type": "equip", "adventurer_id": best_aid, "equipment_instance_id": best_iid})
    return actions


def _action_dict(kind: str, data: dict[str, Any]) -> dict[str, Any]:
    if kind == "craft":
        return {"type": "craft", "recipe_id": data["recipe_id"]}
    return {"type": "purchase_upgrade", "upgrade_id": data["upgrade_id"]}


def _apply_shadow(target: ShadowState, source: ShadowState) -> None:
    target.gold = source.gold
    target.materials = source.materials
    target.experience_pool = source.experience_pool
    target.party_size = source.party_size
    target.party_size_limit = source.party_size_limit
    target.equipped = source.equipped
    target.unlocked_upgrade_ids = source.unlocked_upgrade_ids
    target.recruited_ids = source.recruited_ids
    target.next_eq_num = source.next_eq_num
    target.next_adv_num = source.next_adv_num
    target.inventory = source.inventory
    target.adventurers = source.adventurers


def _dismiss_from_shadow(shadow: ShadowState, adventurer_id: str) -> None:
    """从 shadow 中移除冒险者，归还装备属性，装备留在 inventory 中可重用。"""

    adv = shadow.adventurers.get(adventurer_id)
    if adv is None:
        return

    # 把装备属性从 effective_stats 减回去
    for slot_name, item_iid in shadow.equipped.get(adventurer_id, {}).items():
        item = shadow.inventory.get(item_iid)
        if item:
            item_stats = item.get("stats", {})
            eff = dict(adv.get("effective_stats", adv.get("base_stats", {})))
            for key in ShadowState._STAT_KEYS:
                eff[key] = eff.get(key, 0) - item_stats.get(key, 0)
            adv["effective_stats"] = eff

    del shadow.equipped[adventurer_id]
    del shadow.adventurers[adventurer_id]
    shadow.party_size -= 1


def _total_invested_xp(
    level: int,
    experience: int,
    max_level: int,
    base_req: int,
    req_growth: int,
) -> int:
    """计算已投入的总经验（同 progression.py 的 total_invested_experience）。"""
    if level >= max_level:
        return 0
    total = experience
    n = level - 1
    total += n * base_req
    total += req_growth * (n - 1) * n // 2
    return max(0, total)
