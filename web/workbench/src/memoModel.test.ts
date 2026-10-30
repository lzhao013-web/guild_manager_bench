import { expect, it } from "vitest";
import { memoAt } from "./memoModel";
import type { WatchPage } from "./watchModel";

const page: WatchPage = {
    cursor: 50, selected: 2, attempts: [{ id: 2, turn: 2, attempt: 1, status: "running", score: null, actions: 2, at: "", updated_at: "" }],
    memo_before: { state: "saved", content: "上一回合的计划", updated_turn: 1 },
    records: [
        { cursor: 10, after: 0, at: "", event: { type: "tool_result", name: "write_memo", result: { ok: false, memo: { content: "失败不能覆盖", updated_turn: 2 } } } },
        { cursor: 20, after: 10, at: "", event: { type: "tool_result", name: "write_memo", result: { ok: true, memo: { content: "新计划", updated_turn: 2 } } } },
        { cursor: 30, after: 20, at: "", event: { type: "tool_result", name: "write_memo", result: { ok: true, memo: { content: "", updated_turn: 2 } } } },
    ],
};
it("备忘随精确步骤更新，不泄露未来正文，清空不是缺失，回滚不当成当前有效写入", () => {
    expect(memoAt(page, 10)).toEqual(page.memo_before);
    expect(memoAt(page, 20)).toEqual({ state: "saved", content: "新计划", updated_turn: 2 });
    expect(memoAt(page, 30)).toEqual({ state: "empty", updated_turn: 2 });
    expect(memoAt({ ...page, attempts: [{ ...page.attempts[0], status: "rolled_back" }] }, 30)).toEqual(page.memo_before);
    expect(memoAt({ ...page, attempts: [{ ...page.attempts[0], status: "failed" }] }, 20)).toEqual(page.memo_before);
});
it("旧服务未提供基线时显示未知，当前成功的结构化写入仍可读取", () => {
    const old = { ...page, memo_before: undefined };
    expect(memoAt(old, 10)).toEqual({ state: "unavailable" });
    expect(memoAt(old, 20)).toEqual({ state: "saved", content: "新计划", updated_turn: 2 });
});
