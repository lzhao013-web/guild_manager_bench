import { useMemo, useState } from "react";
import { AlertDialog, Button, Dialog } from "@radix-ui/themes";
import { Plus, Trash, ArrowRight } from "@phosphor-icons/react";
import { api, ApiError } from "./api";
import { expandDraft } from "./model";
import type { Draft, ModelRow, RunRequest } from "./model";
import type { ApiProfile, Job } from "./types";
import { ModelConnection } from "./ModelConnection";

interface RecoveryCheck {
    index: number;
    run_id: string;
    from_turn: number | null;
    confirmation_token: string;
    differences: { field: string; archived: string | number | null; current: string | number | null }[];
}
interface BatchBody { name: string; runs: RunRequest[] }
const versionFields: Record<string, string> = { preset: "规则集", benchmark_version: "评测版本", protocol_hash: "游戏规则版本", settings_hash: "评测参数", data_hash: "游戏数据", game_seed: "游戏种子", scoring_seed: "评分种子" };

export const defaultDraft = (preset: string): Draft => ({
    name: "",
    preset,
    rows: [{ provider: "openai", model: "", base_url: "", api_key: "" }],
    efforts: "",
    seeds: "",
    repeats: 1,
    max_tool_calls_per_turn: 20,
    scoring_seed: "",
    timeout: 600,
    temperature: "",
    max_tokens: "",
    objective: "",
});

