import type { EventRecord, Job, Provider, RunConfig, Status } from "./types";

export const STATUS: Record<Status, string> = {
    queued: "排队中",
    starting: "启动中",
    running: "运行中",
    stopping: "停止中",
    completed: "已完成",
    failed: "失败",
    interrupted: "已中断",
};
export const PHASE: Record<string, string> = {
    queued: "等待执行槽位",
    starting: "初始化工作进程",
    preparing: "准备回合",
    waiting_model: "等待模型响应",
    receiving: "接收模型输出",
    executing_tool: "执行工具",
    scoring: "计算评分",
    retrying: "重试中",
    finalizing: "完成归档",
    idle: "已结束",
};
export const EFFORTS: Record<Provider, string[]> = {
    openai: [
        "none",
        "minimal",
        "low",
        "medium",
        "high",
        "xhigh",
        "ultra",
        "max",
    ],
    "openai-responses": [
        "none",
        "minimal",
        "low",
        "medium",
        "high",
        "xhigh",
        "ultra",
        "max",
    ],
    anthropic: ["low", "medium", "high", "max"],
    google: ["minimal", "low", "medium", "high"],
};
export const TOOLS: Record<string, string> = {
    get_party: "查看队伍",
    get_monsters: "查看怪物",
    get_crafting: "查看制作配方",
    get_inventory: "查看仓库",
    get_upgrades: "查看升级",
    get_recruitment: "查看招募",
    get_events: "查看事件",
    get_battle_log: "查看战斗日志",
    get_forecast: "查看预告",
    configure_tactics: "配置战术",
    preview_monster_refresh: "预览怪物刷新",
    refresh_monsters: "刷新怪物",
    recruit_adventurer: "招募冒险者",
    dismiss_adventurer: "解雇冒险者",
    allocate_experience: "分配经验",
    craft_equipment: "制作装备",
    equip_item: "穿戴装备",
    unequip_item: "卸下装备",
    purchase_upgrade: "购买升级",
    end_turn: "结束回合",
    write_memo: "记录备忘",
    preview_battle: "预览战斗",
    preview_team_power: "试算队伍战力",
};
export const isActive = (job: Job) =>
    ["queued", "starting", "running", "stopping"].includes(job.status);
export const fmt = (value: number | null | undefined) =>
    value == null
        ? "未知"
        : value.toLocaleString("zh-CN", { maximumFractionDigits: 1 });
export const short = (value: number | null) =>
    value == null
        ? "未知"
        : Intl.NumberFormat("zh-CN", {
              notation: "compact",
              maximumFractionDigits: 1,
          }).format(value);
export function duration(
    start: string | null,
    end?: string | null,
    now = Date.now(),
) {
    if (!start) return "尚未启动";
    const s = Math.max(
        0,
        Math.floor(((end ? Date.parse(end) : now) - Date.parse(start)) / 1000),
    );
    return s < 60
        ? `${s} 秒`
        : s < 3600
          ? `${Math.floor(s / 60)} 分 ${s % 60} 秒`
          : `${Math.floor(s / 3600)} 时 ${Math.floor((s % 3600) / 60)} 分`;
}
export function time(value: string | null) {
    return value
        ? new Date(value).toLocaleTimeString("zh-CN", { hour12: false })
        : "暂无";
}

