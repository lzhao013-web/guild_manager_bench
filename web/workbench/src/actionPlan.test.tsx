import { fireEvent, render, screen, within } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { ActionTimeline } from "./ActionTimeline";
import { ActionResults } from "./ActionResultPanel";
import { actionEntries } from "./actionModel";
import { describePlan, findPlanPreview } from "./actionPlan";
import { watchFrames } from "./watchModel";
import type { Observation } from "./types";
import type { WatchFrame, WatchRecord } from "./watchModel";

const before: Observation = { turn: 10, max_turns: 35, gold: 300, experience_pool: 1000, materials: { mithril_shard: 3 }, party_size_limit: 6,
    adventurers: [
        { adventurer_id: "knight", name: "步行骑士", template_id: "foot_knight", level: 6,
            effective_stats: { hp: 349, mp: 10, attack: 46, defense: 60 }, resources: { current_hp: 200, current_mp: 10 }, equipment_slots: [{ slot: "armor", item: null }],
            tactics: { skill_priority: [], disabled_skills: [] } },
        { adventurer_id: "mage", name: "瘟疫法师·坚壁", template_id: "plague_mage", level: 8,
            effective_stats: { hp: 235, mp: 104, attack: 66 }, resources: { current_hp: 235, current_mp: 104 }, equipment_slots: [{ slot: "armor", item: null }],
            tactics: { skill_priority: [], disabled_skills: [] }, skills: [{ skill_id: "touch", name: "枯萎之触" }, { skill_id: "spread", name: "疫病蔓延" }] },
    ], equipment_inventory: [] };
const after: Observation = { ...before, gold: 155, experience_pool: 70, materials: { mithril_shard: 1 },
    adventurers: [
        { ...before.adventurers[0], level: 8, effective_stats: { hp: 439, mp: 10, attack: 48, defense: 83 },
            resources: { current_hp: 290, current_mp: 10 }, equipment_slots: [{ slot: "armor", item: { instance_id: "armor", name: "秘银护甲" } }] },
        { ...before.adventurers[1], level: 9, effective_stats: { hp: 257, mp: 110, attack: 69 },
            resources: { current_hp: 257, current_mp: 110 }, tactics: { skill_priority: ["touch", "spread"], disabled_skills: [] } },
    ], equipment_inventory: [{ instance_id: "armor", name: "秘银护甲", slot: "armor", equipped_by: "knight" }] };
