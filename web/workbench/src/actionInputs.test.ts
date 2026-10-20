import { expect, it } from "vitest";
import { actionEntries, actionSummary } from "./actionModel";
import { inputPreview } from "./actionInputs";
import { watchFrames } from "./watchModel";
import type { Observation } from "./types";
import type { WatchRecord } from "./watchModel";

const observation: Observation = { turn: 1, max_turns: 8, gold: 100, experience_pool: 20, materials: {}, party_size_limit: 3,
    adventurers: [{ adventurer_id: "a1", name: "验收剑士", template_id: "warrior", level: 1,
        resources: { current_hp: 100, current_mp: 20 }, effective_stats: { hp: 100, mp: 20 }, skills: [{ skill_id: "slash", name: "重斩" }] }],
    equipment_inventory: [{ instance_id: "sword", name: "铁剑" }], monsters: [{ monster_id: "slime", name: "史莱姆" }] };
const record = (cursor: number, event: WatchRecord["event"]): WatchRecord => ({ cursor, after: cursor - 1, at: "2026-10-20T10:00:00Z", event });
function entry(argumentsValue: unknown, result: object = { ok: true }, name = "configure_tactics") {
    return actionEntries(watchFrames([record(1, { type: "turn_started", observation }),
        record(2, { type: "tool_result", name, ...(argumentsValue === undefined ? {} : { arguments: argumentsValue }), result })]))[1];
}

it("结果未重复参数时只继承匹配调用，结果帧和原始事件不被改写", () => {
    const call = record(2, { type: "tool_call", name: "equip_item", call_id: "equip", arguments: { adventurer_id: "a1", equipment_id: "sword" } });
    const result = record(3, { type: "tool_result", name: "equip_item", call_id: "equip", result: { ok: true } });
    const frames = watchFrames([record(1, { type: "turn_started", observation }), call, result]);
    const action = actionEntries(frames)[1];
    expect(action.frame).toBe(frames[2]);
    expect(action.frame.record.event).not.toHaveProperty("arguments");
    expect(inputPreview(action.input)).toBe("成员：验收剑士，装备：铁剑");
    expect(action.title).toBe("验收剑士 装备 铁剑");
    expect(inputPreview(actionEntries(frames.slice(0, 2))[1].input)).toContain("铁剑");
    const explicitEmpty = actionEntries(watchFrames([record(1, { type: "turn_started", observation }), call, { ...result, event: { ...result.event, arguments: {} } }]))[1];
    expect(inputPreview(explicitEmpty.input)).toBe("无输入参数");
});

it("同名工具交错时按 call_id 保留各自的输入，不互相覆盖", () => {
    const entries = actionEntries(watchFrames([record(1, { type: "turn_started", observation }),
        record(2, { type: "tool_call", name: "allocate_experience", call_id: "a", arguments: { adventurer_id: "a1", amount: 0 } }),
        record(3, { type: "tool_call", name: "allocate_experience", call_id: "b", arguments: { adventurer_id: "a1", amount: 12.125 } }),
        record(4, { type: "tool_result", name: "allocate_experience", call_id: "b", result: { ok: false, error: "经验数量必须为整数" } }),
        record(5, { type: "tool_result", name: "allocate_experience", call_id: "a", result: { ok: false, error: "数量必须大于零" } }),
    ])).filter(entry => entry.kind === "action");
    expect(inputPreview(entries[0].input)).toContain("分配经验数量：0");
    expect(entries[0].status).toBe("failed");
    expect(inputPreview(entries[1].input)).toContain("分配经验数量：12.125");
});

it("空列表、false、零和 null 保持真实含义，与缺参数和空对象分开", () => {
    const action = entry({ amount: 0, preview: false, skill_priority: [], disabled_skills: [], extra: null });
    const preview = inputPreview(action.input);
    expect(preview).toContain("分配经验数量：0");
    expect(preview).toContain("仅预览：否（false）");
    expect(preview).toContain("技能优先级：空列表 []");
    expect(action.input.state === "recorded" && action.input.value.children.find(field => field.key === "extra")?.value.text).toBe("空值（null）");
    expect(inputPreview(entry(undefined).input)).toBe("原日志未记录输入参数");
    expect(inputPreview(entry({}).input)).toBe("无输入参数");
    expect(inputPreview(entry(null).input)).toBe("空值（null）");
});

it("数字引用只按已记录映射解析，不猜对象位置，原编号仍然保留", () => {
    const action = entry({ adventurer_id: 0, equipment_id: 2 }, { ok: false, error: "无法穿戴", _llm_refs: { adventurer: { a1: 0 }, equipment: { sword: 2 } } }, "equip_item");
    expect(inputPreview(action.input)).toBe("成员：验收剑士，装备：铁剑");
    expect(action.input.state === "recorded" && action.input.value.children[0].value.original).toBe("0");
    expect(inputPreview(entry({ adventurer_id: 0 }).input)).toBe("成员：成员 #0");
});

it("派遣结构、技能顺序和长备忘完整保存，列表预览不改写详情内容", () => {
    const hunts = entry({ hunts: [{ adventurer_id: "a1", monster_id: "slime" }] }, { ok: true }, "end_turn");
    expect(inputPreview(hunts.input)).toContain("派遣 1：成员：验收剑士，怪物：史莱姆");
    const tactics = entry({ skill_priority: ["slash"], disabled_skills: [] });
    expect(inputPreview(tactics.input)).toContain("第 1 项：重斩");
    const content = "a1 与 iron_ore 保持原文\n" + "长备忘".repeat(150);
    const memo = entry({ content }, { ok: true }, "write_memo");
    expect(memo.input.state === "recorded" && memo.input.value.children[0].value.text).toBe(content);
    expect(inputPreview(memo.input).length).toBeLessThanOrEqual(221);
});

it("完整摘要保留第三项之后的实际变化，缺失结果不冒充成功或留白", () => {
    const changes = Array.from({ length: 6 }, (_, index) => ({ kind: "resource", label: `资源${index}`, before: index, after: index + 1 }));
    expect(actionSummary(entry({}, { ok: true, event: { changes } }))).toContain("资源5 5 → 6");
    expect(actionSummary(entry({}, {}))).toContain("未明确记录执行是否成功");
    expect(actionSummary(entry({}, { ok: true }))).toContain("没有可展示的额外业务返回");
    expect(actionSummary(entry({}, { ok: false }))).toContain("未记录具体原因");
});
