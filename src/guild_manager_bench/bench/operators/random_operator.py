from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class RandomHuntOperator:
    """随机选择可用冒险者和怪物交战。"""

    seed: int = 0
    _rng: random.Random = field(init=False)

    def __post_init__(self) -> None:
        self._rng = random.Random(self.seed)

    def choose_action(self, observation: dict[str, Any]) -> dict[str, Any]:
        """返回结束回合动作。"""

        adventurers = [
            item
            for item in observation["adventurers"]
            if item["resources"]["current_hp"] > 0
        ]
        monsters = list(observation["monsters"])
        self._rng.shuffle(adventurers)
        self._rng.shuffle(monsters)
        hunts = [
            {
                "adventurer_id": adventurer["adventurer_id"],
                "monster_id": monster["monster_id"],
            }
            for adventurer, monster in zip(adventurers, monsters, strict=False)
        ]
        return {"type": "end_turn", "hunts": hunts}