const operations = [
    { tool: "allocate_experience", arguments: { adventurer_id: 2, amount: 345 } },
    { tool: "configure_tactics", arguments: { adventurer_id: 2, skill_priority: ["touch", "spread"], disabled_skills: [] } },
    { tool: "allocate_experience", arguments: { adventurer_id: 1, amount: 300 } },
    { tool: "allocate_experience", arguments: { adventurer_id: 1, amount: 285 } },
    { tool: "craft_equipment", arguments: { recipe_id: "mithril_armor" }, as: "armor" },
    { tool: "equip_item", arguments: { adventurer_id: 1, equipment_instance_id: "$armor" } },
];
const refs = { adventurer: { knight: 1, mage: 2 } };
const changes = [
    { kind: "resource", label: "金币", before: 300, after: 155 },
    { kind: "resource", label: "经验池", before: 1000, after: 70 },
    { kind: "material", label: "mithril_shard", before: 3, after: 1 },
    { kind: "adventurer", label: "步行骑士 等级", before: 6, after: 8 },
    { kind: "adventurer", label: "步行骑士 最大 HP", before: 349, after: 439 },
    { kind: "adventurer", label: "步行骑士 防御", before: 60, after: 83 },
    { kind: "adventurer", label: "瘟疫法师·坚壁 等级", before: 8, after: 9 },
    { kind: "adventurer", label: "瘟疫法师·坚壁 最大 MP", before: 104, after: 110 },
];
const events = operations.map((op, i) => ({ sequence: i + 1, summary: "", action: {
    type: op.tool === "craft_equipment" ? "craft" : op.tool === "equip_item" ? "equip" : op.tool,
    ...op.arguments, ...(i === 5 ? { equipment_instance_id: "armor" } : {}),
}, changes: [
    [
        { kind: "resource", label: "经验池", before: 1000, after: 655 },
        { kind: "adventurer", label: "瘟疫法师·坚壁 等级", before: 8, after: 9 },
        { kind: "adventurer", label: "瘟疫法师·坚壁 最大 MP", before: 104, after: 110 },
    ],
    [],
    [
        { kind: "resource", label: "经验池", before: 655, after: 355 },
        { kind: "adventurer", label: "步行骑士 等级", before: 6, after: 7 },
        { kind: "adventurer", label: "步行骑士 最大 HP", before: 349, after: 369 },
        { kind: "adventurer", label: "步行骑士 防御", before: 60, after: 64 },
    ],
    [
        { kind: "resource", label: "经验池", before: 355, after: 70 },
        { kind: "adventurer", label: "步行骑士 等级", before: 7, after: 8 },
        { kind: "adventurer", label: "步行骑士 最大 HP", before: 369, after: 389 },
        { kind: "adventurer", label: "步行骑士 防御", before: 64, after: 68 },
    ],
    [
        { kind: "resource", label: "金币", before: 300, after: 155 },
        { kind: "material", label: "mithril_shard", before: 3, after: 1 },
        { kind: "equipment", label: "获得装备", after: "秘银护甲 (armor)" },
    ],
    [
        { kind: "equipment", label: "秘银护甲 装备者", before: "未装备", after: "knight" },
        { kind: "adventurer", label: "步行骑士 最大 HP", before: 389, after: 439 },
        { kind: "adventurer", label: "步行骑士 防御", before: 68, after: 83 },
    ],
][i] }));
const record = (cursor: number, event: WatchRecord["event"]): WatchRecord => ({ cursor, after: cursor - 1, at: "2026-10-29T09:34:20Z", event });
const initial = record(1, { type: "turn_started", observation: before });
const preview = record(3, { type: "tool_result", name: "preview_team_power", call_id: "preview", result: {
    ok: true, simulation_only: true, operation_count: 6, changes, _llm_refs: refs,
} });
const execution = record(4, { type: "tool_result", name: "execute_plan", arguments: { plan_id: "receipt" }, result: {
    ok: true, operation_count: 6, already_executed: false, events, _llm_refs: refs, _observation_after: after,
} });

