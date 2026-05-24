from __future__ import annotations

from typing import Any, Protocol


class Operator(Protocol):
    """根据当前可见状态选择下一步动作。"""

    def choose_action(self, observation: dict[str, Any]) -> dict[str, Any]:
        """返回一个动作字典。"""

