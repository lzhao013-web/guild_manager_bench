import { useState } from "react";
import { Theme } from "@radix-ui/themes";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { GameScene as Scene } from "./GameScene";
import { MODULE_NAMES, emptySceneView, openSceneView } from "./sceneNavigation";
import type { SceneModule, SceneViews } from "./sceneNavigation";
import type { Selection } from "./sceneModel";
import { SceneMaterials } from "./SceneMaterials";
import { testRecipe } from "./test-recipes";
import { conditionText, effectText, upgradeDepths } from "./sceneModel";
import type { Observation, PartyMember } from "./types";
import type { RankContributions } from "./watchModel";

const weapon = {
    instance_id: "sword-1",
    template_id: "iron_sword",
    name: "沉铁长剑",
    slot: "main_hand",
    stats: { attack: 12, defense: 0 },
    skills: [],
    equipped_by: "knight",
    allowed_class_names: ["重甲卫士"],
};
const member: PartyMember = {
    adventurer_id: "knight",
    template_id: "foot_knight",
    name: "验收重甲卫士",
    level: 8,
    resources: { current_hp: 35, current_mp: 0 },
    effective_stats: { hp: 120, mp: 0, attack: 22, defense: 16, speed: 7 },
    base_stats: { hp: 90, mp: 0, attack: 10, defense: 12, speed: 7 },
    equipment_slots: [
        { slot: "main_hand", item: weapon, blocked_by: null },
        { slot: "off_hand", item: null, blocked_by: "two_hand" },
        { slot: "helmet", item: null, blocked_by: null },
    ],
    skills: [
        {
            skill_id: "steady",
            name: "稳固阵线",
            kind: "passive",
            mp_cost: 0,
            free: false,
            once_per_battle: false,
            condition: { type: "always", value: null, conditions: [] },
            effects: [
                { type: "stat_bonus", stat: "defense", value: 4, target: "self", status: null },
            ],
        },
    ],
};
const observation: Observation = {
    turn: 9,
    max_turns: 35,
    gold: 200,
    experience_pool: 600,
    materials: { iron_ore: 8 },
    party_size_limit: 6,
    adventurers: [member],
    monsters: [
        {
            monster_id: "orc-1",
            archetype_id: "orc_warrior",
            name: "兽人战士",
            tier: "elite",
            stats: { hp: 150, attack: 20, defense: 10 },
            skills: [],
            reward: { gold: 25, experience: 12 },
        },
    ],
    equipment_inventory: [
        weapon,
        {
            instance_id: "boot-1",
            name: "旅者皮靴",
            slot: "boots",
            stats: { speed: 3 },
            equipped_by: null,
            skills: [],
        },
    ],
    recruit_candidates: [
        {
            candidate_id: "recruit-1",
            name: "林地游侠",
            template_id: "woodland_archer",
            recruit_gold: 70,
            can_recruit: true,
            base_stats: { hp: 100, attack: 12 },
            skills: [],
            missing: {},
        },
    ],
    global_upgrades: [
        {
            upgrade_id: "forge",
            name: "铁匠铺",
            gold_cost: 80,
            unlocked: true,
            can_purchase: false,
            required_upgrade_ids: [],
            stats: { attack: 4 },
            skills: [],
            missing: {},
        },
        {
            upgrade_id: "armory",
            name: "军械库",
            gold_cost: 240,
            unlocked: false,
            can_purchase: false,
            required_upgrade_ids: ["forge"],
            stats: { defense: 8 },
            skills: [],
            missing: { gold: 40 },
        },
    ],
};
// 驱动受控的单局模块，不再通过已经移除的多路详情包装器测试它。
function GameScene({ observation, contributions = null }: { observation: Observation; contributions?: RankContributions | null }) {
    const [module, setModule] = useState<SceneModule>("party");
    const [views, setViews] = useState<SceneViews>({});
    function navigate(next: SceneModule, selection?: Selection, slot?: string) {
        setViews(previous => openSceneView(previous, next, observation, selection, slot));
        setModule(next);
    }
    return <>
        <nav aria-label="测试模块选择">
            {(["party", "monsters", "inventory", "recruitment", "upgrades"] as const).map(key =>
                <button key={key} onClick={() => navigate(key)}>{MODULE_NAMES[key]}</button>)}
        </nav>
        <Scene observation={observation} contributions={contributions} module={module} view={views[module] || emptySceneView}
            onView={view => setViews(previous => ({ ...previous, [module]: view }))} onNavigate={navigate} />
    </>;
}
function mount(value: Observation = observation, contributions: RankContributions | null = null) {
    return render(
        <Theme accentColor="teal">
            <GameScene observation={value} contributions={contributions} />
        </Theme>,
    );
}