it("六步方案在时间线只展示摘要，右侧保留资源、成员和完整步骤，选择仍落在真实结果帧", () => {
    const records = [initial, record(2, { type: "tool_call", name: "preview_team_power", call_id: "preview", arguments: { operations } }), preview, execution];
    const original = JSON.stringify(records);
    const frames = watchFrames(records);
    const onFrame = vi.fn();
    const node = (frame: WatchFrame) => <ActionTimeline frames={frames} frame={frame} active following={false} rolledBack={false} onFrame={onFrame} renderDetails={() => null} />;
    const view = render(node(frames.at(-1)!));
    const timeline = screen.getByRole("region", { name: "观战行动时间线" });
    const previewRow = within(timeline).getByRole("button", { name: "定位行动 方案试算" });
    expect(previewRow).toHaveTextContent("预览");
    expect(previewRow).toHaveTextContent("共 6 步");
    expect(within(timeline).queryByRole("region", { name: "方案操作概要" })).not.toBeInTheDocument();
    expect(within(timeline).queryByRole("region", { name: "实际资源变化" })).not.toBeInTheDocument();
    const actual = within(timeline).getByRole("button", { name: "定位行动 执行方案" });
    const actualDetail = screen.getByRole("region", { name: "所选行动详情" });
    const resources = within(actualDetail).getByRole("region", { name: "实际资源变化" });
    expect(within(within(resources).getByRole("row", { name: /经验池/ })).getAllByRole("cell").map(cell => cell.textContent)).toEqual(["1,000", "70", "-930"]);
    expect(within(resources).getAllByText("秘银碎片", { exact: true })).toHaveLength(1);
    const members = within(actualDetail).getByRole("region", { name: "实际成员变化" });
    expect(within(members).getAllByRole("article")).toHaveLength(2);
    const knight = within(members).getByRole("article", { name: "步行骑士" });
    expect(knight).toHaveTextContent("Lv.6 → 8");
    const stats = within(knight).getByRole("table", { name: "步行骑士的属性变化" });
    expect(within(within(stats).getByRole("row", { name: /生命上限/ })).getAllByRole("cell").map(cell => cell.textContent)).toEqual(["349", "439", "+90"]);
    expect(within(within(stats).getByRole("row", { name: /防御/ })).getAllByRole("cell").map(cell => cell.textContent)).toEqual(["60", "83", "+23"]);
    const equipment = within(knight).getByRole("table", { name: "步行骑士的装备与战术变化" });
    expect(within(within(equipment).getByRole("row", { name: /装备/ })).getAllByRole("cell").map(cell => cell.textContent)).toEqual(["护甲：空槽位", "护甲：秘银护甲"]);
    const mage = within(members).getByRole("article", { name: "瘟疫法师·坚壁" });
    const tactics = within(mage).getByRole("table", { name: "瘟疫法师·坚壁的装备与战术变化" });
    expect(within(within(tactics).getByRole("row", { name: /技能顺序/ })).getAllByRole("cell").map(cell => cell.textContent)).toEqual(["默认顺序", "枯萎之触 → 疫病蔓延"]);
    expect(members).not.toHaveTextContent("undefined");
    view.rerender(node(frames.find(frame => frame.cursor === preview.cursor)!));
    const previewDetail = screen.getByRole("region", { name: "所选行动详情" });
    expect(within(previewDetail).getByRole("region", { name: "计划步骤明细" })).toHaveTextContent("计划操作6 步");
    const gold = within(within(previewDetail).getByRole("region", { name: "预计资源变化" })).getByRole("row", { name: /金币/ });
    expect(within(gold).getAllByRole("cell").map(cell => cell.textContent)).toEqual(["300", "155", "-145"]);
    expect(within(previewDetail).queryByRole("region", { name: "实际资源变化" })).not.toBeInTheDocument();
    const steps = within(previewDetail).getByRole("region", { name: "计划步骤明细" });
    expect(steps.closest("details")).toBeNull();
    expect(within(steps).getAllByRole("listitem")).toHaveLength(6);
    expect(steps).toHaveTextContent("给 瘟疫法师·坚壁 分配 345经验");
    expect(steps).toHaveTextContent("优先：枯萎之触 → 疫病蔓延 / 停用：无");
    expect(steps).not.toHaveTextContent("第 1 项");
    expect(steps).toHaveTextContent("方案装备 $armor");
    expect(steps).not.toHaveTextContent("tool：");
    expect(onFrame).not.toHaveBeenCalled();
    fireEvent.click(actual);
    expect(onFrame).toHaveBeenCalledWith(frames.at(-1));
    expect(JSON.stringify(records)).toBe(original);
    view.rerender(node(frames.at(-1)!));
    const detail = screen.getByRole("region", { name: "所选行动详情" });
    expect(within(detail).getByRole("region", { name: "输入参数" , hidden: true }).closest("details")).not.toHaveAttribute("open");
    fireEvent.click(within(detail).getByText("完整实际变化（含属性前后值）"));
    expect(within(detail).getByText("当前生命：200 → 290")).toBeVisible();
});

it("事件独立于快照汇总净变化，同一成员的多次升级不重复计算", () => {
    const entry = actionEntries(watchFrames([record(1, { type: "tool_result", name: "execute_plan", result: { ok: true, operation_count: 2, events: [
        { action: { type: "allocate_experience", adventurer_id: "knight", amount: 20 }, changes: [
            { kind: "resource", label: "经验池", before: 100, after: 80 },
            { kind: "adventurer", label: "步行骑士 等级", before: 1, after: 2 },
        ] },
        { action: { type: "allocate_experience", adventurer_id: "knight", amount: 30 }, changes: [
            { kind: "resource", label: "经验池", before: 80, after: 50 },
            { kind: "adventurer", label: "步行骑士 等级", before: 2, after: 3 },
        ] },
    ] } })]))[0];
    const plan = describePlan(entry);
    expect(plan.resources).toEqual([{ kind: "resource", label: "经验池", before: 100, after: 50 }]);
    expect(plan.members).toMatchObject([{ name: "步行骑士", level: { before: 1, after: 3 } }]);
    expect(plan.steps).toHaveLength(2);
});

