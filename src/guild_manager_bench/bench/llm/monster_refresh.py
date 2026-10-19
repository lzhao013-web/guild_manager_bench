"""手动刷新工具的声明。价格与随机结果由游戏规则决定。"""

REFRESH_SCHEMA = {
    "name": "refresh_monsters",
    "description": (
        "花金币更换本回合的讨伐目标：锁定的普通怪物保留，其余普通怪物重新随机生成，固定挑战不变。"
        "可先预览再决定是否付费。同一回合内，重复预览不重抽，改锁定名单不重抽其他位置，付费刷新后才能看下一批。"
        "预览编号不能用于操作。实际刷新后使用返回的编号，被替换目标的旧编号失效。"
    ),
    "parameters": {
        "type": "object",
        "required": ["locked_monster_ids", "preview"],
        "properties": {
            "locked_monster_ids": {
                "type": "array",
                "items": {"type": "integer", "minimum": 1},
                "uniqueItems": True,
                "description": "要保留的普通怪物数字编号。[]表示全部刷新，固定挑战自动保留。",
            },
            "preview": {
                "type": "boolean",
                "description": "true：仅预览，不扣费、不改变状态。false：付费刷新。",
            },
        },
        "additionalProperties": False,
    },
}
