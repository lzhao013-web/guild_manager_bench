import { fireEvent, render, screen, within } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { actionEntries, actionResult } from "./actionModel";
import { ActionResults } from "./ActionResultPanel";
import { describeReturn } from "./actionReturns";
import { watchFrames } from "./watchModel";
import type { Observation } from "./types";
import type { WatchRecord } from "./watchModel";

const member = { adventurer_id: "hero", name: "结果剑士", level: 2, template_id: "warrior",
    resources: { current_hp: 120, current_mp: 0 }, effective_stats: { hp: 120, mp: 0, attack: 18 },
    tactics: { skill_priority: [], disabled_skills: [] }, skills: [{ skill_id: "slash", name: "重斩" }] };
const before: Observation = { turn: 1, max_turns: 8, gold: 100, experience_pool: 100, materials: {}, party_size_limit: 3,
    adventurers: [member], equipment_inventory: [{ instance_id: "old", name: "旧库存铁剑", stats: { attack: 99 } }] };
const rec = (cursor: number, event: WatchRecord["event"]): WatchRecord => ({ cursor, after: cursor - 1, at: "2026-10-20T10:00:00Z", event });
function entry(name: string, result: object, args: object = {}) {
    return actionEntries(watchFrames([rec(1, { type: "turn_started", observation: before }), rec(2, { type: "tool_result", name, arguments: args, result })]))[1];
}

it("招募、制作与天赋直接展示实际结果对象，不再只返回成功计数", () => {
    const recruited = entry("recruit_adventurer", { ok: true, _action_output: { adventurers: [member] } }, { candidate_id: "candidate" });
    expect(actionResult(recruited)).toBe("招募到 结果剑士，等级 2");
    const crafted = entry("craft_equipment", { ok: true, _action_output: { equipment_inventory: [{ instance_id: "new", name: "新制铁剑", slot: "main_hand", stats: { attack: 7 }, equipped_by: null }] } }, { recipe_id: "recipe" });
    expect(actionResult(crafted)).toContain("实际制成 新制铁剑，实例 new");
    render(<ActionResults entry={crafted} />);
    const result = screen.getByRole("region", { name: "实际制作出的装备" });
    expect(result).toHaveTextContent("攻击 7");
    expect(result).toHaveTextContent("主手");
    expect(result).toHaveTextContent("未穿戴");
    expect(result).not.toHaveTextContent("旧库存铁剑");
    const unlocked = entry("purchase_upgrade", { ok: true, _action_output: { global_upgrades: [{ name: "训练场", upgrade_id: "training", unlocked: true, stats: { attack: 3 } }] } });
    expect(actionResult(unlocked)).toBe("本次已解锁 训练场");
    expect(unlocked.returned.tables[0].title).toBe("本次已解锁天赋");
    expect(unlocked.returned.tables[0].rows[0]).toContain("攻击 3");
    expect(recruited.returned.tables.some(table => table.title.includes("实际战术"))).toBe(false);
});

it("查询使用本次返回而非当前快照，空返回不从已有队伍补人", () => {
    const empty = entry("get_party", { adventurers: [], experience_pool: 0 });
    const { rerender } = render(<ActionResults entry={empty} />);
    expect(screen.getByRole("region", { name: "返回的队伍" })).toHaveTextContent("本次实际返回空列表");
    expect(screen.getByRole("region", { name: "业务返回" })).not.toHaveTextContent("结果剑士");
    const actual = entry("get_party", { adventurers: [{ ...member, name: "查询返回成员", level: 5 }], experience_pool: 0 });
    rerender(<ActionResults entry={actual} />);
    expect(screen.getByRole("region", { name: "返回的队伍" })).toHaveTextContent("查询返回成员");
    expect(screen.getByRole("region", { name: "返回的队伍" })).toHaveTextContent("攻击 18");
});

