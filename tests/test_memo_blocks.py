"""多块备忘的模型可见契约、有效期、原子更新与恢复。"""
from copy import deepcopy

import pytest

from guild_manager_bench.bench.llm.harness import Memo, MemoStore, TurnToolHarness
from guild_manager_bench.bench.llm.memo import MEMO_GUIDANCE
from guild_manager_bench.bench.llm.prompts import build_system_prompt, build_turn_prompt
from guild_manager_bench.bench.llm.runner import _format_tool_result_for_model, _memo_store_from_replay
from guild_manager_bench.bench.llm.tools import GuildManagerTools


def add(key="计划", content="预留资源", **extra):
    return {"op": "add", "key": key, "content": content, **extra}


def apply(store, *operations, turn=1):
    return store.apply({"operations": list(operations)}, turn=turn)


def test_add_update_delete_preserve_other_blocks_and_record_game_turns():
    store = MemoStore()
    first = apply(store, add(expires_turn=3), add("结论", "带条件的判断"))
    assert first["used_chars"] == len("计划预留资源结论带条件的判断")
    assert first["max_chars"] == 4000
    assert first["blocks"][0] == {"key": "计划", "content": "预留资源", "created_turn": 1, "updated_turn": 1, "expires_turn": 3}
    unchanged = first["blocks"][1]
    updated = apply(store, {"op": "update", "key": "计划", "content": "调整预留"}, turn=2)
    assert updated["blocks"][0] == {"key": "计划", "content": "调整预留", "created_turn": 1, "updated_turn": 2, "expires_turn": 3}
    assert updated["blocks"][1] == unchanged
    assert "[计划] 创建 T1，更新 T2，有效至 T3（含）" in updated["content"]
    assert "[结论] 创建 T1，更新 T1，无到期回合" in updated["content"]
    deleted = apply(store, {"op": "delete", "key": "计划"}, turn=3)
    assert deleted["blocks"] == [unchanged]
    assert deleted["used_chars"] == len("结论带条件的判断")


def test_expiry_is_inclusive_without_mutating_old_snapshots():
    store = MemoStore()
    apply(store, add(expires_turn=2), add("结论", "保留"))
    before = store.snapshot()
    store.expire(2)
    assert store.snapshot() == before
    store.expire(3)
    assert [b.key for b in store.snapshot().blocks] == ["结论"]
    assert store.snapshot().updated_turn == 1
    assert "预留资源" in before.content
    store.restore(before)
    assert store.snapshot().blocks[0].expires_turn == 2
    apply(store, {"op": "update", "key": "计划", "expires_turn": None}, turn=2)
    store.expire(35)
    assert [b.key for b in store.snapshot().blocks] == ["计划", "结论"]


def test_expiry_can_be_extended_and_expired_keys_can_be_added_again():
    store = MemoStore()
    apply(store, add(expires_turn=1))
    apply(store, {"op": "update", "key": "计划", "expires_turn": 3})
    store.expire(3)
    assert store.snapshot().blocks[0].expires_turn == 3
    apply(store, add(content="新计划", expires_turn=4), turn=4)
    assert store.snapshot().blocks[0].created_turn == 4
    store.expire(5)
    assert store.snapshot().to_dict()["used_chars"] == 0
    assert store.snapshot().content == ""


def test_batch_capacity_is_checked_on_final_state_and_names_count_too():
    store = MemoStore()
    apply(store, add("a", "旧" * 3999))
    assert store.snapshot().used_chars == 4000
    # 新增后临时超容，但整批会删除旧块，最终恰好4000字符。
    final = apply(store, add("新", "🧠" * 3999), {"op": "delete", "key": "a"}, turn=2)
    assert final["used_chars"] == 4000  # Unicode 字符，不是UTF-8字节或UTF-16单元。
    before = store.snapshot()
    with pytest.raises(ValueError, match="最终 4001 字符"):
        apply(store, {"op": "update", "key": "新", "content": "🧠" * 4000})
    assert store.snapshot() == before


@pytest.mark.parametrize("operations", [
    [], None, {}, [False], [{"op": "missing", "key": "x"}], [add(key=" ")], [add(key=1)],
    [add(content="")], [add(content=False)], [add(expires_turn=True)], [add(expires_turn=0)],
    [add(expires_turn=1.5)], [add(expires_turn="2")], [add(created_turn=1)],
    [{"op": "add", "key": "x"}], [add("已有")],
    [{"op": "update", "key": "不存在", "content": "x"}],
    [{"op": "delete", "key": "不存在"}], [{"op": "update", "key": "已有"}],
    [{"op": "delete", "key": "已有", "content": "x"}],
    [{"op": "update", "key": "已有", "content": ""}],
    [{"op": "update", "key": "已有", "content": "改变"}, {"op": "delete", "key": "已有"}],
    [add("先有效", "但不应保存"), {"op": "delete", "key": "不存在"}],
])
def test_bad_batch_does_not_partially_commit(operations):
    store = MemoStore()
    apply(store, add("已有", "保留", expires_turn=5))
    before = store.snapshot()
    with pytest.raises(ValueError):
        store.apply({"operations": operations}, turn=1)
    assert store.snapshot() == before


