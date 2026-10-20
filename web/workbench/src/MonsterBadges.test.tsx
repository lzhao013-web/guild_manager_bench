import { Theme } from "@radix-ui/themes";
import { render, screen } from "@testing-library/react";
import { expect, it } from "vitest";
import { MonsterBadges } from "./MonsterBadges";
import { monsterClassification } from "./monsterModel";
import { watchFrames } from "./watchModel";
import type { Observation } from "./types";

it.each([
    ["t1", "normal", "T1", "普通"],
    ["t1", "boss", "T1", "首领"],
    ["t2", "elite", "T2", "精英"],
    ["t3", "boss", "T3", "首领"],
])("独立显示 %s 原型与 %s 身份", (stage, tier, stageText, roleText) => {
    render(<Theme><MonsterBadges monster={{ name: "不带身份前缀的怪物", display_stage: stage, tier }} /></Theme>);
    expect(screen.getByText(stageText)).toBeVisible();
    expect(screen.getByText(roleText)).toBeVisible();
    expect(screen.getByRole("group")).toHaveAccessibleName(`${stageText} · ${roleText}`);
});
it("挑战不误标为首领，旧日志也可从真实 tier 识别挑战", () => {
    render(<Theme><MonsterBadges monster={{ name: "灭阵暴君", tier: "challenge" }} compact /></Theme>);
    expect(screen.getByText("挑战")).toBeVisible();
    expect(screen.queryByText("首领")).not.toBeInTheDocument();
    expect(screen.getByRole("group")).toHaveAccessibleName("固定挑战");
});
it("未知分组不能根据名称、身份或回合猜成 T3", () => {
    const monster = { name: "T3 首领 自定义怪物", tier: "elite" };
    render(<Theme><MonsterBadges monster={monster} /></Theme>);
    expect(screen.getByText("阶级?")).toBeVisible();
    expect(screen.getByText("精英")).toBeVisible();
    expect(screen.queryByText("T3")).not.toBeInTheDocument();
    expect(monsterClassification(monster)).toBe("阶级未记录 · 精英");
});
it("历史、续跑和操作后局面共用原型分组，不改写原始快照", () => {
    const observation: Observation = { turn: 29, max_turns: 35, gold: 0, experience_pool: 0, materials: {}, adventurers: [], party_size_limit: 3, monsters: [{ name: "首领·史莱姆", archetype_id: "slime", tier: "boss" }] };
    const events = [
        { type: "turn_started", observation },
        { type: "run_resumed", restored_observation: observation },
        { type: "tool_result", name: "get_party", result: { ok: true, _observation_after: observation } },
    ].map((event, index) => ({ event, at: "2026-10-20T12:00:00Z", cursor: index + 1, after: index }));
    const before = JSON.stringify(events);
    const frames = watchFrames(events, null, { slime: "t1" });
    expect(frames).toHaveLength(3);
    for (const frame of frames) {
        expect(monsterClassification(frame.observation!.monsters![0])).toBe("T1 · 首领");
        expect(frame.observation).not.toBe(observation);
    }
    expect(JSON.stringify(events)).toBe(before);
});