it("事件与快照记录同一次升级时只展示一次解锁，不把补充快照当成第二份收益", () => {
    const entry = actionEntries(watchFrames([
        record(1, { type: "turn_started", observation: { ...before, global_upgrades: [{ upgrade_id: "training", name: "训练场", unlocked: false }] } }),
        record(2, { type: "tool_result", name: "execute_plan", result: { ok: true, operation_count: 1,
            events: [{ action: { type: "purchase_upgrade", upgrade_id: "training" }, changes: [{ kind: "upgrade", label: "解锁加成", after: "训练场" }] }],
            _observation_after: { ...before, global_upgrades: [{ upgrade_id: "training", name: "训练场", unlocked: true }] },
        } }),
    ]))[1];
    expect(describePlan(entry).other).toEqual([{ kind: "upgrade", label: "解锁加成", after: "训练场" }]);
});

it("重复提交虽携带原事件和产物也不再展示资源消耗、成员收益或已执行步骤", () => {
    const repeated = { ...execution, event: { ...execution.event, result: { ...execution.event.result, already_executed: true } } };
    const entry = actionEntries(watchFrames([initial, repeated]))[1];
    render(<ActionResults entry={entry} />);
    expect(screen.getByRole("region", { name: "执行结果" })).toHaveTextContent("该方案已提交过，本次未重复执行");
    expect(screen.queryByRole("region", { name: "实际资源变化" })).not.toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "实际成员变化" })).not.toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "已执行步骤明细" })).not.toBeInTheDocument();
});

it("失败、缺失与空变化清单分别表达，不拿真实快照当预计收益，也不伪造零变化", () => {
    const [entry] = actionEntries(watchFrames([record(1, { ...preview.event, arguments: { operations } })]));
    const [failed] = actionEntries(watchFrames([record(1, { type: "tool_result", name: "preview_team_power", arguments: { operations: [null] },
        result: { ok: false, error: "第 1 步操作格式无效" } })]));
    const { rerender } = render(<ActionResults entry={failed} />);
    expect(screen.getByRole("region", { name: "执行结果" })).toHaveTextContent("第 1 步操作格式无效");
    expect(screen.queryByRole("region", { name: "预计资源变化" })).not.toBeInTheDocument();
    const missing = { ...entry, frame: { ...entry.frame, before, observation: after }, event: { ...entry.event, result: { ok: true, operation_count: 6 } } };
    rerender(<ActionResults entry={missing} />);
    expect(screen.getByText("预计变化未记录")).toBeVisible();
    expect(screen.queryByRole("region", { name: "实际资源变化" })).not.toBeInTheDocument();
    rerender(<ActionResults entry={{ ...missing, event: { ...missing.event, result: { ok: true, operation_count: 0, changes: [] } } }} />);
    expect(screen.getByText("无变化")).toBeVisible();
    expect(screen.queryByText("预计变化未记录")).not.toBeInTheDocument();
});

function previewEntry(result: Record<string, unknown>, planned = operations) {
    return actionEntries(watchFrames([initial, record(2, { type: "tool_result", name: "preview_team_power", arguments: { operations: planned }, result })])).at(-1)!;
}

it("试算突出当时的提交资格和正式 rank，预算门槛不是消费，零值保留且不补算缺失分数", () => {
    const result = { ok: true, operation_count: 6, required_calls: 6, remaining_calls: 12, can_execute: true, plan_id: "accepted", changes,
        current_rank_score: 64.2, projected_rank_score: 71.5, rank_score_delta: 7.3 };
    const { rerender } = render(<ActionResults entry={previewEntry(result)} />);
    const status = screen.getByRole("region", { name: "执行结果" });
    expect(status).toHaveTextContent("仅试算，未改变真实状态");
    expect(status).toHaveTextContent("试算时可提交");
    expect(status).toHaveTextContent("需余额 ≥6 次 / 试算后剩 12 次");
    const rank = screen.getByRole("region", { name: "正式战力试算" });
    expect(rank).toHaveTextContent("调用前64.20方案执行后（预计）71.50变化+7.30");
    const gold = within(screen.getByRole("region", { name: "预计资源变化" })).getByRole("row", { name: /金币/ });
    expect(within(gold).getAllByRole("cell").map(cell => cell.textContent)).toEqual(["300", "155", "-145"]);
    expect(screen.queryByText("方案投影编号（仅用于该方案讨伐预览）", { exact: true, selector: "summary" })).not.toBeInTheDocument();
    rerender(<ActionResults entry={previewEntry({ ...result, remaining_calls: 5, can_execute: false, current_rank_score: 0, projected_rank_score: 0, rank_score_delta: 0 })} />);
    expect(screen.getByRole("region", { name: "执行结果" })).toHaveTextContent("预算不足，无法提交");
    expect(screen.getByRole("region", { name: "正式战力试算" })).toHaveTextContent("调用前0.00方案执行后（预计）0.00变化0.00");
    rerender(<ActionResults entry={previewEntry({ ok: true, operation_count: 6, changes, current_rank_score: 64.2, projected_rank_score: 63.1 })} />);
    expect(screen.getByRole("region", { name: "正式战力试算" })).toHaveTextContent("变化未记录");
    rerender(<ActionResults entry={previewEntry({ ok: true, operation_count: 6, changes, score: 100 })} />);
    expect(screen.queryByRole("region", { name: "正式战力试算" })).not.toBeInTheDocument();
    expect(screen.getByRole("region", { name: "执行结果" })).toHaveTextContent("提交资格未记录");
});