def test_legacy_whole_document_can_be_mixed_with_later_block_usage():
    store = MemoStore()
    result = store.write("原来的完整备忘", turn=1)
    assert result["used_chars"] == len("原来的完整备忘")
    assert store.snapshot() == Memo("原来的完整备忘", 1)
    result = apply(store, add("新块", "新信息"), turn=2)
    assert result["blocks"][0] == {"key": "全文", "content": "原来的完整备忘", "created_turn": 1, "updated_turn": 1, "expires_turn": None}
    assert result["blocks"][1]["key"] == "新块"
    before = store.snapshot()
    with pytest.raises(ValueError, match="混用"):
        store.apply({"content": "x", "operations": [add("y")]}, turn=3)
    assert store.snapshot() == before
    store.write("", turn=3)
    assert store.snapshot() == Memo("", 3)
    assert store.snapshot().used_chars == 0


def test_harness_counts_one_call_returns_usage_and_filters_next_turn_prompt():
    tools = GuildManagerTools.from_data_dir("data/presets/default")
    tools.start_session("memo-blocks")
    store = MemoStore()
    h = TurnToolHarness(tools, "memo-blocks", max_tool_calls=20, memo_store=store)
    result = h.call_tool("write_memo", {"operations": [add(expires_turn=1), add("长期", "跨回合计划")]})
    assert result["ok"] and result["tool_budget"]["used"] == 1
    assert result["memo"]["used_chars"] == store.snapshot().used_chars
    text = _format_tool_result_for_model("write_memo", result)
    assert f"已用 {store.snapshot().used_chars}/4000 字符" in text
    assert "预留资源" not in text  # 不把所有原文再次回显给模型。
    assert h.call_tool("end_turn", {"hunts": []})["ok"]
    # 直接构造提示也必须按目标回合过滤，不依赖先发生下一次写入。
    obs = tools.get_observation("memo-blocks")["observation"]
    prompt = build_turn_prompt(obs, memo=store.snapshot())
    assert "预留资源" not in prompt and "跨回合计划" in prompt
    new = TurnToolHarness(tools, "memo-blocks", max_tool_calls=20, memo_store=store)
    failure = new.call_tool("write_memo", {"operations": [{"op": "delete", "key": "不存在"}]})
    assert not failure["ok"] and failure["tool_budget"]["used"] == 1
    assert failure["memo"]["used_chars"] == len("长期跨回合计划")
    assert "备忘未修改，已用" in _format_tool_result_for_model("write_memo", failure)


def test_memo_rejected_by_budget_or_ended_turn_still_reports_used_characters():
    tools = GuildManagerTools.from_data_dir("data/presets/default")
    tools.start_session("memo-budget")
    h = TurnToolHarness(tools, "memo-budget", max_tool_calls=1)
    assert h.call_tool("write_memo", {"operations": [add()]})["ok"]
    for ended in (False, True):
        if ended:
            assert h.call_tool("end_turn", {"hunts": []})["ok"]
        result = h.call_tool("write_memo", {"operations": [add("额外")]})
        assert not result["ok"]
        assert result["memo"]["used_chars"] == len("计划预留资源")
        assert result["tool_budget"]["used"] == 1


def test_model_schema_and_guidance_are_the_requested_version():
    tools = GuildManagerTools.from_data_dir("data/presets/default")
    tools.start_session("memo-schema")
    h = TurnToolHarness(tools, "memo-schema", max_tool_calls=20)
    schema = next(s for s in h.tool_schemas() if s["name"] == "write_memo")
    assert schema["parameters"]["required"] == ["operations"]
    assert "content" not in schema["parameters"]["properties"]
    assert MEMO_GUIDANCE == "备忘录只记录“以后用得上、查不到”的关键信息：跨回合计划、预留资源、待办验证、核心对比结论等。不要抄录当前面板状态、基础规则和随时能查到的列表。如果有必要，给时效信息标上过期回合，过期的和办完的及时删掉。4000字是上限，不必强求写满。"
    assert MEMO_GUIDANCE in schema["description"]
    assert MEMO_GUIDANCE in build_system_prompt()


def test_replay_restore_applies_batches_ignores_failed_turns_and_expires_at_checkpoint():
    def turn(number, operations, status="completed", ok=True):
        return {"turn": number, "status": status, "steps": [{
            "type": "tool_result", "name": "write_memo", "arguments": {"operations": operations},
            "result": {"ok": ok},
        }]}
    replay = {"turns": [
        turn(1, [add("临时", "旧信息", expires_turn=2), add("长期", "原计划")]),
        turn(2, [{"op": "update", "key": "长期", "content": "调整后的计划"}]),
        turn(3, [add("失败分支", "不能保存")], status="failed"),
        turn(4, [add("临时", "新的临时信息", expires_turn=4)]),
        turn(4, [add("失败调用", "不能保存")], ok=False),
    ], "final_observation": {"turn": 5}}
    original = deepcopy(replay)
    restored = _memo_store_from_replay(replay).snapshot()
    assert [(b.key, b.content, b.created_turn, b.updated_turn) for b in restored.blocks] == [
        ("长期", "调整后的计划", 1, 2),
    ]
    assert replay == original
