from __future__ import annotations

from guild_manager_bench.bench.operators.base import Operator
from guild_manager_bench.game.engine import is_finished
from guild_manager_bench.game.state import GameDefinition
from guild_manager_bench.runtime.action_codec import decode_end_turn_action, decode_preparation_action
from guild_manager_bench.runtime.session import GameSession


def run_operator(
    definition: GameDefinition,
    operator: Operator,
    *,
    max_steps: int = 1_000,
) -> GameSession:
    """用自动操作者推进一局游戏。"""

    session = GameSession(definition)
    steps = 0
    while session.state is not None and not is_finished(session.state):
        if steps >= max_steps:
            break
        payload = operator.choose_action(session.observation())
        if payload.get("type") == "end_turn":
            session.end_turn(decode_end_turn_action(payload))
        else:
            session.apply_preparation(decode_preparation_action(payload))
        steps += 1
    return session