it("空方案明确预检且不展示缺失步骤，投影编号与业务返回集中收底", async () => {
    const { rerender } = render(<ActionResults entry={previewEntry({ ok: true, operation_count: 0, required_calls: 0, remaining_calls: 10, can_execute: false, changes: [] }, [])} />);
    expect(screen.getByRole("region", { name: "执行结果" })).toHaveTextContent("空方案预检，不签发执行凭据");
    expect(screen.queryByText("预算不足，无法提交")).not.toBeInTheDocument();
    expect(screen.queryByText("操作明细未记录")).not.toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "计划步骤明细" })).not.toBeInTheDocument();
    rerender(<ActionResults entry={previewEntry({ ok: true, operation_count: 6, changes, plan_id: "accepted", plan_entities: { adventurers: [{ id: 9, name: "方案法师" }], monsters: [{ id: 10, name: "方案巨蛛" }] } })} />);
    const inspection = screen.getByRole("region", { name: "排查详情" });
    const projection = within(inspection).getByText("方案投影编号（仅用于该方案讨伐预览）");
    expect(projection.closest("details")).not.toHaveAttribute("open");
    expect(screen.queryByText("方案巨蛛")).not.toBeInTheDocument();
    fireEvent.click(projection);
    expect(await within(inspection).findByText("方案巨蛛")).toBeVisible();
    expect(within(inspection).getByText("输入参数", { selector: "summary" }).closest("details")).not.toHaveAttribute("open");
});

it("默认展示前六步、超过六步按需展开并继续编号，别名定义与引用保留", () => {
    const planned = [...operations,
        { tool: "configure_tactics", arguments: { adventurer_id: 2, skill_priority: [], disabled_skills: [] } },
        { tool: "configure_tactics", arguments: { adventurer_id: 2, skill_priority: ["touch", "spread"], disabled_skills: [] } },
    ];
    render(<ActionResults entry={previewEntry({ ok: true, operation_count: 8, changes, _llm_refs: refs }, planned)} />);
    const steps = screen.getByRole("region", { name: "计划步骤明细" });
    const rows = within(steps).getAllByRole("listitem");
    expect(rows).toHaveLength(8);
    rows.slice(0, 6).forEach(row => expect(row).toBeVisible());
    rows.slice(6).forEach(row => expect(row).not.toBeVisible());
    expect(within(steps).getByText("$armor", { exact: true })).toBeVisible();
    expect(rows[5]).toHaveTextContent("方案装备 $armor");
    const more = within(steps).getByText("其余 2 步");
    expect(more.closest("details")).not.toHaveAttribute("open");
    fireEvent.click(more);
    rows.forEach(row => expect(row).toBeVisible());
    expect(rows[6].parentElement).toHaveAttribute("start", "7");
});

it("新增资产按类别展示并隐藏投影内部后缀，完整记录仍可查看", () => {
    const projected = [...changes,
        { kind: "adventurer", label: "新冒险者", after: "烈焰法师 (试算角色3)" },
        { kind: "equipment", label: "获得装备", after: "赤焰法杖 (试算装备1)" },
        { kind: "upgrade", label: "解锁加成", after: "奥术训练" },
    ];
    render(<ActionResults entry={previewEntry({ ok: true, operation_count: 6, changes: projected })} />);
    const assets = screen.getByRole("region", { name: "预计资产变化" });
    expect(assets).toHaveTextContent("新增成员烈焰法师");
    expect(assets).toHaveTextContent("获得装备赤焰法杖");
    expect(assets).toHaveTextContent("解锁天赋奥术训练");
    expect(assets).not.toHaveTextContent("试算角色3");
    expect(assets).not.toHaveTextContent("试算装备1");
    fireEvent.click(screen.getByText("完整预计变化（含属性前后值）"));
    expect(screen.getByRole("region", { name: "排查详情" })).toHaveTextContent("赤焰法杖 (试算装备1)");
});

