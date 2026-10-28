import { expect, it } from "vitest";
import { watchFrames } from "./watchModel";
import type { WatchRecord } from "./watchModel";

const observation = { turn: 2, max_turns: 35, gold: 0, experience_pool: 0, materials: {}, adventurers: [], party_size_limit: 3 };
const record = (cursor: number, event: WatchRecord["event"]): WatchRecord => ({ cursor, after: cursor - 1, at: "2026-10-22T00:00:00Z", event });

it("回合内沿用前次结算，只在评分事件的位置更新，早期帧不带入未来贡献", () => {
    const previous = { turn: 1, scores: { hero: 10 } };
    const frames = watchFrames([
        record(1, { type: "turn_started", turn: 2, observation }),
        record(2, { type: "tool_result", name: "allocate_experience", result: { ok: true, _observation_after: observation } }),
        record(3, { type: "turn_scored", turn: 2, rank_score: 20, adventurer_rank_scores: { hero: 20 } }),
    ], 10, {}, previous);
    expect(frames[0].rank_contributions).toEqual(previous);
    expect(frames[1].rank_contributions).toEqual(previous);
    expect(frames[2].rank_contributions).toEqual({ turn: 2, scores: { hero: 20 } });
    expect(previous.scores.hero).toBe(10);
});

it("恢复时改用检查点贡献，不沿用被回滚的后续数据", () => {
    const frames = watchFrames([
        record(1, { type: "turn_scored", turn: 8, rank_score: 80, adventurer_rank_scores: { hero: 80 } }),
        record(2, { type: "run_resumed", restored_observation: observation, last_rank_score: 10,
            last_adventurer_rank_scores: { turn: 1, scores: { hero: 10 } } }),
    ]);
    expect(frames[1].rank_contributions).toEqual({ turn: 1, scores: { hero: 10 } });
});

it("旧记录缺少个人贡献时不推算，也不沿用更早的已知数值", () => {
    const frames = watchFrames([
        record(1, { type: "turn_scored", turn: 2, rank_score: 20 }),
        record(2, { type: "run_resumed", restored_observation: observation, last_rank_score: 20 }),
    ], 10, {}, { turn: 1, scores: { hero: 10 } });
    expect(frames.every(frame => frame.rank_contributions === null)).toBe(true);
});
