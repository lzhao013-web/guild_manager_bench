import { expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { SkillCards } from "./SceneParts";
import { TalentDetails } from "./TalentDetails";
import { effectText } from "./sceneModel";
import type { Talent } from "./talentModel";

it("自动回蓝说明不再暗示占用主动或赠送普攻", () => {
    render(<SkillCards skills={[{
        skill_id: "talent_mana_reflux", name: "法力回流", kind: "active",
        trigger: "action_start", mp_cost: 0, free: false, once_per_battle: true,
        condition: { type: "self_mp_pct_lte", value: 0.25, conditions: [] },
        effects: [{ type: "mp_restore_percent", value: 0.3, minimum: 12, target: "self", stat: null, status: null }],
    }]} />);
    expect(screen.getByText(/自动回复/)).toBeVisible();
    expect(screen.getByText(/不占主动选择，不额外普攻/)).toBeVisible();
    expect(screen.getByText(/30% 最大法力，至少 12 MP/)).toBeVisible();
    expect(screen.queryByText("即时")).toBeNull();
});

it("经营天赋显示普通金币、高阶金币及刷新折扣", () => {
    const talent: Talent = {
        upgrade_id: "guild_contracts", name: "行会商路", required_upgrade_ids: [],
        unlocked: true, can_purchase: false, gold_cost: 450, experience_cost: 120,
        hunt_gold_bonus_percent: 10, elite_boss_gold_bonus_percent: 18,
        monster_refresh_discount_percent: 10, skills: [], stats: {},
    };
    render(<TalentDetails talent={talent} upgrades={[talent]} onSelect={vi.fn()} />);
    expect(screen.getByText("讨伐金币加成 10%")).toBeVisible();
    expect(screen.getByText("精英、首领金币额外加成 18%")).toBeVisible();
    expect(screen.getByText("刷新基础价减免 10%")).toBeVisible();
});

it("百分比回蓝的保底是可选的数据字段", () => {
    expect(effectText({ type: "mp_restore_percent", value: 0.3, stat: null, target: "self", status: null })).toBe("为自身恢复 30% 最大法力");
});
