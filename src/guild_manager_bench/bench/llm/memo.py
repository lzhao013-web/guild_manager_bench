"""局内多块备忘：批量更新、回合有效期与旧全文写入兼容。"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
from typing import Any, Mapping, cast

MAX_MEMO_CHARS = 4000
MEMO_GUIDANCE = (
    "备忘录只记录“以后用得上、查不到”的关键信息：跨回合计划、预留资源、待办验证、核心对比结论等。"
    "不要抄录当前面板状态、基础规则和随时能查到的列表。"
    "如果有必要，给时效信息标上过期回合，过期的和办完的及时删掉。"
    "4000字是上限，不必强求写满。"
)


@dataclass(frozen=True, slots=True)
class MemoBlock:
    key: str
    content: str
    created_turn: int
    updated_turn: int
    expires_turn: int | None = None


def _render_blocks(blocks: tuple[MemoBlock, ...]) -> str:
    return "\n\n".join(
        f"[{block.key}] 创建 T{block.created_turn}，更新 T{block.updated_turn}，"
        + (f"有效至 T{block.expires_turn}（含）" if block.expires_turn is not None else "无到期回合")
        + f"\n{block.content}"
        for block in blocks
    )


@dataclass(frozen=True, slots=True)
class Memo:
    """不可变检查点。blocks=None 表示旧版全文写入，正文保持原样。"""

    content: str = ""
    updated_turn: int | None = None
    blocks: tuple[MemoBlock, ...] | None = None

    @classmethod
    def from_blocks(cls, blocks: tuple[MemoBlock, ...], updated_turn: int | None) -> Memo:
        return cls(_render_blocks(blocks), updated_turn, blocks)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> Memo:
        blocks = value.get("blocks")
        if blocks is None:
            return cls(value["content"], value["updated_turn"])
        return cls.from_blocks(tuple(MemoBlock(**block) for block in blocks), value["updated_turn"])

    @property
    def used_chars(self) -> int:
        if self.blocks is None:
            return len(self.content)
        # 自动时间标签不占容量，模型填写的块名和正文均计入。
        return sum(len(block.key) + len(block.content) for block in self.blocks)

    def at_turn(self, turn: int) -> Memo:
        if self.blocks is None:
            return self
        active = tuple(block for block in self.blocks if block.expires_turn is None or block.expires_turn >= turn)
        return self if active == self.blocks else Memo.from_blocks(active, self.updated_turn)

    def to_dict(self) -> dict[str, Any]:
        value: dict[str, Any] = {
            "content": self.content, "updated_turn": self.updated_turn,
            "used_chars": self.used_chars, "max_chars": MAX_MEMO_CHARS,
        }
        if self.blocks is not None:
            value["blocks"] = [asdict(block) for block in self.blocks]
        return value


@dataclass(slots=True)
class MemoStore:
    """当前分支的有效备忘。历史快照独立保存在回放与事件中。"""

    memo: Memo = field(default_factory=Memo)

    def write(self, content: Any, *, turn: int) -> dict[str, Any]:
        """兼容旧调用的整份覆盖，空字符串清空全部块。"""
        if not isinstance(content, str):
            raise ValueError("备忘内容必须是字符串")
        if len(content) > MAX_MEMO_CHARS:
            raise ValueError(f"备忘内容超出长度上限：当前 {len(content)} 字符，最多 {MAX_MEMO_CHARS} 字符")
        self.memo = Memo(content=content.strip(), updated_turn=turn)
        return self.memo.to_dict()

    def apply(self, arguments: Mapping[str, Any], *, turn: int) -> dict[str, Any]:
        if "operations" not in arguments:
            if set(arguments) - {"content"}:
                raise ValueError("备忘内容写入仅支持 content，批量操作请使用 operations")
            return self.write(arguments.get("content"), turn=turn)
        if set(arguments) != {"operations"}:
            raise ValueError("operations 不得与 content 或其他字段混用")
        operations = arguments["operations"]
        if not isinstance(operations, list) or not operations:
            raise ValueError("operations 必须是非空操作列表")

        active = self.memo.at_turn(turn)
        if active.blocks is None:
            # 非空持久正文来自成功写入，必有写入回合。原文作为可见块保留。
            legacy_turn = cast(int, active.updated_turn)
            blocks = {} if not active.content else {"全文": MemoBlock(
                "全文", active.content, legacy_turn, legacy_turn,
            )}
        else:
            blocks = {block.key: block for block in active.blocks}
        seen: set[str] = set()
        for operation in operations:
            if not isinstance(operation, Mapping):
                raise ValueError("每项备忘操作必须是对象")
            op, key = operation.get("op"), operation.get("key")
            if op not in ("add", "update", "delete"):
                raise ValueError("op 必须是 add、update 或 delete")
            if not isinstance(key, str) or not key.strip():
                raise ValueError("key 必须是非空块名")
            if key in seen:
                raise ValueError(f"同一批操作不能重复指定块：{key}")
            seen.add(key)
            allowed = {"op", "key"} if op == "delete" else {"op", "key", "content", "expires_turn"}
            if set(operation) - allowed:
                raise ValueError(f"{op} 包含不支持的字段")
            if op == "add" and key in blocks:
                raise ValueError(f"备忘块已存在：{key}，请使用 update")
            if op != "add" and key not in blocks:
                raise ValueError(f"备忘块不存在：{key}")
            if op == "delete":
                del blocks[key]
                continue
            if op == "add" and "content" not in operation:
                raise ValueError("add 必须提供 content")
            if op == "update" and not ({"content", "expires_turn"} & set(operation)):
                raise ValueError("update 至少修改 content 或 expires_turn")
            if "content" in operation:
                content = operation["content"]
                if not isinstance(content, str) or not content.strip():
                    raise ValueError("备忘内容必须是非空字符串，移除块请使用 delete")
                content = content.strip()
            else:
                content = blocks[key].content
            if "expires_turn" in operation:
                expiry = operation["expires_turn"]
                if expiry is not None and (type(expiry) is not int or expiry < turn):
                    raise ValueError(f"expires_turn 必须为 null 或不小于当前回合 T{turn} 的整数")
            else:
                expiry = None if op == "add" else blocks[key].expires_turn
            blocks[key] = MemoBlock(key, content, turn, turn, expiry) if op == "add" else replace(
                blocks[key], content=content, updated_turn=turn, expires_turn=expiry,
            )
        candidate = Memo.from_blocks(tuple(blocks.values()), turn)
        if candidate.used_chars > MAX_MEMO_CHARS:
            raise ValueError(
                f"备忘内容超出长度上限：最终 {candidate.used_chars} 字符，最多 {MAX_MEMO_CHARS} 字符"
            )
        self.memo = candidate
        return candidate.to_dict()

    def expire(self, turn: int) -> None:
        self.memo = self.memo.at_turn(turn)

    def snapshot(self) -> Memo:
        return self.memo

    def restore(self, memo: Memo) -> None:
        self.memo = memo


def _operation_schema(op: str) -> dict[str, Any]:
    properties: dict[str, Any] = {
        "op": {"type": "string", "enum": [op]},
        "key": {"type": "string", "minLength": 1, "description": "稳定的语义块名，见当前备忘。add 不得重名，update/delete 必须存在。"},
    }
    if op != "delete":
        properties.update({
            "content": {"type": "string", "minLength": 1, "maxLength": MAX_MEMO_CHARS,
                        "description": "替换本块正文，未改块无需重写。"},
            "expires_turn": {"type": ["integer", "null"], "minimum": 1,
                             "description": "最后有效回合，不能早于当前回合。null 取消期限，修改时省略则保留原期限。"},
        })
    schema: dict[str, Any] = {
        "type": "object", "additionalProperties": False, "properties": properties,
        "required": ["op", "key", "content"] if op == "add" else ["op", "key"],
    }
    if op == "update":
        schema["anyOf"] = [{"required": ["content"]}, {"required": ["expires_turn"]}]
    return schema


WRITE_MEMO_SCHEMA: dict[str, Any] = {
    "name": "write_memo",
    "description": (
        "各回合独立对话，备忘录在本局跨回合持续保留，未修改且未到期的块不变。"
        "仅在内容需要更新时于 end_turn 前调用。operations 可批量 add、update、delete 命名块，"
        "一批只扣1次普通调用，整批验证后提交。所有有效块的块名和正文合计最多4000字符。"
        "创建与更新时间由系统记录。expires_turn 为最后有效回合（含），省略表示新增时无期限、修改时保留原期限，"
        "null 表示取消期限。到期后自动停止提供，不影响历史。返回 used_chars 已用字符数。"
        + MEMO_GUIDANCE
    ),
    "parameters": {
        "type": "object", "required": ["operations"], "additionalProperties": False,
        "properties": {
            "operations": {
                "type": "array", "minItems": 1,
                "description": "同一批每个 key 只能出现一次。任一操作非法或最终超容时整批失败。",
                "items": {"anyOf": [_operation_schema(op) for op in ("add", "update", "delete")]},
            },
        },
    },
}