export interface ModelRow {
    profile_id?: string | null;
    provider: Provider;
    model: string;
    base_url: string;
    api_key: string;
    thinking?: boolean | null;
}
export interface Draft {
    name: string;
    preset: string;
    rows: ModelRow[];
    efforts: string;
    seeds: string;
    repeats: number;
    max_tool_calls_per_turn: number;
    scoring_seed: string;
    timeout: number;
    temperature: string;
    max_tokens: string;
    objective: string;
    resume_run_id?: string;
    rerun_run_id?: string;
    rerun_from_turn?: number;
    rerun_max_turn?: number;
}
export interface RunRequest extends Omit<RunConfig, "objective"> {
    objective?: string;
    api_key?: string;
}
export function expandDraft(draft: Draft): RunRequest[] {
    const efforts = draft.efforts.split(/[,，\s]+/).filter(Boolean);
    const seedStrings = draft.seeds.split(/[,，\s]+/).filter(Boolean);
    const seeds: (number | null)[] = seedStrings.length
        ? seedStrings.map((seed) => {
              if (!/^-?\d+$/.test(seed) || !Number.isSafeInteger(Number(seed)))
                  throw new Error("游戏种子必须是整数，用逗号分隔");
              return Number(seed);
          })
        : [null];
    if (
        !Number.isInteger(draft.repeats) ||
        draft.repeats < 1 ||
        draft.repeats > 64
    )
        throw new Error("重复次数应为 1 到 64 的整数");
    const count =
        draft.rows.length *
        (efforts.length || 1) *
        seeds.length *
        draft.repeats;
    if (count < 1 || count > 64)
        throw new Error("一个批次需要 1 到 64 个 run，请调整参数组合");
    for (const row of draft.rows) {
        if (row.base_url.trim()) {
            let url: URL;
            try {
                url = new URL(row.base_url);
            } catch {
                throw new Error("服务地址必须是完整的 HTTP(S) URL");
            }
            if (!["http:", "https:"].includes(url.protocol))
                throw new Error("服务地址必须是完整的 HTTP(S) URL");
            if (url.username || url.password || url.search || url.hash)
                throw new Error("服务地址不能包含认证信息、查询参数或片段");
        }
        for (const effort of efforts)
            if (!EFFORTS[row.provider].includes(effort))
                throw new Error(`${row.provider} 不支持推理强度 ${effort}`);
    }
    if (
        draft.scoring_seed &&
        (!/^-?\d+$/.test(draft.scoring_seed) ||
            !Number.isSafeInteger(Number(draft.scoring_seed)))
    )
        throw new Error("评分种子必须是整数");
    const result = draft.rows.flatMap((row) =>
        (efforts.length ? efforts : [null]).flatMap((effort) =>
            seeds.flatMap((seed) =>
                Array.from({ length: draft.repeats }, (_, index) => ({
                    name: `${row.model || (row.profile_id ? "API 配置默认模型" : "环境配置模型")}${effort ? ` / ${effort}` : ""}${seed != null ? ` / seed ${seed}` : ""}${draft.repeats > 1 ? ` / 第 ${index + 1} 次` : ""}`,
                    preset: draft.preset,
                    provider: row.provider,
                    model: row.model.trim() || null,
                    base_url: row.profile_id
                        ? null
                        : row.base_url.trim() || null,
                    ...(row.profile_id ? { profile_id: row.profile_id } : {}),
                    thinking: row.thinking ?? null,
                    ...(!row.profile_id && row.api_key.trim()
                        ? { api_key: row.api_key.trim() }
                        : {}),
                    reasoning_effort: effort,
                    game_seed: seed,
                    scoring_seed: draft.scoring_seed
                        ? Number(draft.scoring_seed)
                        : null,
                    max_tool_calls_per_turn: draft.max_tool_calls_per_turn,
                    timeout: draft.timeout,
                    temperature: draft.temperature
                        ? Number(draft.temperature)
                        : null,
                    max_tokens: draft.max_tokens
                        ? Number(draft.max_tokens)
                        : null,
                    ...(draft.objective.trim()
                        ? { objective: draft.objective.trim() }
                        : {}),
                    resume_run_id: draft.resume_run_id || null,
                    rerun_run_id: draft.rerun_run_id || null,
                    rerun_from_turn: draft.rerun_run_id ? draft.rerun_from_turn : null,
                })),
            ),
        ),
    );
    if ((draft.resume_run_id || draft.rerun_run_id) && result.length !== 1)
        throw new Error(draft.resume_run_id ? "同一份存档只能创建一个续跑任务" : "一次分支重跑只能创建一个任务");
    if (draft.rerun_run_id && (!Number.isInteger(draft.rerun_from_turn) || draft.rerun_from_turn! < 1 || draft.rerun_from_turn! > draft.rerun_max_turn!))
        throw new Error("重跑起始回合不在可恢复范围内");
    return result;
}

export function safeTemplate(draft: Draft): Draft {
    expandDraft(draft);
    return {
        ...draft,
        resume_run_id: undefined,
        rerun_run_id: undefined,
        rerun_from_turn: undefined,
        rerun_max_turn: undefined,
        rows: draft.rows.map((row) => ({
            profile_id: row.profile_id || null,
            provider: row.provider,
            model: row.model,
            base_url: row.base_url,
            thinking: row.thinking ?? null,
            api_key: "",
        })),
    };
}

export interface ModelEntry {
    key: number;
    turn?: number;
    step?: number;
    text: string;
    reasoning: string;
    request?: unknown;
    response?: unknown;
    requestedAt?: string;
    observedAt?: string;
}
export function modelEntries(records: EventRecord[]): ModelEntry[] {
    const entries: ModelEntry[] = [];
    let current: ModelEntry | undefined;
    for (const record of records) {
        const e = record.event;
        if (e.type === "model_request") {
            current = {
                key: record.cursor,
                turn: e.turn,
                step: e.step,
                text: "",
                reasoning: "",
                request: e.request,
                requestedAt: record.at,
                observedAt: record.at,
            };
            entries.push(current);
        } else if (
            ["model_delta", "model_reasoning_delta", "model_response"].includes(
                e.type,
            )
        ) {
            if (!current) {
                current = { key: record.cursor, text: "", reasoning: "" };
                entries.push(current);
            }
            current.observedAt = record.at;
            if (e.type === "model_delta") current.text += e.text || "";
            if (e.type === "model_reasoning_delta")
                current.reasoning += e.text || "";
            if (e.type === "model_response") {
                current.text = e.text || current.text;
                current.reasoning =
                    e.assistant_metadata?.reasoning_content ||
                    current.reasoning;
                current.response = e;
                current = undefined;
            }
        }
    }
    return entries;
}
