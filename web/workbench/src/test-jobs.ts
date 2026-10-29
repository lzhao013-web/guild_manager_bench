import type { Archive, Job } from "./types";

export function testJob(id: string, patch: Partial<Job> = {}): Job {
    return {
        id, name: "构造的同名模型", batch_id: "original-batch", batch_name: "原始实验",
        status: "running", phase: "waiting_model", phase_started_at: "2026-10-18T10:00:00Z",
        created_at: "2026-10-18T10:00:00Z", updated_at: "2026-10-18T10:00:00Z",
        started_at: "2026-10-18T10:00:00Z", ended_at: null,
        config: { preset: "default", provider: "openai", model: "fixture-model", base_url: "http://127.0.0.1:1/v1", reasoning_effort: null, timeout: 180, objective: "", max_tool_calls_per_turn: 24, game_seed: null, scoring_seed: null },
        effective_config: {}, data: {}, archive_id: "same-archive", turn: 11, max_turns: 35,
        last_score: 595.2728, scores: [{ turn: 10, value: 595.2728 }], calls: 55, tools: 99,
        usage: { input_tokens: 1000, output_tokens: 200, reported: 1, responses: 1 },
        retries: 0, tool_failures: 0, error: null, notice: null, cursor: 0,
        checkpoint_at: null, checkpoint_error: null, budget: null, recent_actions: [],
        ...patch,
    };
}

export function testArchive(run_id = "same-archive", patch: Partial<Archive> = {}): Archive {
    return { run_id, created_at: "2026-10-18T10:00:00Z", status: "running", turns: 11, preset: "default", model: "fixture-model", rank_score: 595.2728, ...patch };
}
