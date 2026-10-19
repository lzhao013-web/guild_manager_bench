import { useState } from "react";
import { Theme } from "@radix-ui/themes";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { afterAll, beforeAll, expect, it, vi } from "vitest";
import { UpgradeGraph } from "./UpgradeGraph";
import { TalentDetails } from "./TalentDetails";
import type { Talent } from "./talentModel";

beforeAll(() => vi.stubGlobal("ResizeObserver", class { observe() {} disconnect() {} }));
afterAll(() => vi.unstubAllGlobals());
const nodes: Talent[] = [
    { upgrade_id: "root", name: "构造基础锻造", branch: "martial", tier: 1, required_upgrade_ids: [], unlocked: true, can_purchase: false, missing: {}, gold_cost: 50, stats: {}, skills: [] },
    { upgrade_id: "middle", name: "构造军械研究", branch: "martial", tier: 2, required_upgrade_ids: ["root"], unlocked: false, can_purchase: true, missing: {}, gold_cost: 120, stats: {}, skills: [] },
    { upgrade_id: "goal", name: "构造战争天赋", branch: "martial", tier: 4, required_upgrade_ids: ["middle"], exclusive_upgrade_ids: ["other"], unlocked: false, can_purchase: false, missing: { required_upgrade_ids: ["middle"], gold: 80, experience: 180, materials: { dragon_scale: 2 } }, gold_cost: 600, experience_cost: 300, material_costs: { dragon_scale: 8 }, stats: { attack: 8 }, skills: [] },
    { upgrade_id: "other", name: "构造奥术天赋", branch: "arcane", tier: 4, required_upgrade_ids: [], exclusive_upgrade_ids: ["goal"], unlocked: false, can_purchase: false, missing: { gold: 80 }, gold_cost: 600, stats: {}, skills: [] },
];
function Harness() {
    const [selected, setSelected] = useState<string | null>(null);
    return <Theme accentColor="teal"><UpgradeGraph upgrades={nodes} selectedId={selected} onSelect={(node) => setSelected(node.upgrade_id as string)} /></Theme>;
}

it("节点紧凑展示，选中后完整前置链与互斥分别突出，鼠标离开仍保留", () => {
    const { container } = render(<Harness />);
    fireEvent.click(screen.getByRole("button", { name: "查看升级 构造战争天赋" }));
    expect(screen.getByRole("button", { name: "查看升级 构造战争天赋" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: "查看升级 构造基础锻造" })).toHaveClass("is-route");
    expect(screen.getByRole("button", { name: "查看升级 构造军械研究" })).toHaveClass("is-route");
    expect(screen.getByRole("button", { name: "查看升级 构造奥术天赋" })).toHaveClass("is-exclusive");
    expect(container.querySelectorAll("path.dependency.highlighted")).toHaveLength(2);
    expect(container.querySelectorAll("path.exclusive.highlighted")).toHaveLength(1);
    fireEvent.mouseLeave(screen.getByRole("button", { name: "查看升级 构造战争天赋" }));
    fireEvent.blur(screen.getByRole("button", { name: "查看升级 构造战争天赋" }));
    expect(container.querySelectorAll("path.dependency.highlighted")).toHaveLength(2);
    expect(screen.getByRole("button", { name: "查看升级 构造战争天赋" })).not.toHaveTextContent("600");
});

it("搜索直接定位而不删除其他分支，缩放可恢复原始大小", () => {
    render(<Harness />);
    fireEvent.change(screen.getByLabelText("搜索天赋"), { target: { value: "战争" } });
    expect(screen.getByRole("status")).toHaveTextContent("1 个匹配");
    fireEvent.click(screen.getByRole("button", { name: "定位" }));
    expect(screen.getByRole("button", { name: "查看升级 构造战争天赋" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getAllByRole("button", { name: /^查看升级 / })).toHaveLength(4);
    fireEvent.click(screen.getByRole("button", { name: "放大天赋图" }));
    expect(screen.getByRole("button", { name: "天赋图原始大小" })).toHaveTextContent("110%");
    fireEvent.click(screen.getByRole("button", { name: "天赋图原始大小" }));
    expect(screen.getByRole("button", { name: "天赋图原始大小" })).toHaveTextContent("100%");
});

it("费用、缺口、真实前置和互斥说明集中展示，点击关系可定位对应对象", () => {
    const onSelect = vi.fn();
    const upgrades = nodes.map((node) => node.upgrade_id === "other" ? { ...node, unlocked: true, can_purchase: false, missing: {} } : node);
    const talent = { ...nodes[2], missing: { ...nodes[2].missing, exclusive_upgrade_ids: ["other"] } };
    render(<Theme><TalentDetails talent={talent} upgrades={upgrades} onSelect={onSelect} /></Theme>);
    expect(screen.getByText("互斥锁定", { exact: true })).toBeVisible();
    const costs = screen.getByText("完整解锁费用").nextElementSibling! as HTMLElement;
    expect(within(costs).getByText("600")).toBeVisible();
    expect(within(costs).getByText("300")).toBeVisible();
    expect(within(costs).getByText("龙鳞")).toBeVisible();
    expect(within(costs).getByText("8")).toBeVisible();
    expect(screen.getByLabelText("资源缺口")).toHaveTextContent("缺龙鳞 2");
    expect(screen.getByText("被已解锁节点锁定：构造奥术天赋")).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: /构造军械研究/ }));
    expect(onSelect).toHaveBeenCalledWith("middle");
});

it("切换历史快照会更新可解锁状态，不把后来的资源状况带到过去", () => {
    const { rerender } = render(<Theme><UpgradeGraph upgrades={nodes} selectedId="middle" onSelect={() => {}} /></Theme>);
    expect(within(screen.getByRole("button", { name: "查看升级 构造军械研究" })).getByText("可解锁")).toBeVisible();
    const previous = nodes.map((node) => node.upgrade_id === "middle" ? { ...node, can_purchase: false, missing: { gold: 40 } } : node);
    rerender(<Theme><UpgradeGraph upgrades={previous} selectedId="middle" onSelect={() => {}} /></Theme>);
    expect(within(screen.getByRole("button", { name: "查看升级 构造军械研究" })).getByText("缺资源")).toBeVisible();
});