it("成员和招募详情不重复显示固定挑战职业资格，即使旧记录有该字段", async () => {
    const savedMember = { ...member, challenge_class_available: false };
    mount({ ...observation, adventurers: [savedMember],
        recruit_candidates: observation.recruit_candidates!.map(candidate =>
            ({ ...candidate, challenge_class_available: false })) });
    fireEvent.click(screen.getByRole("button", { name: "查看成员 验收重甲卫士" }));
    const memberDetail = await screen.findByRole("region", { name: "验收重甲卫士 的详情" });
    expect(within(memberDetail).queryByText(/固定挑战职业资格/)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "招募" }));
    fireEvent.click(screen.getByRole("button", { name: "查看招募候选 林地游侠" }));
    const candidateDetail = await screen.findByRole("region", { name: "林地游侠 的详情" });
    expect(within(candidateDetail).queryByText(/固定挑战职业资格/)).not.toBeInTheDocument();
    expect(within(candidateDetail).getByText("当前满足招募条件")).toBeVisible();
});

it.each([
    { effective_stats: { hp: 145, attack: 27 }, hp: "145", attack: "27", title: "Lv1入队面板", note: "含公会直接属性加成，条件效果在战斗中生效。" },
    { effective_stats: undefined, hp: "100", attack: "12", title: "初始基础属性", note: null },
])("招募卡和详情使用记录的面板口径：$title", async ({ effective_stats, hp, attack, title, note }) => {
    mount({ ...observation, recruit_candidates: observation.recruit_candidates!.map(candidate => ({ ...candidate, effective_stats })) });
    fireEvent.click(screen.getByRole("button", { name: "招募" }));
    const card = screen.getByRole("button", { name: "查看招募候选 林地游侠" });
    expect(within(card).getByText(`生命上限 ${hp}`)).toBeVisible();
    expect(within(card).getByText(`攻击 ${attack}`)).toBeVisible();
    fireEvent.click(card);
    const detail = await screen.findByRole("region", { name: "林地游侠 的详情" });
    expect(within(detail).getByRole("heading", { name: title })).toBeVisible();
    expect(within(detail).getByText(hp)).toBeVisible();
    expect(within(detail).getByText(attack)).toBeVisible();
    if (note) expect(within(detail).getByText(note)).toBeVisible();
    else expect(within(detail).queryByText(/含公会直接属性加成/)).not.toBeInTheDocument();
});

it("编队卡和详情显示结算贡献，聚焦时说明来源回合及非实时口径", async () => {
    mount(observation, { turn: 8, scores: { knight: 123.4 } });
    const card = screen.getByRole("button", { name: "查看成员 验收重甲卫士" });
    expect(within(card).getByText("段位贡献")).toBeVisible();
    expect(within(card).getByText("123.4")).toBeVisible();
    fireEvent.focus(card);
    expect(await screen.findByRole("tooltip")).toHaveTextContent("第 8 回合结束时的段位贡献，非实时值。");
    fireEvent.blur(card);
    fireEvent.click(card);
    const detail = await screen.findByRole("region", { name: "验收重甲卫士 的详情" });
    expect(within(detail).getByText("123.4")).toBeVisible();
});

it("按冒险者ID匹配贡献，同名新成员不借用旧成员分数", () => {
    const newcomer = { ...member, adventurer_id: "new-knight" };
    mount({ ...observation, adventurers: [member, newcomer] }, { turn: 8, scores: { knight: 123.4 } });
    const cards = screen.getAllByRole("button", { name: "查看成员 验收重甲卫士" });
    expect(within(cards[0]).getByText("123.4")).toBeVisible();
    expect(within(cards[1]).getByText("未记录")).toBeVisible();
    expect(within(cards[1]).queryByText("123.4")).not.toBeInTheDocument();
});

it("零贡献显示0，旧记录没有个人贡献时显示未记录", () => {
    const view = mount(observation, { turn: 8, scores: { knight: 0 } });
    expect(screen.getByRole("button", { name: "查看成员 验收重甲卫士" }).querySelector(".member-contribution b")).toHaveTextContent(/^0$/);
    view.rerender(<Theme><GameScene observation={observation} contributions={null} /></Theme>);
    expect(screen.getByText("未记录")).toBeVisible();
});

