"""从实际规则生成材料兑换工具说明，不追加策略提示。"""
from __future__ import annotations

from typing import Any

from guild_manager_bench.game.material_exchange import MaterialExchangeRules
from guild_manager_bench.game.state import MATERIAL_NAMES


def material_exchange_schema(
    rules: MaterialExchangeRules, *, max_turns: int, final_preparation_only: bool,
) -> dict[str, Any]:
    final_notice = f"，含T{max_turns}最终准备" if final_preparation_only else ""
    lines = [
        f"T{rules.from_turn}起可在准备阶段兑换材料{final_notice}。"
        f"整局共享{rules.point_limit}点兑换额度，跨回合不恢复。",
        "", "每份成本：",
    ]
    for offer in rules.offers:
        materials = "＋".join(f"{cost.quantity}{MATERIAL_NAMES[cost.material_id]}" for cost in offer.material_costs)
        lines.append(
            f"- {offer.material_id}（{MATERIAL_NAMES[offer.material_id]}）："
            f"{offer.gold_cost}金币＋{materials}，消耗{offer.point_cost}点额度。"
        )
    lines.extend([
        "",
        "支持批量兑换同种材料，费用和额度按数量累加。制作折扣不适用。"
        "资源或额度不足时整次失败，不扣游戏资源或兑换额度。",
    ])
    return {
        "name": "exchange_materials",
        "description": "\n".join(lines),
        "parameters": {
            "type": "object",
            "required": ["material_id", "quantity"],
            "properties": {
                "material_id": {"type": "string", "enum": [offer.material_id for offer in rules.offers]},
                "quantity": {"type": "integer", "minimum": 1, "description": "兑换份数。"},
            },
            "additionalProperties": False,
        },
    }
