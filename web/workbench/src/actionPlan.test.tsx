import { fireEvent, render, screen, within } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { ActionTimeline } from "./ActionTimeline";
import { ActionResults } from "./ActionResultPanel";
import { actionEntries } from "./actionModel";
import { describePlan } from "./actionPlan";
import { watchFrames } from "./watchModel";
import type { Observation } from "./types";
import type { WatchRecord } from "./watchModel";

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

it("六步方案按资源和成员展示，展开全部中文步骤不会触发定位，选择仍落在真实结果帧", () => {
    const records = [initial, record(2, { type: "tool_call", name: "preview_team_power", call_id: "preview", arguments: { operations } }), preview, execution];
    const original = JSON.stringify(records);
    const frames = watchFrames(records);
    const onFrame = vi.fn();
    render(<ActionTimeline frames={frames} frame={frames.at(-1)} active following={false} rolledBack={false} onFrame={onFrame} renderDetails={() => null} />);
    const timeline = screen.getByRole("region", { name: "观战行动时间线" });
    const previewRow = within(timeline).getByRole("button", { name: "定位行动 方案试算" }).closest("article")!;
    expect(within(previewRow).getByRole("button", { name: "定位行动 方案试算" })).toHaveTextContent("预览");
    expect(within(previewRow).getByRole("region", { name: "方案操作概要" })).toHaveTextContent("分配经验 ×3");
    expect(within(previewRow).getByRole("region", { name: "预计资源变化" })).toHaveTextContent("金币-145");
    expect(within(previewRow).queryByRole("region", { name: "实际资源变化" })).not.toBeInTheDocument();
    const actual = within(timeline).getByRole("button", { name: "定位行动 执行方案" });
    const row = actual.closest("article")!;
    const resources = within(row).getByRole("region", { name: "实际资源变化" });
    expect(resources).toHaveTextContent("经验池-930");
    expect(within(resources).getAllByText("秘银碎片", { exact: true })).toHaveLength(1);
    const members = within(row).getByRole("region", { name: "实际成员变化" });
    expect(within(members).getAllByRole("row")).toHaveLength(3);
    const knight = within(members).getByRole("row", { name: /步行骑士/ });
    expect(knight).toHaveTextContent("6 → 8");
    expect(knight).toHaveTextContent("生命上限+90");
    expect(knight).toHaveTextContent("防御+23");
    expect(within(knight).getAllByText("生命上限", { exact: true })).toHaveLength(1);
    expect(members).toHaveTextContent("技能顺序默认顺序 → 枯萎之触 → 疫病蔓延");
    expect(members).toHaveTextContent("装备护甲：空槽位 → 护甲：秘银护甲");
    expect(members).not.toHaveTextContent("undefined");
    const steps = within(previewRow).getByText("6 步计划操作明细").closest("details")!;
    expect(steps).not.toHaveAttribute("open");
    fireEvent.click(within(steps).getByText("6 步计划操作明细"));
    expect(steps).toHaveAttribute("open");
    expect(within(steps).getAllByRole("listitem")).toHaveLength(6);
    expect(steps).toHaveTextContent("给 瘟疫法师·坚壁 分配 345经验");
    expect(steps).toHaveTextContent("枯萎之触");
    expect(steps).toHaveTextContent("方案装备 $armor");
    expect(steps).not.toHaveTextContent("tool：");
    expect(onFrame).not.toHaveBeenCalled();
    fireEvent.click(actual);
    expect(onFrame).toHaveBeenCalledWith(frames.at(-1));
    expect(JSON.stringify(records)).toBe(original);
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
    expect(screen.getByRole("region", { name: "执行结果" })).toHaveTextContent("重复提交，未再次执行");
    expect(screen.queryByRole("region", { name: "实际资源变化" })).not.toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "实际成员变化" })).not.toBeInTheDocument();
    expect(screen.queryByText("6 步已执行操作明细")).not.toBeInTheDocument();
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
