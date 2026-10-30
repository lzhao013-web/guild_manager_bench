import { useState } from "react";
import { Theme } from "@radix-ui/themes";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { afterAll, beforeAll, expect, it, vi } from "vitest";
import { RunWorkspace } from "./RunWorkspace";
import type { RunModule } from "./sceneNavigation";
import type { Observation } from "./types";
import { watchFrames } from "./watchModel";
import type { WatchFrame, WatchRecord } from "./watchModel";

const member = {
    adventurer_id: "one",
    name: "总览验收卫士",
    template_id: "foot_knight",
    level: 3,
    resources: { current_hp: 72, current_mp: 9 },
    effective_stats: { hp: 100, mp: 20, attack: 14, defense: 8 },
    equipment_slots: [{ slot: "main_hand", item: null, blocked_by: null }],
    skills: [],
};
const observation: Observation = {
    turn: 4,
    max_turns: 35,
    gold: 250,
    experience_pool: 300,
    materials: {},
    party_size_limit: 6,
    adventurers: [member],
    monsters: [
        {
            monster_id: "enemy",
            name: "总览验收怪物",
            archetype_id: "goblin",
            tier: "normal",
            stats: { hp: 90 },
            skills: [],
        },
    ],
    equipment_inventory: [
        {
            instance_id: "sword",
            name: "总览验收剑",
            slot: "main_hand",
            equipped_by: "one",
            stats: { attack: 4 },
            skills: [],
        },
        {
            instance_id: "boots",
            name: "总览验收靴",
            slot: "boots",
            equipped_by: null,
            stats: { speed: 3 },
            skills: [],
        },
    ],
    recruit_candidates: [
        {
            candidate_id: "candidate",
            name: "总览验收游侠",
            template_id: "woodland_archer",
            recruit_gold: 75,
            can_recruit: true,
            base_stats: { hp: 80 },
            skills: [],
        },
    ],
    global_upgrades: [],
};
const records: WatchRecord[] = [
    {
        at: "2026-10-18T10:00:00Z",
        after: 0,
        cursor: 1,
        event: { type: "model_request", turn: 4, step: 1 },
    },
    {
        at: "2026-10-18T10:00:01Z",
        after: 1,
        cursor: 2,
        event: {
            type: "model_response",
            turn: 4,
            step: 1,
            text: "构造的模型正文，不代表实际模型决策。",
        },
    },
];
beforeAll(() => vi.stubGlobal("ResizeObserver", class { observe() {} disconnect() {} }));
afterAll(() => vi.unstubAllGlobals());

function Harness({ value = observation, frames = [], initialModule = null }: { value?: Observation; frames?: WatchFrame[]; initialModule?: RunModule | null }) {
    const [activeModule, setModule] = useState<RunModule | null>(initialModule);
    const [modelTab, setModelTab] = useState<"output" | "memo">("output");
    return (
        <Theme accentColor="teal">
            <RunWorkspace
                observation={value}
                before={null}
                frames={frames}
                records={records}
                memo={{ state: "unavailable" }} modelTab={modelTab} onModelTab={setModelTab}
                activeModule={activeModule}
                onModule={setModule}
                onFrame={() => {}}
                actions={<p>完整行动模块</p>}
                models={<p>完整模型模块</p>}
            />
        </Theme>
    );
}

it("默认同时展示七个模块摘要，展开只占当前工作区，不产生对话框", () => {
    render(<Harness />);
    for (const name of [
        "编队概览",
        "本回合怪物概览",
        "装备库概览",
        "招募候选概览",
        "公会发展概览",
        "最近行动概览",
        "模型输出概览",
    ])
        expect(screen.getByRole("region", { name })).toBeVisible();
    expect(screen.getByText("构造的模型正文，不代表实际模型决策。")).toBeVisible();
    const trigger = screen.getByRole("button", { name: "展开编队" });
    trigger.focus();
    fireEvent.click(trigger);
    expect(screen.getByRole("heading", { name: "编队模块标题" })).toHaveFocus();
    expect(screen.getByRole("region", { name: "编队模块" })).toBeVisible();
    expect(screen.getByRole("region", { name: "总览验收卫士 的详情" })).toBeVisible();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "装备库概览" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "返回总览" }));
    expect(screen.getByRole("region", { name: "装备库概览" })).toBeVisible();
    expect(screen.getByRole("region", { name: "编队概览" })).toHaveClass("is-return-target");
    expect(trigger).toHaveFocus();
});