it("输入始终可见且派遣改为紧凑表格，摘要、战果和奖励直接可读", () => {
    const second = { ...member, adventurer_id: "hero2", name: "第二位剑士" };
    const action = actionEntries(watchFrames([
        rec(1, { type: "turn_started", observation: { ...before, adventurers: [member, second] } }),
        rec(2, { type: "tool_result", name: "end_turn", arguments: { hunts: [{ adventurer_id: "hero", monster_id: "slime" }, { adventurer_id: "hero2", monster_id: "boss" }] },
            result: { ok: true, _action_output: { adventurers: [{ ...member, resources: { current_hp: 110, current_mp: 0 } }] }, turn_result: { battles: [
                { adventurer_name: "结果剑士", monster_name: "史莱姆", won: true, reward: { gold: 0, experience: 12 } },
                { adventurer_name: "第二位剑士", monster_name: "首领", won: false, reward: { gold: 0, experience: 0 } },
            ] } } }),
    ]))[1];
    expect(action.returned.tables[0].title).toBe("已记录的战斗结果");
    render(<ActionResults entry={action} />);
    const input = screen.getByRole("region", { name: "输入参数" });
    expect(input.closest("details")).toBeNull();
    expect(within(input).getByRole("table")).toBeVisible();
    expect(within(input).getAllByRole("row")).toHaveLength(3);
    const battles = screen.getByRole("region", { name: "已记录的战斗结果" });
    expect(battles).toHaveTextContent("胜利");
    expect(battles).toHaveTextContent("金币 0 · 经验 12");
    expect(within(battles).queryByRole("columnheader", { name: "行动 / 耗时" })).not.toBeInTheDocument();
    expect(screen.getByRole("region", { name: "执行结果" }).compareDocumentPosition(input) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
});

it("大集合可在已返回数据内分页和筛选，不额外请求，也不依赖原始 JSON", () => {
    const fetch = vi.spyOn(globalThis, "fetch");
    const records = Array.from({ length: 13 }, (_, i) => ({ ...member, adventurer_id: `member-${i}`, name: `候选成员${i}`, skills: [{ skill_id: `skill-${i}`, name: "重斩" }] }));
    render(<ActionResults entry={entry("get_party", { adventurers: records })} />);
    const table = screen.getByRole("region", { name: "返回的队伍" });
    expect(within(table).getAllByRole("row")).toHaveLength(7);
    fireEvent.click(within(table).getByRole("button", { name: "下一页" }));
    expect(table).toHaveTextContent("候选成员6");
    fireEvent.change(within(table).getByRole("searchbox"), { target: { value: "skill-12" } });
    expect(within(table).getAllByRole("row")).toHaveLength(2);
    expect(table).toHaveTextContent("候选成员12");
    fireEvent.click(within(table).getByRole("button", { name: /候选成员12/ }));
    expect(table).toHaveTextContent("重斩");
    expect(fetch).not.toHaveBeenCalled();
});

it("实际战术与模拟结果不拿输入冒充，失败不展示产物，缺失不伪造成零", () => {
    const configured = entry("configure_tactics", { ok: true, _action_output: { adventurers: [member] } }, { adventurer_id: "hero", skill_priority: ["slash"] });
    expect(configured.returned.tables[0].title).toContain("实际战术");
    expect(configured.returned.tables[0].rows).toContainEqual(["技能顺序", "默认顺序"]);
    expect(actionResult(configured)).toContain("技能顺序 默认顺序");
    const simulated = entry("preview_battle", { ok: true, preview: { adventurer_name: "结果剑士", monster_name: "史莱姆" } });
    const { rerender } = render(<ActionResults entry={simulated} />);
    expect(screen.getByRole("region", { name: "业务返回" })).toHaveTextContent("结果未记录");
    expect(screen.getByText(/这是查询或模拟返回/)).toBeVisible();
    expect(screen.getByRole("region", { name: "业务返回" })).toHaveTextContent("奖励未记录");
    rerender(<ActionResults entry={entry("craft_equipment", { ok: false, error: "金币不足", missing: { gold: 10 } }, { recipe_id: "sword" })} />);
    expect(screen.getByRole("region", { name: "执行结果" })).toHaveTextContent("金币不足");
    expect(screen.queryByRole("region", { name: "实际制作出的装备" })).not.toBeInTheDocument();
    expect(screen.getByRole("region", { name: "业务返回" })).toHaveTextContent("10");
    expect(entry("craft_equipment", { ok: true }, { recipe_id: "old" }).returned.tables).toEqual([]);
    const monsters = entry("get_monsters", { monsters: [{ name: "无技能怪物", stats: { hp: 20 }, skills: [] }] });
    expect(monsters.returned.tables[0].rows[0][3]).toBe("无");
    expect(monsters.returned.tables[0].columns).toContain("目标奖励");
});

it("已查询的战斗统计、未来条目和未知返回字段可读，文本不会执行 HTML", () => {
    const action = entry("get_battle_log", { ok: true, source: "actual", battle: { adventurer_name: "剑士", monster_name: "史莱姆", outcome: "left_win" },
        statistics: { left: { direct_damage: 31, healing_received: 0, skill_uses: { slash: 2 } } } });
    const { rerender } = render(<ActionResults entry={action} />);
    expect(screen.getByRole("region", { name: "业务返回" })).toHaveTextContent("直接伤害31");
    expect(screen.getByRole("region", { name: "业务返回" })).toHaveTextContent("实际收到的治疗0");
    const forecast = entry("get_forecast", { items: [{ name: "未来候选", turn: 5, forecast_ref: "future:5:1", recruit_gold: 30, base_stats: { hp: 80 } }], total_matches: 10, next_offset: 1 });
    expect(forecast.returned.preview).toBe(true);
    rerender(<ActionResults entry={forecast} />);
    expect(screen.getByRole("region", { name: "业务返回" })).toHaveTextContent("未来候选");
    const raw = '<img src=x onerror="alert(1)">';
    rerender(<ActionResults entry={entry("custom_tool", { ok: true, note: raw, value: 0, enabled: false })} />);
    expect(screen.getByRole("region", { name: "业务返回" })).toHaveTextContent(raw);
    expect(screen.getByRole("region", { name: "业务返回" })).toHaveTextContent("否（false）");
    expect(document.querySelector("img")).toBeNull();
    expect(describeReturn(action.frame).tables[0].rows[0][1]).toBe("胜利");
    rerender(<ActionResults entry={entry("write_memo", { ok: true, memo: { content: "preview" } }, { content: "preview" })} />);
    expect(screen.getByRole("region", { name: "业务返回" })).toHaveTextContent("preview");
    expect(screen.getByRole("region", { name: "业务返回" })).not.toHaveTextContent("模拟预览");
    const dictionary = JSON.parse('{"constructor":0,"payload":{"__proto__":"普通字段"},"source":"constructor"}');
    rerender(<ActionResults entry={entry("custom_tool", { ok: true, ...dictionary })} />);
    expect(screen.getByRole("region", { name: "业务返回" })).toHaveTextContent("constructor");
    expect(screen.getByRole("region", { name: "业务返回" })).toHaveTextContent("普通字段");
    rerender(<ActionResults entry={entry("end_turn", { ok: false, error: "缺少派遣目标" }, { hunts: [{}] })} />);
    expect(screen.getByRole("region", { name: "输入参数" })).toHaveTextContent("空对象 {}");
    const invalidArgs = JSON.parse('{"constructor":0,"__proto__":{"x":false},"slot":"constructor"}');
    rerender(<ActionResults entry={entry("unequip_item", { ok: false, error: "未知参数 constructor" }, invalidArgs)} />);
    expect(screen.getByRole("region", { name: "输入参数" })).toHaveTextContent("constructor");
    expect(screen.getByRole("region", { name: "输入参数" })).toHaveTextContent("否（false）");
    expect(screen.getByRole("region", { name: "执行结果" })).toHaveTextContent("未知参数 constructor");
    expect(screen.getByRole("region", { name: "执行结果" })).not.toHaveTextContent("native code");
});
