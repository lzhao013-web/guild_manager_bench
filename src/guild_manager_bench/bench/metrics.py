from __future__ import annotations

from guild_manager_bench.game.state import GameState


def total_effective_level(state: GameState) -> int:
    """返回队伍等级总和，作为临时运行统计。"""

    return sum(adventurer.level for adventurer in state.adventurers)