it("仅去掉成员槽位已经完整覆盖的装备归属变化，未覆盖项仍保留", () => {
    const action = actionEntries(watchFrames([initial, execution])).at(-1)!;
    const { rerender } = render(<ActionResults entry={action} />);
    expect(screen.queryByRole("region", { name: "其他对象变化" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByText("完整实际变化（含属性前后值）"));
    expect(screen.getByRole("region", { name: "排查详情" })).toHaveTextContent("秘银护甲 装备者");
    const result = { ...execution.event.result, events: [...events, { action: { type: "unequip", adventurer_id: "mage", slot: "accessory" }, changes: [{ kind: "equipment", label: "护身符 装备者", before: "mage", after: "未装备" }] }] };
    const other = actionEntries(watchFrames([initial, record(4, { ...execution.event, result })])).at(-1)!;
    rerender(<ActionResults entry={other} />);
    expect(screen.getByRole("region", { name: "其他对象变化" })).toHaveTextContent("护身符 装备者");
});

it("执行详情按同凭据定位对应试算，跳转清除工具筛选，不选最近的无关试算", () => {
    const records = [initial,
        record(2, { ...preview.event, arguments: { operations }, result: { ...preview.event.result, plan_id: "correct", current_rank_score: 60, projected_rank_score: 65, rank_score_delta: 5 } }),
        record(3, { ...preview.event, arguments: { operations }, result: { ...preview.event.result, plan_id: "unrelated", current_rank_score: 60, projected_rank_score: 66, rank_score_delta: 6 } }),
        record(4, { ...execution.event, arguments: { plan_id: "unrelated" }, result: { ...execution.event.result, plan_id: "correct" } }),
    ];
    const original = JSON.stringify(records);
    const frames = watchFrames(records);
    const onFrame = vi.fn();
    const { rerender } = render(<ActionTimeline frames={frames} frame={frames.at(-1)} active following={false} rolledBack={false} onFrame={onFrame} renderDetails={() => null} />);
    fireEvent.change(screen.getByLabelText("筛选本回合工具"), { target: { value: "execute_plan" } });
    expect(screen.queryByRole("button", { name: "定位行动 方案试算" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "查看对应试算" }));
    expect(onFrame).toHaveBeenCalledWith(frames.find(frame => frame.cursor === 2));
    expect(screen.getByLabelText("筛选本回合工具")).toHaveValue("all");
    rerender(<ActionTimeline frames={frames} frame={frames.find(frame => frame.cursor === 2)} active following={false} rolledBack={false} onFrame={onFrame} renderDetails={() => null} />);
    expect(screen.getByRole("region", { name: "正式战力试算" })).toHaveTextContent("65.00");
    expect(JSON.stringify(records)).toBe(original);
});

it("缺少凭据、试算失败、只有之后的试算或跨回合回滚边界时不伪造关联", () => {
    const successfulPreview = record(2, { ...preview.event, result: { ...preview.event.result, plan_id: "receipt" } });
    const entries = actionEntries(watchFrames([initial, successfulPreview, execution]));
    expect(findPlanPreview(entries.at(-1)!, entries)?.frame.cursor).toBe(2);
    for (const boundary of [record(3, { type: "turn_started", observation: { ...before, turn: 11 } }), record(3, { type: "turn_retry" }), record(3, { type: "run_resumed", restored_observation: before })]) {
        const divided = actionEntries(watchFrames([initial, successfulPreview, boundary, execution]));
        expect(findPlanPreview(divided.at(-1)!, divided)).toBeUndefined();
    }
    const noPreview = actionEntries(watchFrames([initial, execution]));
    render(<ActionTimeline frames={noPreview.map(entry => entry.frame)} frame={noPreview.at(-1)!.frame} active following={false} rolledBack={false} onFrame={vi.fn()} renderDetails={() => null} />);
    expect(screen.queryByRole("button", { name: "查看对应试算" })).not.toBeInTheDocument();
    expect(screen.getByText("对应试算未记录")).toBeVisible();
    const failed = actionEntries(watchFrames([initial, record(2, { ...successfulPreview.event, result: { ok: false, plan_id: "receipt" } }), execution]));
    expect(findPlanPreview(failed.at(-1)!, failed)).toBeUndefined();
    const future = actionEntries(watchFrames([initial, execution, record(5, successfulPreview.event)]));
    expect(findPlanPreview(future.find(entry => entry.event.name === "execute_plan")!, future)).toBeUndefined();
    const noId = { ...entries.at(-1)!, event: { ...execution.event, arguments: {}, result: { ok: true } } };
    expect(findPlanPreview(noId, entries)).toBeUndefined();
});

it("执行拒绝突出预算与失败原因，携带步骤也不展示成部分成功", () => {
    const rejected = actionEntries(watchFrames([initial, record(4, { ...execution.event, result: { ok: false, required_calls: 6, remaining_calls: 5, error: "剩余调用次数不足，方案未执行", events } })])).at(-1)!;
    render(<ActionResults entry={rejected} />);
    expect(screen.getByRole("region", { name: "执行结果" })).toHaveTextContent("剩余调用次数不足，方案未执行");
    expect(screen.getByRole("region", { name: "执行结果" })).toHaveTextContent("需余额 ≥6 次 / 当前剩 5 次");
    expect(screen.queryByRole("region", { name: "实际资源变化" })).not.toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "已执行步骤明细" })).not.toBeInTheDocument();
});