it("空摘要保留展开入口，旧快照未记录不冒充空清单，回看后内容恢复", () => {
    const view = render(<Harness value={{ ...observation, equipment_inventory: [] }} />);
    const inventory = () => screen.getByRole("region", { name: "装备库概览" });
    expect(inventory()).toHaveTextContent("此时仓库里没有装备");
    const trigger = within(inventory()).getByRole("button", { name: "展开装备库" });
    trigger.focus();
    fireEvent.click(trigger);
    expect(screen.getByRole("region", { name: "装备库模块" })).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "返回总览" }));
    expect(trigger).toHaveFocus();
    view.rerender(<Harness value={{ ...observation, equipment_inventory: undefined }} />);
    expect(inventory()).toHaveTextContent("该快照未记录库存");
    expect(inventory()).not.toHaveTextContent("此时仓库里没有装备");
    view.rerender(<Harness />);
    expect(within(inventory()).getByRole("button", { name: "查看装备 总览验收靴" })).toBeVisible();
});

it("终局收起怪物和候选，回看结算前按历史快照恢复，返回焦点仍可用", () => {
    const finished = { ...observation, turn: 36, finished: true, monsters: [], recruit_candidates: [] };
    const view = render(<Harness value={finished} />);
    expect(screen.queryByRole("region", { name: "本回合怪物概览" })).not.toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "招募候选概览" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "查看成员 总览验收卫士" })).toBeVisible();
    expect(screen.getByRole("region", { name: "装备库概览" })).toBeVisible();
    expect(document.querySelector(".overview-grid")).toHaveClass("is-finished");
    view.rerender(<Harness />);
    expect(screen.getByRole("button", { name: "查看怪物 总览验收怪物" })).toBeVisible();
    expect(screen.getByRole("button", { name: "查看招募候选 总览验收游侠" })).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "展开本回合怪物" }));
    view.rerender(<Harness value={finished} />);
    fireEvent.click(screen.getByRole("button", { name: "返回总览" }));
    expect(screen.getByRole("button", { name: "展开编队" })).toHaveFocus();
});

it("空间充足时显示四位成员、十个怪物、三位候选和五项可解锁升级", () => {
    vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(() => new DOMRect(0, 0, 750, 180));
    render(<Harness value={{ ...observation,
        adventurers: Array.from({ length: 4 }, (_, i) => ({ ...member, adventurer_id: `member-${i}`, name: `成员 ${i}` })),
        monsters: Array.from({ length: 10 }, (_, i) => ({ ...observation.monsters![0], monster_id: `monster-${i}`, name: `怪物 ${i}` })),
        recruit_candidates: Array.from({ length: 3 }, (_, i) => ({ ...observation.recruit_candidates![0], candidate_id: `candidate-${i}`, name: `候选 ${i}` })),
        global_upgrades: Array.from({ length: 5 }, (_, i) => ({ ...observation.global_upgrades![0], upgrade_id: `upgrade-${i}`, name: `升级 ${i}`, unlocked: false, can_purchase: true })),
    }} />);
    expect(document.querySelector(".overview-party-grid")).toHaveAttribute("data-members", "4");
    expect(screen.getAllByRole("button", { name: /^查看成员 成员/ })).toHaveLength(4);
    expect(screen.getAllByRole("button", { name: /^查看怪物 怪物/ })).toHaveLength(10);
    expect(screen.getAllByRole("button", { name: /^查看招募候选 候选/ })).toHaveLength(3);
    expect(screen.getAllByRole("button", { name: /^查看升级 升级/ })).toHaveLength(5);
});

it("最近行动的回合结算只显示一次战斗统计，明确零场仍显示为零", () => {
    const events: WatchRecord[] = [{ at: records[0].at, after: 0, cursor: 1,
        event: { type: "tool_result", name: "end_turn", turn: 35, result: { ok: true,
            event: { summary: "结束第 35 回合：0 场战斗，0 胜 0 负" }, turn_result: { battles: [] } } } }];
    render(<Harness frames={watchFrames(events)} />);
    const actions = screen.getByRole("region", { name: "最近行动概览" });
    expect(within(actions).getByText("结束回合", { exact: true })).toBeVisible();
    expect(within(actions).getAllByText("0 场，0 胜 0 负", { exact: true })).toHaveLength(1);
    expect(within(actions).queryByText(/结束第 35 回合/)).not.toBeInTheDocument();
});

