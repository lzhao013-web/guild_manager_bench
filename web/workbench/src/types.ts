export type Status =
    | "queued"
    | "starting"
    | "running"
    | "stopping"
    | "completed"
    | "failed"
    | "interrupted";
export type Provider = "openai" | "openai-responses" | "anthropic" | "google";
export interface RunConfig {
    name?: string;
    profile_id?: string | null;
    api_profile_name?: string;
    preset: string;
    provider: Provider;
    model: string | null;
    base_url: string | null;
    reasoning_effort: string | null;
    thinking?: boolean | null;
    temperature?: number | null;
    max_tokens?: number | null;
    timeout: number;
    objective: string;
    max_tool_calls_per_turn: number;
    game_seed: number | null;
    scoring_seed: number | null;
    resume_run_id?: string | null;
    credential_present?: boolean;
}
export interface ApiProfile {
    id: string;
    name: string;
    provider: Provider;
    base_url: string;
    default_model: string | null;
    known_models: string[];
    credential_present: boolean;
    source: string;
    created_at: string;
    updated_at: string;
}
export interface PartyMember {
    adventurer_id: string;
    name: string;
    template_id: string;
    level: number;
    resources: { current_hp: number; current_mp: number };
    effective_stats: { hp: number; mp: number; [key: string]: number };
    base_stats?: Record<string, number>;
    skills?: GameEntity[];
    equipment_slots?: GameEntity[];
    tactics?: Record<string, unknown>;
    next_level?: Record<string, unknown>;
}
export interface GameEntity {
    name?: string;
    template_id?: string;
    instance_id?: string;
    slot?: string;
    [key: string]: unknown;
}
export interface CraftingRecipe extends GameEntity {
    recipe_id: string;
    name: string;
    output_name: string;
    output_slot: string;
    output_stats: Record<string, number>;
    output_skills: GameEntity[];
    output_allowed_class_names: string[];
    gold_cost: number;
    base_gold_cost?: number;
    material_costs: Record<string, number>;
    can_craft: boolean;
    missing: Record<string, unknown>;
}
export interface Observation {
    gold: number;
    experience_pool: number;
    materials: Record<string, number>;
    adventurers: PartyMember[];
    party_size_limit: number;
    turn: number;
    max_turns: number;
    finished?: boolean;
    monsters?: GameEntity[];
    equipment_inventory?: GameEntity[];
    crafting_recipes?: CraftingRecipe[];
    recruit_candidates?: GameEntity[];
    global_upgrades?: GameEntity[];
}
export interface Job {
    id: string;
    name: string;
    batch_id: string;
    batch_name: string;
    status: Status;
    phase: string;
    phase_started_at: string;
    created_at: string;
    updated_at: string;
    started_at: string | null;
    ended_at: string | null;
    config: RunConfig;
    effective_config: Record<string, unknown>;
    data: Record<string, unknown>;
    archive_id: string | null;
    turn: number | null;
    max_turns: number | null;
    last_score: number | null;
    scores: { turn: number; value: number }[];
    calls: number;
    tools: number;
    usage: {
        input_tokens: number | null;
        output_tokens: number | null;
        reported: number;
        responses: number;
    };
    retries: number;
    tool_failures: number;
    error: string | null;
    notice: string | null;
    cursor: number;
    checkpoint_at: string | null;
    checkpoint_error: string | null;
    observation: Observation | null;
    budget: { used: number; max_tool_calls: number } | null;
    recent_actions: {
        name: string;
        turn: number;
        ok: boolean | null;
        summary: string;
    }[];
}
export interface Snapshot {
    jobs: Job[];
    settings: Settings;
    presets: string[];
    scheduler_error: string | null;
}
export interface Settings {
    max_parallel: number;
    per_endpoint: number;
}
export interface EventRecord {
    at: string;
    cursor: number;
    after?: number;
    event: Record<string, any> & { type: string };
}
export interface EventPage {
    events: EventRecord[];
    cursor: number;
    has_more: boolean;
}
export interface Archive {
    run_id: string;
    created_at: string | null;
    status: string;
    turns: number;
    preset: string;
    model: string;
    rank_score: number | null;
    failure_reason?: string;
}