it("先展示完整的操作区再展示变化，资源、成员、对象和正式战力都不插在步骤前", () => {
    const actual = actionEntries(watchFrames([initial, execution])).at(-1)!;
    const trial = previewEntry({ ok: true, operation_count: 6, changes: [...changes, { kind: "equipment", label: "获得装备", after: "秘银护甲 (试算装备1)" }], current_rank_score: 60, projected_rank_score: 65, rank_score_delta: 5 });
    const { rerender } = render(<ActionResults entry={actual} />);
    for (const action of [actual, trial]) {
        rerender(<ActionResults entry={action} />);
        const steps = screen.getByRole("region", { name: action.event.name === "execute_plan" ? "已执行步骤明细" : "计划步骤明细" });
        const changes = screen.getByRole("region", { name: action.event.name === "execute_plan" ? "方案实际变化" : "方案预计变化" });
        const inspection = screen.getByRole("region", { name: "排查详情" });
        expect(steps.compareDocumentPosition(changes) & Node.DOCUMENT_POSITION_FOLLOWING).not.toBe(0);
        expect(changes.compareDocumentPosition(inspection) & Node.DOCUMENT_POSITION_FOLLOWING).not.toBe(0);
        expect(changes).toContainElement(screen.getByRole("region", { name: action.event.name === "execute_plan" ? "实际资源变化" : "预计资源变化" }));
        expect(changes).toContainElement(screen.getByRole("region", { name: action.event.name === "execute_plan" ? "实际成员变化" : "预计成员变化" }));
        expect(changes).toContainElement(screen.getByRole("region", { name: action.event.name === "execute_plan" ? "实际资产变化" : "预计资产变化" }));
        if (action.event.name === "preview_team_power") expect(changes).toContainElement(screen.getByRole("region", { name: "正式战力试算" }));
    }
});

it("成员经验跨级结转单列说明，不将升级后的经验余额下降标为损失", () => {
    const action = actionEntries(watchFrames([initial, record(4, { type: "tool_result", name: "execute_plan", result: { ok: true, operation_count: 1, events: [{
        action: { type: "allocate_experience", adventurer_id: "knight", amount: 19 }, changes: [
            { kind: "adventurer", label: "步行骑士 等级", before: 6, after: 7 },
            { kind: "adventurer", label: "步行骑士 经验", before: 85, after: 4 },
        ],
    }] } })])).at(-1)!;
    render(<ActionResults entry={action} />);
    const member = screen.getByRole("article", { name: "步行骑士" });
    const row = within(member).getByRole("row", { name: /经验进度/ });
    expect(within(row).getAllByRole("cell").map(cell => cell.textContent)).toEqual(["85", "4", "跨级结转"]);
    expect(within(row).getByText("跨级结转")).not.toHaveClass("is-loss");
});
