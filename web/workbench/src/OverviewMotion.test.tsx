import { act, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { RunOverview } from "./RunOverview";
import type { Observation } from "./types";

const initial: Observation = {
    turn: 1, max_turns: 35, gold: 100, experience_pool: 0, materials: {}, party_size_limit: 3,
    adventurers: [{ adventurer_id: "guard", name: "验收卫士", template_id: "foot_knight", level: 1,
        resources: { current_hp: 80, current_mp: 0 }, effective_stats: { hp: 80, mp: 0 }, equipment_slots: [] }],
    equipment_inventory: [{ instance_id: "sword", name: "验收长剑", equipped_by: null }],
    global_upgrades: [{ upgrade_id: "forge", name: "验收铁匠铺", unlocked: false, can_purchase: true }],
};
afterEach(() => vi.useRealTimers());

it("首次读取不强调，实时招募、换装和解锁才强调对应对象，回看与恢复不重播", () => {
    vi.useFakeTimers();
    const node = (observation: Observation, live = true) => <RunOverview observation={observation} live={live}
        frames={[]} records={[]} memo={{ state: "unavailable" }} modelTab="output" onModelTab={() => {}} onOpen={() => {}} onFrame={() => {}} />;
    const view = render(node(initial));
    expect(document.querySelectorAll(".motion-event")).toHaveLength(0);
    const changed: Observation = { ...initial,
        adventurers: [...initial.adventurers, { ...initial.adventurers[0], adventurer_id: "mage", name: "验收法师" }],
        equipment_inventory: [{ ...initial.equipment_inventory![0], equipped_by: "guard" }],
        global_upgrades: [{ ...initial.global_upgrades![0], unlocked: true, can_purchase: false }],
    };
    view.rerender(node(changed));
    expect(screen.getByRole("button", { name: "查看成员 验收法师" })).toHaveClass("motion-event");
    expect(screen.getByRole("button", { name: "查看成员 验收卫士" })).not.toHaveClass("motion-event");
    expect(screen.getByRole("button", { name: "查看装备 验收长剑" })).toHaveClass("motion-event");
    expect(screen.getByRole("button", { name: "查看升级 验收铁匠铺" })).toHaveClass("motion-event");
    // 轮询会重复相同内容，但不能延长一次事件的强调。
    view.rerender(node({ ...changed, gold: 90 }));
    act(() => vi.advanceTimersByTime(900));
    expect(document.querySelectorAll(".motion-event")).toHaveLength(0);
    view.rerender(node(initial, false));
    view.rerender(node(changed));
    expect(document.querySelectorAll(".motion-event")).toHaveLength(0);
});
