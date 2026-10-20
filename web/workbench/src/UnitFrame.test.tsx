import { render, screen } from "@testing-library/react";
import { expect, it } from "vitest";
import { AdventurerStatus, adventurerFrame, adventurerHealth, adventurerStateText, isLowHealth, monsterFrame } from "./UnitFrame";
import type { PartyMember } from "./types";

const member = (hp: number, mp = 5, maxMp = 10): PartyMember => ({
    adventurer_id: "frame-unit", template_id: "foot_knight", name: "边框验收卫士", level: 1,
    resources: { current_hp: hp, current_mp: mp }, effective_stats: { hp: 100, mp: maxMp },
});

it.each([[100, "healthy"], [70, "wounded"], [30, "wounded"], [29, "critical"], [0, "down"]] as const)("生命 %s 使用 %s 边框，沿用原有 30% 低生命边界", (hp, state) => {
    expect(adventurerHealth(member(hp))).toBe(state);
    expect(adventurerFrame(member(hp))["data-unit-state"]).toBe(state);
    expect(isLowHealth(member(hp))).toBe(hp < 30);
});
it("法力耗尽为次要状态，不覆盖低生命框，也不误报无蓝职业", () => {
    expect(adventurerFrame(member(10, 0))).toMatchObject({ "data-unit-state": "critical", "data-mana-empty": true });
    expect(adventurerStateText(member(10, 0))).toBe("低生命，法力耗尽");
    expect(adventurerFrame(member(100, 0, 0))["data-mana-empty"]).toBe(false);
    render(<AdventurerStatus member={member(100, 0, 0)} compact />);
    expect(screen.queryByLabelText("法力耗尽")).not.toBeInTheDocument();
});
it.each([
    ["t1", "normal", "t1"], ["t2", "normal", "t2"], ["t3", "normal", "t3"],
    ["t1", "elite", "elite"], ["t1", "boss", "boss"], [undefined, "challenge", "challenge"],
    [undefined, "normal", "unknown"], ["t2", undefined, "t2"],
])("怪物 %s / %s 的框使用 %s，身份优先于阶级", (display_stage, tier, state) => {
    expect(monsterFrame({ display_stage, tier })["data-unit-state"]).toBe(state);
});
it("紧凑框仍提供低生命、倒下、法力耗尽的非颜色提示", () => {
    const view = render(<AdventurerStatus member={member(10, 0)} compact />);
    expect(screen.getByRole("img", { name: "低生命" })).toBeVisible();
    expect(screen.getByRole("img", { name: "法力耗尽" })).toBeVisible();
    view.rerender(<AdventurerStatus member={member(0)} compact />);
    expect(screen.getByRole("img", { name: "倒下" })).toBeVisible();
    expect(screen.queryByLabelText("低生命")).not.toBeInTheDocument();
});
it("正常成员不重复堆叠徽章，完整受伤卡提供可读文字", () => {
    const view = render(<AdventurerStatus member={member(100)} compact />);
    expect(screen.queryByRole("img")).not.toBeInTheDocument();
    view.rerender(<AdventurerStatus member={member(70)} />);
    expect(screen.getByText("受伤")).toBeVisible();
});
