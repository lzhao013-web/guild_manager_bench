"""有限额的基础材料兑换规则。费用由 preset 配置，未配置时关闭。"""
from __future__ import annotations

from dataclasses import dataclass

from guild_manager_bench.game.crafting import MaterialCost


EXCHANGE_OUTPUTS = frozenset({"mithril_shard", "spider_silk", "beast_hide"})
EXCHANGE_INPUTS = frozenset({"iron_ore", "bone", "herb", "sharp_claw"})


@dataclass(frozen=True, slots=True)
class MaterialExchangeOffer:
    material_id: str
    gold_cost: int
    point_cost: int
    material_costs: tuple[MaterialCost, ...]

    def __post_init__(self) -> None:
        if self.material_id not in EXCHANGE_OUTPUTS:
            raise ValueError(f"不支持兑换该材料: {self.material_id}")
        _positive_int("gold_cost", self.gold_cost)
        _positive_int("point_cost", self.point_cost)
        object.__setattr__(self, "material_costs", tuple(self.material_costs))
        if not self.material_costs:
            raise ValueError("兑换必须消耗基础材料")
        seen: set[str] = set()
        for cost in self.material_costs:
            if not isinstance(cost, MaterialCost):
                raise TypeError("material_costs must contain MaterialCost")
            if cost.material_id not in EXCHANGE_INPUTS or cost.material_id in seen:
                raise ValueError(f"兑换输入材料不合法或重复: {cost.material_id}")
            _positive_int("material quantity", cost.quantity)
            seen.add(cost.material_id)


@dataclass(frozen=True, slots=True)
class MaterialExchangeRules:
    from_turn: int
    point_limit: int
    offers: tuple[MaterialExchangeOffer, ...]

    def __post_init__(self) -> None:
        _positive_int("from_turn", self.from_turn)
        _positive_int("point_limit", self.point_limit)
        object.__setattr__(self, "offers", tuple(self.offers))
        if not self.offers:
            raise ValueError("启用兑换必须配置至少一种材料")
        seen: set[str] = set()
        for offer in self.offers:
            if not isinstance(offer, MaterialExchangeOffer):
                raise TypeError("offers must contain MaterialExchangeOffer")
            if offer.material_id in seen:
                raise ValueError(f"重复的兑换材料: {offer.material_id}")
            if offer.point_cost > self.point_limit:
                raise ValueError("单份兑换额度不能超过整局额度")
            seen.add(offer.material_id)


def _positive_int(name: str, value: int) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ValueError(f"{name} 必须为正整数")