export function NewBatch({
    presets,
    initial,
    onCreated,
    profiles = [],
    onProfileSaved,
    profilesLoading = false,
    profilesError,
}: {
    presets: string[];
    initial?: Draft;
    onCreated: (jobs: Job[]) => void;
    profiles?: ApiProfile[];
    profilesLoading?: boolean;
    profilesError?: string;
    onProfileSaved?: (profile: ApiProfile) => void;
}) {
    const [draft, setDraft] = useState<Draft>(
        initial || defaultDraft(presets[0]),
    );
    const [error, setError] = useState("");
    const [busy, setBusy] = useState(false);
    const [confirmation, setConfirmation] = useState<{ body: BatchBody; recoveries: RecoveryCheck[] } | null>(null);
    const update = <K extends keyof Draft>(key: K, value: Draft[K]) => {
        setDraft((d) => ({ ...d, [key]: value }));
        setConfirmation(null);
    };
    const updateRow = (index: number, patch: Partial<ModelRow>) =>
        update(
            "rows",
            draft.rows.map((row, i) =>
                i === index ? { ...row, ...patch } : row,
            ),
        );
    const preview = useMemo(() => {
        try {
            return { runs: expandDraft(draft), error: "" };
        } catch (cause) {
            return { runs: [], error: (cause as Error).message };
        }
    }, [draft]);
    const branch = !!draft.rerun_run_id;
    const resume = !!draft.resume_run_id || branch;
    async function enqueue(body: BatchBody) {
        try {
            const result = await api<{ jobs: Job[] }>("/api/workbench/batches", { method: "POST", body: JSON.stringify(body) });
            onCreated(result.jobs);
            setConfirmation(null);
        } catch (cause) {
            const detail = cause instanceof ApiError ? cause.detail as { code?: string; recoveries: RecoveryCheck[] } | null : null;
            if (detail?.code === "version_confirmation_required") setConfirmation({ body, recoveries: detail.recoveries });
            else { setConfirmation(null); setError((cause as Error).message); }
        }
    }
    async function confirmVersion() {
        if (!confirmation) return;
        setBusy(true);
        try {
            await enqueue({ ...confirmation.body, runs: confirmation.body.runs.map((run, index) => ({ ...run,
                version_confirmation: confirmation.recoveries.find(item => item.index === index)?.confirmation_token || run.version_confirmation,
            })) });
        } finally { setBusy(false); }
    }
    async function submit(event: React.FormEvent) {
        event.preventDefault();
        if (preview.error) {
            setError(preview.error);
            return;
        }
        setBusy(true);
        setError("");
        try {
            const body = { name: draft.name.trim() || "运行", runs: preview.runs };
            if (resume) {
                const check = await api<{ recoveries: RecoveryCheck[] }>("/api/workbench/batches/check", { method: "POST", body: JSON.stringify(body) });
                const differences = check.recoveries.filter(item => item.differences.length);
                if (differences.length) { setConfirmation({ body, recoveries: differences }); return; }
            }
            await enqueue(body);
        } catch (cause) {
            setError((cause as Error).message);
        } finally {
            setBusy(false);
        }
    }
    return (
        <Dialog.Content maxWidth="860px" className="create-dialog">
            <div className="dialog-title">
                <div>
                    <Dialog.Title>
                        {branch ? "从指定回合分支重跑" : resume ? "从检查点继续运行" : "新建运行"}
                    </Dialog.Title>
                    <Dialog.Description className="visually-hidden">配置模型和运行参数。</Dialog.Description>
                </div>
                <Dialog.Close>
                    <Button highContrast variant="soft" color="gray">
                        关闭
                    </Button>
                </Dialog.Close>
            </div>
            <form onSubmit={submit}>
                {resume && (
                    <p className="callout">
                        {branch ? `从 ${draft.rerun_run_id} 创建新的运行，原记录保留。` : `继续运行 ${draft.resume_run_id}，未完成的回合可能重新开始。`}
                    </p>
                )}
                {branch && <label className="callout">重跑起始回合
                    <input aria-label="重跑起始回合" type="number" min={1} max={draft.rerun_max_turn} required
                        value={draft.rerun_from_turn ?? 1} onChange={event => update("rerun_from_turn", Number(event.target.value))} />
                    <span> 可选 1 到 {draft.rerun_max_turn} 回合，此前的进度会保留。</span>
                </label>}
                <div className="form-grid">
                    <label>
                        数据 preset
                        <select
                            aria-label="数据 preset"
                            disabled={resume}
                            value={draft.preset}
                            onChange={(e) => update("preset", e.target.value)}
                        >
                            {presets.map((p) => (
                                <option key={p}>{p}</option>
                            ))}
                        </select>
                    </label>
                </div>
                {profilesError && (
                    <p className="callout error" role="alert">
                        无法读取 API 配置：{profilesError}
                    </p>
                )}
                <section className="form-section">
                    <div className="section-heading">
                        <h3>模型接入</h3>
                        {!resume && (
                            <Button
                                highContrast
                                type="button"
                                size="1"
                                variant="soft"
                                onClick={() =>
                                    update("rows", [
                                        ...draft.rows,
                                        {
                                            provider: "openai",
                                            model: "",
                                            base_url: "",
                                            api_key: "",
                                        },
                                    ])
                                }
                            >
                                <Plus />
                                添加模型
                            </Button>
                        )}
                    </div>
                    {draft.rows.map((row, i) => (
                        <div className="model-form" key={i}>
                            <ModelConnection
                                row={row}
                                profiles={profiles}
                                onChange={(patch) => updateRow(i, patch)}
                                onSaved={onProfileSaved}
                                keepModel={resume}
                                loading={profilesLoading}
                                loadError={profilesError}
                            />
                            {draft.rows.length > 1 && (
                                <Button
                                    highContrast
                                    type="button"
                                    color="red"
                                    variant="ghost"
                                    size="1"
                                    onClick={() =>
                                        update(
                                            "rows",
                                            draft.rows.filter(
                                                (_, n) => n !== i,
                                            ),
                                        )
                                    }
                                >
                                    <Trash />
                                    移除此模型
                                </Button>
                            )}
                        </div>
                    ))}
                    {draft.rows.some(row => !row.profile_id) && <p className="field-help">
                        临时填写的 API Key 只用于本次运行。点击“保存此 API 配置”可留作下次使用。
                    </p>}
                </section>
                {resume && (
                    <section className="form-section">
                        <h3>本次模型参数</h3>
                        <div className="form-grid">
                            <label>
                                推理强度
                                <input
                                    value={draft.efforts}
                                    onChange={(e) =>
                                        update("efforts", e.target.value)
                                    }
                                    placeholder="留空沿用服务端配置"
                                />
                                <small>
                                    这里只能填写一个推理强度。
                                </small>
                            </label>
                        </div>
                    </section>
                )}
                {!resume && (
                    <section className="form-section">
                        <h3>实验参数</h3>
                        <div className="form-grid three-fields">
                            <label>
                                推理强度
                                <input
                                    value={draft.efforts}
                                    onChange={(e) =>
                                        update("efforts", e.target.value)
                                    }
                                    placeholder="默认，或 medium, high"
                                />
                                <small>
                                    多个强度用逗号分隔。
                                </small>
                            </label>
                            <label>
                                游戏种子
                                <input
                                    value={draft.seeds}
                                    onChange={(e) =>
                                        update("seeds", e.target.value)
                                    }
                                    placeholder="默认，或 42, 43"
                                />
                                <small>留空使用默认种子。</small>
                            </label>
                            <label>
                                每个组合重复次数
                                <input
                                    type="number"
                                    min={1}
                                    max={64}
                                    required
                                    value={draft.repeats}
                                    onChange={(e) =>
                                        update(
                                            "repeats",
                                            Number(e.target.value),
                                        )
                                    }
                                />
                                <small>重复运行不自动改变种子。</small>
                            </label>
                        </div>
                    </section>
                )}
                {!resume && (
                    <details className="advanced">
                        <summary>高级运行参数</summary>
                        <div className="form-grid three-fields">
                            <label>
                                每回合工具预算
                                <input
                                    type="number"
                                    min={0}
                                    max={1000}
                                    required
                                    value={draft.max_tool_calls_per_turn}
                                    onChange={(e) =>
                                        update(
                                            "max_tool_calls_per_turn",
                                            Number(e.target.value),
                                        )
                                    }
                                />
                                <small>结束回合操作不计入次数。</small>
                            </label>
                            <label>
                                评分种子
                                <input
                                    value={draft.scoring_seed}
                                    onChange={(e) =>
                                        update("scoring_seed", e.target.value)
                                    }
                                    placeholder="默认"
                                />
                            </label>
                            <label>
                                请求超时（秒）
                                <input
                                    type="number"
                                    min={1}
                                    max={3600}
                                    required
                                    value={draft.timeout}
                                    onChange={(e) =>
                                        update(
                                            "timeout",
                                            Number(e.target.value),
                                        )
                                    }
                                />
                            </label>
                            <label>
                                Temperature
                                <input
                                    type="number"
                                    min={0}
                                    max={2}
                                    step={0.1}
                                    value={draft.temperature}
                                    onChange={(e) =>
                                        update("temperature", e.target.value)
                                    }
                                    placeholder="不指定"
                                />
                            </label>
                            <label>
                                最大输出 Token
                                <input
                                    type="number"
                                    min={1}
                                    max={1000000}
                                    value={draft.max_tokens}
                                    onChange={(e) =>
                                        update("max_tokens", e.target.value)
                                    }
                                    placeholder="沿用接口默认值"
                                />
                            </label>
                        </div>
                        <label>
                            经营目标
                            <textarea
                                rows={3}
                                maxLength={16000}
                                value={draft.objective}
                                onChange={(e) =>
                                    update("objective", e.target.value)
                                }
                                placeholder="留空使用项目默认经营目标"
                            />
                        </label>
                    </details>
                )}
                <section className="matrix-preview">
                    <div className="section-heading">
                        <h3>启动前确认</h3>
                        <strong className="mono">
                            {preview.runs.length} RUNS
                        </strong>
                    </div>
                    {preview.error ? (
                        <p className="error-text" role="alert">
                            {preview.error}
                        </p>
                    ) : (
                        <>
                            <div className="preview-list">
                                {preview.runs.map((run, i) => (
                                    <span key={i}>
                                        {String(i + 1).padStart(2, "0")}
                                        <b>{run.name}</b>
                                        <small>{run.preset}</small>
                                    </span>
                                ))}
                            </div>
                        </>
                    )}
                </section>
                {error && (
                    <p className="callout error" role="alert">
                        {error}
                    </p>
                )}
                <div className="dialog-footer">
                    <Button
                        highContrast
                        type="submit"
                        disabled={busy || !!preview.error}
                        loading={busy}
                    >
                        加入队列
                        <ArrowRight />
                    </Button>
                </div>
            </form>
            <AlertDialog.Root open={!!confirmation} onOpenChange={open => { if (!open && !busy) setConfirmation(null); }}>
                <AlertDialog.Content maxWidth="620px">
                    <AlertDialog.Title>按当前设置继续运行？</AlertDialog.Title>
                    <AlertDialog.Description>继续后会按当前规则恢复进度，结果可能与原来不同，也可能恢复失败。</AlertDialog.Description>
                    <div style={{ maxHeight: 300, overflow: "auto", overflowWrap: "anywhere", fontSize: 12 }}>
                        {confirmation?.recoveries.map(item => <section key={item.index}>
                            <h3>{item.run_id}{item.from_turn != null ? ` · 从 T${item.from_turn} 分支` : " · 续跑"}</h3>
                            <dl>{item.differences.map(change => <div key={change.field} style={{ marginBlock: 8 }}>
                                <dt>{versionFields[change.field] || change.field}</dt>
                                <dd style={{ marginLeft: 12 }}>原记录：<code>{change.archived == null ? "未记录" : String(change.archived)}</code><br />当前：<code>{change.current == null ? "未记录" : String(change.current)}</code></dd>
                            </div>)}</dl>
                        </section>)}
                    </div>
                    <div className="dialog-footer">
                        <AlertDialog.Cancel><Button type="button" variant="soft" color="gray" disabled={busy}>取消</Button></AlertDialog.Cancel>
                        <Button type="button" highContrast color="amber" loading={busy} onClick={() => void confirmVersion()}>仍然继续</Button>
                    </div>
                </AlertDialog.Content>
            </AlertDialog.Root>
        </Dialog.Content>
    );
}