it("返回总览再打开模块，保留装备筛选，不要求用户重新查找", () => {
    render(<Harness />);
    fireEvent.click(screen.getByRole("button", { name: "展开装备库" }));
    fireEvent.change(screen.getByLabelText("搜索装备"), { target: { value: "靴" } });
    fireEvent.change(screen.getByLabelText("筛选穿戴状态"), { target: { value: "stored" } });
    expect(screen.getByRole("button", { name: "查看装备 总览验收靴" })).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "返回总览" }));
    fireEvent.click(screen.getByRole("button", { name: "展开装备库" }));
    expect(screen.getByLabelText("搜索装备")).toHaveValue("靴");
    expect(screen.getByLabelText("筛选穿戴状态")).toHaveValue("stored");
    expect(screen.queryByRole("button", { name: "查看装备 总览验收剑" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "查看装备 总览验收靴" }));
    const picker = screen.getByRole("combobox", { name: "切换查看对象" });
    expect(within(picker).getAllByRole("option")).toHaveLength(1);
    expect(within(picker).getByRole("option", { name: "总览验收靴" })).toBeVisible();
    expect(screen.getByRole("button", { name: "查看下一个对象" })).toBeDisabled();
});

it("总览中的对象可直接展开详情，穿戴者链接跨模块定位而不打开弹窗", () => {
    render(<Harness />);
    fireEvent.click(screen.getByRole("button", { name: "查看装备 总览验收剑" }));
    const detail = screen.getByRole("region", { name: "总览验收剑 的详情" });
    fireEvent.click(within(detail).getByRole("button", { name: /由 总览验收卫士 穿戴/ }));
    expect(screen.getByRole("region", { name: "编队模块" })).toBeVisible();
    expect(screen.getByRole("region", { name: "总览验收卫士 的详情" })).toBeVisible();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
});

it("切换观看快照后，已挂载模块与总览一起更新，不保留未来角色", () => {
    const view = render(<Harness />);
    fireEvent.click(screen.getByRole("button", { name: "查看成员 总览验收卫士" }));
    view.rerender(<Harness value={{ ...observation, adventurers: [], equipment_inventory: [] }} />);
    expect(screen.queryByRole("region", { name: "总览验收卫士 的详情" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "返回总览" }));
    expect(
        within(screen.getByRole("region", { name: "编队概览" })).getByText("此时尚未招募冒险者"),
    ).toBeVisible();
    expect(screen.queryByRole("button", { name: "查看成员 总览验收卫士" })).not.toBeInTheDocument();
});

it("总览优先展示改变局面的行动和失败，而不是被连续查询刷走", () => {
    const events: WatchRecord[] = [
        { at: records[0].at, after: 0, cursor: 1, event: { type: "turn_started", observation } },
        { at: records[0].at, after: 1, cursor: 2, event: { type: "tool_result", name: "allocate_experience", result: { ok: true, _observation_after: { ...observation, gold: 200 } } } },
        { at: records[0].at, after: 2, cursor: 3, event: { type: "tool_result", name: "purchase_upgrade", result: { ok: false, error: "构造失败：金币不足" } } },
        ...Array.from({ length: 12 }, (_, i) => ({ at: records[0].at, after: i + 3, cursor: i + 4, event: { type: "tool_result", name: "get_party", content: "已读取队伍", result: { ok: true } } })),
    ];
    render(<Harness frames={watchFrames(events)} />);
    const actions = screen.getByRole("region", { name: "最近行动概览" });
    expect(within(actions).getByText("分配经验")).toBeVisible();
    expect(within(actions).getByText("购买升级")).toBeVisible();
    expect(within(actions).getByText("构造失败：金币不足")).toBeVisible();
    expect(within(actions).queryByText("查看队伍")).not.toBeInTheDocument();
    expect(actions).toHaveTextContent("12 次查询");
});

it("按卡片空间展示更多条目，闲置装备和可招募候选优先，其余可展开", () => {
    vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(function (this: HTMLElement) {
        return this.classList.contains("overview-items") ? new DOMRect(0, 0, 450, 180) : new DOMRect();
    });
    const value: Observation = {
        ...observation,
        equipment_inventory: [
            observation.equipment_inventory![1],
            ...Array.from({ length: 15 }, (_, i) => ({ ...observation.equipment_inventory![0], instance_id: `sword-${i}`, name: `构造已穿戴剑 ${i}` })),
        ],
        recruit_candidates: [
            { ...observation.recruit_candidates![0], candidate_id: "locked", name: "构造不可招募者", can_recruit: false },
            ...Array.from({ length: 4 }, (_, i) => ({ ...observation.recruit_candidates![0], candidate_id: `candidate-${i}`, name: `构造可招募者 ${i}` })),
        ],
    };
    render(<Harness value={value} />);
    const inventory = screen.getByRole("region", { name: "装备库概览" });
    expect(within(inventory).getByRole("button", { name: "查看装备 总览验收靴" })).toBeVisible();
    expect(within(inventory).getAllByRole("button", { name: /^查看装备 / })).toHaveLength(10);
    const recruits = screen.getByRole("region", { name: "招募候选概览" });
    expect(within(recruits).getAllByRole("button", { name: /^查看招募候选 / })).toHaveLength(4);
    expect(within(recruits).queryByRole("button", { name: "查看招募候选 构造不可招募者" })).not.toBeInTheDocument();
    fireEvent.click(within(recruits).getByRole("button", { name: "展开招募候选" }));
    expect(screen.getByRole("button", { name: "查看招募候选 构造不可招募者" })).toBeVisible();
});

