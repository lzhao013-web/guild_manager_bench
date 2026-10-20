"""手动刷新工具的声明。价格与随机结果由游戏规则决定。"""

_LOCKED_MONSTERS = {
    "type": "array",
    "items": {"type": "integer", "minimum": 1},
    "uniqueItems": True,
    "description": "本回合保留的普通怪物数字 id 列表（传 [] 刷新全部普通怪物；固定挑战自动保留，勿填入）。",
}

PREVIEW_REFRESH_SCHEMA = {
    "name": "preview_monster_refresh",
    "description": "只读预览刷新费用与下一批候选普通怪物，不扣费。实刷前候选固定（改锁定不重抽其他位置），返回的 refresh: 引用不可用于操作。",
    "parameters": {
        "type": "object",
        "required": ["locked_monster_ids"],
        "properties": {"locked_monster_ids": _LOCKED_MONSTERS},
        "additionalProperties": False,
    },
}

REFRESH_SCHEMA = {
    "name": "refresh_monsters",
    "description": "消耗金币刷新未锁定的普通怪物（固定挑战自动保留，同回合每次实刷后费用翻倍）。刷新后请使用返回的新怪物编号，被替换目标的旧编号失效。",
    "parameters": {
        "type": "object",
        "required": ["locked_monster_ids"],
        "properties": {"locked_monster_ids": _LOCKED_MONSTERS},
        "additionalProperties": False,
    },
}
