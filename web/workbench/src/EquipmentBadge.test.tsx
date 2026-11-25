import { Theme } from "@radix-ui/themes";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { EquipmentBadge, equipmentStage } from "./EquipmentBadge";
import { MonitorPreviews } from "./MonitorPreviews";
import { RunOverview } from "./RunOverview";
import { testRecipe } from "./test-recipes";
import type { Observation } from "./types";

it.each([
    ["t1_heavy_sword", "t1", "T1"],
    ["t2_sacred_robe", "t2", "T2"],
    ["t3_siege_cannon", "t3", "T3"],
])("%s 使用模板的明确阶级", (id, stage, text) => {
    render(<EquipmentBadge templateId={id} />);
    expect(equipmentStage(id)).toBe(stage);
    expect(screen.getByText(text)).toHaveAttribute("data-equipment-stage", stage);
    expect(screen.getByText(text)).toHaveAttribute("title", `装备阶级 ${text}`);
});
it.each([undefined, "iron_sword", "legendary_cannon", "item_t3_heavy_sword", "t30_sword"])("未分阶模板 %s 不伪造 T1 或稀有度", id => {
    const { container } = render(<EquipmentBadge templateId={id} />);
    expect(equipmentStage(id)).toBeUndefined();
    expect(container).toBeEmptyDOMElement();
});

const observation: Observation = {
    turn: 20, max_turns: 35, gold: 100, experience_pool: 0, materials: {}, adventurers: [], party_size_limit: 6,
    equipment_inventory: [{ instance_id: "sword-1", template_id: "t1_heavy_sword", name: "铁剑", slot: "main_hand", equipped_by: null }],
    crafting_recipes: [testRecipe({ output_template_id: "t3_heavy_sword", can_craft: true, missing: {} })],
};
it("单局总览保留装备状态与导航，同时显示阶级", () => {
    const onOpen = vi.fn();
    render(<Theme><RunOverview observation={observation} frames={[]} records={[]} memo={{ state: "unavailable" }}
        modelTab="output" onModelTab={() => {}} onOpen={onOpen} onFrame={() => {}} /></Theme>);
    const item = screen.getByRole("button", { name: "查看装备 铁剑" });
    expect(within(item).getByText("T1")).toBeVisible();
    expect(within(item).getByText("未穿戴")).toBeVisible();
    expect(item).toHaveAccessibleDescription("装备阶级 T1");
    fireEvent.click(item);
    expect(onOpen).toHaveBeenCalledWith("inventory", { kind: "equipment", id: "sword-1" });
});
it("多路预览的配方按成品分阶，闲装按模板分阶，不改变可制作状态", () => {
    vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue({ width: 800, height: 300 } as DOMRect);
    const onFocus = vi.fn();
    const before = JSON.stringify(observation);
    render(<Theme><MonitorPreviews observation={observation} frame={undefined} frames={[]} onFrame={() => {}} onFocus={onFocus} /></Theme>);
    const recipe = screen.getByRole("button", { name: "查看制作配方 验收长剑" });
    expect(within(recipe).getByText("T3")).toBeVisible();
    expect(within(recipe).getByText("可制作")).toBeVisible();
    expect(within(screen.getByRole("button", { name: "查看装备 铁剑" })).getByText("T1")).toBeVisible();
    fireEvent.click(recipe);
    expect(onFocus).toHaveBeenCalledWith({ module: "inventory", selection: { kind: "recipe", id: "fixture-recipe" } });
    expect(JSON.stringify(observation)).toBe(before);
});