it("用编队卡和真实生命条展示局面，点角色打开槽位图，核心详情不再使用 JSON", async () => {
    mount();
    expect(screen.getByRole("meter", { name: "生命" })).toHaveAttribute("aria-valuenow", "35");
    expect(screen.getByRole("meter", { name: "生命" })).toHaveAttribute("aria-valuemax", "120");
    expect(screen.queryByRole("meter", { name: "法力" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "查看成员 验收重甲卫士" }));
    const dialog = await screen.findByRole("region", { name: "验收重甲卫士 的详情" });
    expect(within(dialog).getByRole("region", { name: "验收重甲卫士 的装备槽位图" })).toBeVisible();
    const equipped = within(dialog).getByRole("button", { name: "主手：沉铁长剑" });
    expect(equipped).toBeVisible();
    expect(equipped).toHaveTextContent(/^沉铁长剑$/);
    expect(within(equipped).getByText("沉铁长剑")).toHaveAttribute("title", "沉铁长剑");
    const icon = equipped.querySelector("img.equipment-icon");
    expect(icon).toHaveAttribute("width", "36");
    expect(icon).toHaveAttribute("height", "36");
    const blocked = within(dialog).getByRole("button", { name: "副手：被占用" });
    expect(blocked).toHaveTextContent("副手");
    expect(blocked).toHaveTextContent("被双手占用");
    expect(within(dialog).getByRole("button", { name: "头盔：空" })).toHaveTextContent("头盔空槽位");
    fireEvent.click(blocked);
    expect(blocked).toHaveAttribute("aria-pressed", "true");
    expect(within(dialog).getByRole("heading", { name: "副手：已被占用" })).toBeVisible();
    fireEvent.click(within(dialog).getByRole("button", { name: /技能与战术/ }));
    expect(within(dialog).getByText("稳固阵线")).toBeVisible();
    expect(within(dialog).getByText("自身防御 +4")).toBeVisible();
    expect(dialog.querySelector("pre")).toBeNull();
});

it("从装备槽直接定位同类库存，搜索和穿戴状态筛选不会混入其他物品", async () => {
    mount();
    fireEvent.click(screen.getByRole("button", { name: "查看成员 验收重甲卫士" }));
    const dialog = await screen.findByRole("region", { name: "验收重甲卫士 的详情" });
    fireEvent.click(within(dialog).getByRole("button", { name: /在仓库查看同槽装备/ }));
    await waitFor(() =>
        expect(
            screen.queryByRole("region", { name: "验收重甲卫士 的详情" }),
        ).not.toBeInTheDocument(),
    );
    expect(screen.getByLabelText("筛选装备槽位")).toHaveValue("main_hand");
    expect(screen.getByRole("button", { name: "查看装备 沉铁长剑" })).toBeVisible();
    expect(screen.queryByRole("button", { name: "查看装备 旅者皮靴" })).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("筛选装备槽位"), { target: { value: "all" } });
    fireEvent.change(screen.getByLabelText("筛选穿戴状态"), { target: { value: "stored" } });
    expect(screen.getByRole("button", { name: "查看装备 旅者皮靴" })).toBeVisible();
    expect(screen.queryByRole("button", { name: "查看装备 沉铁长剑" })).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("搜索装备"), { target: { value: "不存在" } });
    expect(screen.getByText("没有符合筛选条件的装备")).toBeVisible();
});

it("切换历史局面后，消失的角色详情关闭，不残留未来角色", async () => {
    const view = mount();
    fireEvent.click(screen.getByRole("button", { name: "查看成员 验收重甲卫士" }));
    await screen.findByRole("region", { name: "验收重甲卫士 的详情" });
    view.rerender(
        <Theme>
            <GameScene observation={{ ...observation, adventurers: [] }} contributions={null} />
        </Theme>,
    );
    await waitFor(() =>
        expect(
            screen.queryByRole("region", { name: "验收重甲卫士 的详情" }),
        ).not.toBeInTheDocument(),
    );
    expect(screen.queryByRole("button", { name: "查看成员 验收重甲卫士" })).not.toBeInTheDocument();
});

