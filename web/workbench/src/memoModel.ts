import type { MemoSnapshot, WatchPage } from "./watchModel";

export function memoAt(page: WatchPage | undefined, cursor: number): MemoSnapshot {
    const before = page?.memo_before || { state: "unavailable" } as const;
    const attempt = page?.attempts.find(item => item.id === page.selected);
    if (attempt && ["rolled_back", "failed"].includes(attempt.status)) return before;
    let memo: MemoSnapshot = before;
    for (const record of page?.records || []) {
        if (record.cursor > cursor) continue;
        const event = record.event;
        if (event.type === "turn_failed" || event.type === "turn_retry") memo = before;
        if (event.type !== "tool_result" || event.name !== "write_memo" || event.result?.ok !== true) continue;
        const saved = event.result.memo;
        if (saved && "count" in saved) memo = { state: "legacy" };
        else if (typeof saved?.content === "string" && typeof saved.updated_turn === "number")
            memo = saved.content ? { state: "saved", content: saved.content, updated_turn: saved.updated_turn }
                : { state: "empty", updated_turn: saved.updated_turn };
        else memo = { state: "unavailable" };
    }
    return memo;
}
