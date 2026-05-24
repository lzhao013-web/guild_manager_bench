from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ActionRequest(BaseModel):
    """提交到会话的动作数据。"""

    type: str
    recipe_id: str | None = None
    upgrade_id: str | None = None
    adventurer_id: str | None = None
    equipment_instance_id: str | None = None
    slot: str | None = None
    amount: int | None = None
    hunts: list[dict[str, str]] = Field(default_factory=list)

    def to_payload(self) -> dict[str, Any]:
        """转成去掉空值的动作字典。"""

        data = self.model_dump() if hasattr(self, "model_dump") else self.dict()
        return {
            key: value
            for key, value in data.items()
            if value is not None
        }


class CreateSessionRequest(BaseModel):
    """创建会话的可选参数。"""

    session_id: str | None = None