it("怪物详情区分生命上限与当前生命，升级按真实前置层级排列", async () => {
    mount();
    fireEvent.click(screen.getByRole("button", { name: "怪物" }));
    fireEvent.click(screen.getByRole("button", { name: "查看怪物 兽人战士" }));
    const dialog = await screen.findByRole("region", { name: "兽人战士 的详情" });
    expect(within(dialog).getByText("生命上限")).toBeVisible();
    expect(within(dialog).queryByRole("meter")).not.toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole("button", { name: "收起对象详情" }));
    fireEvent.click(screen.getByRole("button", { name: "公会发展" }));
    expect(screen.getByRole("button", { name: "查看升级 铁匠铺" })).toBeVisible();
    expect(screen.getByRole("button", { name: "查看升级 军械库" })).toHaveAccessibleDescription("缺资源。前置需要全部满足：铁匠铺");
    expect(upgradeDepths(observation.global_upgrades!).get("armory")).toBe(1);
});

it("材料摘要不挤占主画面，全部材料在可访问的网格中保留真实零值", async () => {
    render(
        <Theme>
            <SceneMaterials materials={{ iron_ore: 8, wood: 0, bone: 2, spider_silk: 5 }} />
        </Theme>,
    );
    fireEvent.click(screen.getByRole("button", { name: "查看全部材料" }));
    const heading = await screen.findByRole("heading", { name: "当前观看位置的材料" });
    expect(heading).toBeVisible();
    expect(screen.getByText("蛛丝")).toBeVisible();
    expect(screen.getByText("0", { exact: true })).toBeVisible();
});

it("成员常用属性优先，生命法力不重复堆卡片，对比时仍能查看完整差值", () => {
    mount();
    fireEvent.click(screen.getByRole("button", { name: "查看成员 验收重甲卫士" }));
    const stats = screen.getByText("当前生效属性").closest("section")!;
    expect([...stats.querySelectorAll("dt")].map((node) => node.textContent)).toEqual(["攻击", "防御", "速度"]);
    fireEvent.click(within(stats).getByRole("button", { name: "对比基础" }));
    expect(within(stats).getByText("生命上限")).toBeVisible();
    expect(within(stats).getByText("法力上限")).toBeVisible();
    expect(within(stats).getByText("+30")).toBeVisible();
});

it("配方展示真实费用、材料缺口和成品，可返回同槽库存，全程只读", async () => {
    const fetch = vi.spyOn(globalThis, "fetch");
    mount({ ...observation, materials: { iron_ore: 1 }, crafting_recipes: [testRecipe()] });
    fireEvent.click(screen.getByRole("button", { name: "装备库" }));
    fireEvent.click(screen.getByRole("button", { name: "制作配方" }));
    fireEvent.click(screen.getByRole("button", { name: "查看制作配方 验收长剑" }));
    const details = await screen.findByRole("region", { name: "验收长剑配方 的详情" });
    expect(within(details).getByText("当前未满足制作条件")).toBeVisible();
    expect(within(details).getByText("缺铁矿石 1")).toBeVisible();
    expect(within(details).getByText("需要 2 / 持有 1")).toBeVisible();
    expect(within(details).getByText("只读查看所选快照，不执行制作。")).toBeVisible();
    fireEvent.click(within(details).getByRole("button", { name: "查看同槽库存" }));
    expect(screen.getByRole("button", { name: "装备库存" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByLabelText("筛选装备槽位")).toHaveValue("main_hand");
    expect(fetch).not.toHaveBeenCalled();
});

it("历史位置不再包含配方时关闭旧详情，不残留未来条件", async () => {
    const value = { ...observation, crafting_recipes: [testRecipe()] };
    const view = mount(value);
    fireEvent.click(screen.getByRole("button", { name: "装备库" }));
    fireEvent.click(screen.getByRole("button", { name: "制作配方" }));
    fireEvent.click(screen.getByRole("button", { name: "查看制作配方 验收长剑" }));
    await screen.findByRole("region", { name: "验收长剑配方 的详情" });
    view.rerender(<Theme><GameScene observation={{ ...value, crafting_recipes: [] }} /></Theme>);
    await waitFor(() => expect(screen.queryByRole("region", { name: "验收长剑配方 的详情" })).not.toBeInTheDocument());
    expect(screen.getByText("该位置没有制作配方")).toBeVisible();
});

it("技能文字保留真实倍率、触发条件和状态，不做胜率或强弱推断", () => {
    expect(conditionText({ type: "self_hp_pct_lte", value: 0.3, conditions: [] })).toBe(
        "自身生命不高于 30%",
    );
    expect(conditionText({ type: "self_hp_pct_lte", value: 0.125, conditions: [] })).toBe(
        "自身生命不高于 12.5%",
    );
    expect(
        effectText({
            type: "atk_ratio_damage",
            value: 0.5,
            stat: null,
            target: "target",
            status: null,
        }),
    ).toBe("造成攻击力 50% 的无视防御伤害");
});