it("生命偏低的成员在摘要中提前，零生命不当成缺失值", () => {
    render(<Harness value={{ ...observation, adventurers: [
        { ...member, adventurer_id: "healthy", name: "构造健康队员", resources: { current_hp: 100, current_mp: 9 } },
        { ...member, adventurer_id: "zero", name: "构造零生命队员", resources: { current_hp: 0, current_mp: 0 } },
    ] }} />);
    const party = screen.getByRole("region", { name: "编队概览" });
    expect(within(party).getAllByRole("button", { name: /^查看成员 / })[0]).toHaveAccessibleName("查看成员 构造零生命队员");
    expect(within(party).getByText("2 / 6，1 位生命偏低")).toBeVisible();
    expect(within(party).getAllByRole("meter", { name: "生命" })[0]).toHaveAttribute("aria-valuenow", "0");
});

it("成员详情直接选择任意对象，不再需要滚动横向对象栏", () => {
    render(<Harness value={{ ...observation, adventurers: [member, { ...member, adventurer_id: "two", name: "构造第二成员" }] }} />);
    fireEvent.click(screen.getByRole("button", { name: "展开编队" }));
    fireEvent.change(screen.getByLabelText("切换查看对象"), { target: { value: "two" } });
    expect(screen.getByRole("region", { name: "构造第二成员 的详情" })).toBeVisible();
    expect(screen.getByRole("combobox", { name: "切换查看对象" })).toHaveValue("two");
});

it("从已展开模块恢复工作区后，返回总览不会丢失天赋搜索和缩放", () => {
    const value = { ...observation, global_upgrades: [{ upgrade_id: "forge", name: "构造铁匠铺", required_upgrade_ids: [], unlocked: false, can_purchase: true, gold_cost: 50, missing: {}, stats: {}, skills: [] }] };
    render(<Harness value={value} initialModule="upgrades" />);
    fireEvent.change(screen.getByLabelText("搜索天赋"), { target: { value: "铁匠" } });
    fireEvent.click(screen.getByRole("button", { name: "放大天赋图" }));
    fireEvent.click(screen.getByRole("button", { name: "返回总览" }));
    fireEvent.click(screen.getByRole("button", { name: "展开公会发展" }));
    expect(screen.getByLabelText("搜索天赋")).toHaveValue("铁匠");
    expect(screen.getByRole("button", { name: "天赋图原始大小" })).toHaveTextContent("110%");
});

it("外部轨迹定位进入行动后，往返模型模块仍保留检索条件", () => {
    const content = (activeModule: RunModule) => <Theme>
        <RunWorkspace observation={observation} before={null} frames={[]} records={records}
            memo={{ state: "unavailable" }} modelTab="output" onModelTab={() => {}}
            activeModule={activeModule} onModule={() => {}} onFrame={() => {}}
            actions={<label>整局检索条件<input aria-label="整局检索条件" defaultValue="" /></label>}
            models={<p>对应模型调用</p>} />
    </Theme>;
    const view = render(content("inventory"));
    // 对象轨迹与工具跳转通过受控属性切换模块，不经过工具栏的 open。
    view.rerender(content("actions"));
    fireEvent.change(screen.getByLabelText("整局检索条件"), { target: { value: "训练场" } });
    view.rerender(content("models"));
    expect(screen.getByText("对应模型调用")).toBeVisible();
    view.rerender(content("actions"));
    expect(screen.getByLabelText("整局检索条件")).toHaveValue("训练场");
});

it("行动和模型也在同一工作区展开，模块之间可直接切换", () => {
    render(<Harness />);
    fireEvent.click(screen.getByRole("button", { name: "展开最近行动" }));
    expect(screen.getByText("完整行动模块")).toBeVisible();
    fireEvent.change(screen.getByLabelText("切换展开模块"), { target: { value: "models" } });
    expect(screen.getByText("完整模型模块")).toBeVisible();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
});
