import { expect, it } from "vitest";
import { layoutTalents, talentRoute, talentState } from "./talentModel";
import type { Talent } from "./talentModel";

export const talents: Talent[] = [
    { upgrade_id: "forge", name: "构造铁匠铺", branch: "martial", tier: 1, required_upgrade_ids: [], unlocked: true, can_purchase: true, missing: {}, gold_cost: 50, stats: {}, skills: [] },
    { upgrade_id: "armory", name: "构造军械库", branch: "martial", tier: 2, required_upgrade_ids: ["forge"], unlocked: false, can_purchase: true, missing: {}, gold_cost: 120, stats: {}, skills: [] },
    { upgrade_id: "war", name: "构造战争学院", branch: "martial", tier: 4, required_upgrade_ids: ["armory"], exclusive_upgrade_ids: ["arcane"], unlocked: false, can_purchase: false, missing: { required_upgrade_ids: ["armory"], gold: 80 }, gold_cost: 600, experience_cost: 300, material_costs: { dragon_scale: 8 }, stats: { attack: 8 }, skills: [] },
    { upgrade_id: "scout", name: "构造侦查网", branch: "arcane", tier: 1, required_upgrade_ids: [], unlocked: true, can_purchase: true, missing: {}, gold_cost: 40, stats: {}, skills: [] },
    { upgrade_id: "arcane", name: "构造秘法结社", branch: "arcane", tier: 4, required_upgrade_ids: ["scout"], exclusive_upgrade_ids: ["war"], unlocked: false, can_purchase: false, missing: { gold: 80 }, gold_cost: 600, stats: {}, skills: [] },
];

it("层级纵向、分支横向，节点不重叠，真实互斥关系只绘制一次", () => {
    const result = layoutTalents(talents);
    expect(result.branched).toBe(true);
    expect(result.lanes.map((lane) => lane.label)).toEqual(["武备", "奥术"]);
    const byId = new Map(result.nodes.map((node) => [node.talent.upgrade_id, node]));
    expect(byId.get("forge")!.x).toBe(byId.get("armory")!.x);
    expect(byId.get("armory")!.y).toBeGreaterThan(byId.get("forge")!.y);
    expect(byId.get("war")!.level).toBe(4);
    expect(byId.get("scout")!.x).toBeGreaterThan(byId.get("forge")!.x);
    expect(result.edges.filter((edge) => edge.kind === "dependency")).toHaveLength(3);
    expect(result.edges.filter((edge) => edge.kind === "exclusive")).toHaveLength(1);
    for (const a of result.nodes) for (const b of result.nodes) {
        if (a === b) continue;
        expect(a.x + a.width <= b.x || b.x + b.width <= a.x || a.y + a.height <= b.y || b.y + b.height <= a.y).toBe(true);
    }
});

it("旧快照不虚构分支和天赋层级，仍按真实前置深度布局", () => {
    const legacy = talents.slice(0, 3).map(({ branch, tier, exclusive_upgrade_ids, ...node }) => node);
    const result = layoutTalents(legacy);
    expect(result.branched).toBe(false);
    expect(result.lanes).toHaveLength(1);
    expect(result.nodes.map((node) => node.level)).toEqual([1, 2, 3]);
});

it("选中路线包含所有祖先而非仅相邻节点，互斥对象与前置严格分开", () => {
    const route = talentRoute(talents, "war");
    expect([...route.ancestors].sort()).toEqual(["armory", "forge"]);
    expect([...route.exclusive]).toEqual(["arcane"]);
    expect(route.edges.has("forge>armory")).toBe(true);
    expect(route.edges.has("armory>war")).toBe(true);
    expect(route.edges.has("scout>arcane")).toBe(false);
});

it("节点状态遵循服务端结果，区分前置、资源、互斥，已解锁不会被资源变化覆盖", () => {
    expect(talentState(talents[0])).toBe("unlocked");
    expect(talentState(talents[1])).toBe("available");
    expect(talentState(talents[2])).toBe("prerequisite");
    expect(talentState({ ...talents[2], missing: { materials: { dragon_scale: 2 } } })).toBe("resources");
    expect(talentState({ ...talents[2], missing: { exclusive_upgrade_ids: ["arcane"], gold: 80 } })).toBe("exclusive");
    expect(talentState({ ...talents[0], missing: { gold: 80 } })).toBe("unlocked");
});

it("缺失的旧版前置仍可在详情显示原 ID，不画不存在的节点或依赖", () => {
    const incomplete = [{ ...talents[2], required_upgrade_ids: ["old-parent"] }];
    const result = layoutTalents(incomplete);
    expect(result.nodes).toHaveLength(1);
    expect(result.edges).toHaveLength(0);
    expect(talentRoute(incomplete, "war").ancestors.has("old-parent")).toBe(true);
});

it("跨过同层第二排的前置线绕开其他节点，不产生假的中间依赖", () => {
    const definitions: [string, number, string[]][] = [
        ["training", 1, []], ["forge", 1, []],
        ["assault", 2, ["training"]], ["armory", 2, ["forge"]],
        ["execution", 3, ["assault"]], ["reinforced", 3, ["armory"]], ["elite", 3, ["armory"]],
    ];
    const graph = layoutTalents(definitions.map(([id, tier, parents]) => ({ ...talents[1], upgrade_id: id, name: id, tier, required_upgrade_ids: parents })));
    const edge = graph.edges.find((item) => item.from === "armory" && item.to === "elite")!;
    expect(edge.path).toContain(" L ");
    const values = edge.path.match(/-?\d+(?:\.\d+)?/g)!.map(Number);
    const points = Array.from({ length: values.length / 2 }, (_, index) => ({ x: values[index * 2], y: values[index * 2 + 1] }));
    for (let index = 1; index < points.length; index++) {
        const a = points[index - 1], b = points[index];
        for (const node of graph.nodes.filter((item) => !["armory", "elite"].includes(item.talent.upgrade_id))) {
            const crosses = a.x === b.x
                ? a.x > node.x && a.x < node.x + node.width && Math.max(a.y, b.y) > node.y && Math.min(a.y, b.y) < node.y + node.height
                : a.y > node.y && a.y < node.y + node.height && Math.max(a.x, b.x) > node.x && Math.min(a.x, b.x) < node.x + node.width;
            expect(crosses, `连线不能穿过 ${node.talent.upgrade_id}`).toBe(false);
        }
    }
});

it("生成布局和高亮路线不修改观看快照", () => {
    const before = JSON.stringify(talents);
    layoutTalents(talents);
    talentRoute(talents, "war");
    expect(JSON.stringify(talents)).toBe(before);
});
