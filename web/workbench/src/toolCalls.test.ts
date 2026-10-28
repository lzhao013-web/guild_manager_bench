import { expect, it } from "vitest";
import { matchesTool, operationCategory, summarizeToolCalls, toolCallStatus } from "./toolCalls";

it("所有工具包含查询、预览、旧式刷新及未注册工具，均可匹配并按名称汇总", () => {
    const names = ["get_crafting", "get_inventory", "get_upgrades", "get_recruitment", "get_events", "get_forecast", "get_battle_log",
        "preview_battle", "preview_team_power", "preview_monster_refresh", "refresh_monsters", "execute_plan", "write_memo", "dismiss_adventurer", "future_custom_tool"];
    const events = names.map(name => ({ type: "tool_result", name, result: { ok: true } }));
    events.push({ type: "tool_result", name: "refresh_monsters", arguments: { preview: true }, result: { ok: true } } as typeof events[number]);
    const tools = summarizeToolCalls(events);
    expect(tools.map(tool => tool.name)).toEqual(names);
    expect(tools.find(tool => tool.name === "refresh_monsters")?.total).toBe(2);
    for (const event of events) expect(matchesTool(event, event.name) && matchesTool(event, "all")).toBe(true);
    expect(operationCategory(events.at(-1)!)).toBe("preview");
    expect(operationCategory(events.find(event => event.name === "future_custom_tool")!)).toBe("other");
    expect(operationCategory({ type: "tool_result", name: "write_memo" })).toBe("memo");
    const plan = { type: "tool_result", name: "execute_plan" };
    expect(operationCategory(plan)).toBe("plan");
    expect(matchesTool(plan, "category:plan")).toBe(true);
    expect(summarizeToolCalls([plan])[0].label).toBe("执行方案");
});

it("执行状态互斥，明确工具文本可确认旧查询成功，缺结果不能虚构成功", () => {
    const events = [
        { type: "tool_call", name: "get_forecast" },
        { type: "tool_result", name: "get_forecast", result: { ok: false } },
        { type: "tool_result", name: "get_forecast", result: { ok: true } },
        { type: "tool_result", name: "get_forecast", content: "成功 get_forecast" },
        { type: "tool_result", name: "get_forecast", result: {} },
    ];
    expect(events.map(toolCallStatus)).toEqual(["pending", "failed", "succeeded", "succeeded", "recorded"]);
    expect(summarizeToolCalls(events)[0]).toMatchObject({ total: 5, pending: 1, failed: 1, succeeded: 2, recorded: 1 });
    expect(summarizeToolCalls([{ type: "turn_started" }])).toEqual([]);
});
