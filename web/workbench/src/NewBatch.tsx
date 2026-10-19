import { useMemo, useState } from "react";
import { Button, Dialog } from "@radix-ui/themes";
import { Plus, Trash, ArrowRight, FloppyDisk } from "@phosphor-icons/react";
import { api } from "./api";
import { expandDraft, safeTemplate } from "./model";
import type { Draft, ModelRow } from "./model";
import type { ApiProfile, Job } from "./types";
import { ModelConnection } from "./ModelConnection";

export const defaultDraft = (preset: string): Draft => ({
    name: "",
    preset,
    rows: [{ provider: "openai", model: "", base_url: "", api_key: "" }],
    efforts: "",
    seeds: "",
    repeats: 1,
    max_tool_calls_per_turn: 20,
    scoring_seed: "",
    timeout: 180,
    temperature: "",
    max_tokens: "",
    objective: "",
});

export function NewBatch({
    presets,
    initial,
    onCreated,
    onSave,
    profiles = [],
    onProfileSaved,
    profilesLoading = false,
    profilesError,
}: {
    presets: string[];
    initial?: Draft;
    onCreated: (jobs: Job[]) => void;
    onSave: (draft: Draft) => void;
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
    const [saved, setSaved] = useState(false);
    const update = <K extends keyof Draft>(key: K, value: Draft[K]) => {
        setDraft((d) => ({ ...d, [key]: value }));
        setSaved(false);
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
    const resume = !!draft.resume_run_id;
    async function submit(event: React.FormEvent) {
        event.preventDefault();
        if (preview.error) {
            setError(preview.error);
            return;
        }
        setBusy(true);
        setError("");
        try {
            const result = await api<{ jobs: Job[] }>(
                "/api/workbench/batches",
                {
                    method: "POST",
                    body: JSON.stringify({
                        name: draft.name.trim(),
                        runs: preview.runs,
                    }),
                },
            );
            onCreated(result.jobs);
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
                        {resume ? "从检查点继续运行" : "新建实验批次"}
                    </Dialog.Title>
                    <Dialog.Description>
                        配置模型、展开参数组合，确认后加入服务端队列。
                    </Dialog.Description>
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
                        续跑 {draft.resume_run_id}。沿用存档中的评测参数，由
                        runner 核对协议。未完成回合可能回滚。
                    </p>
                )}
                <div className="form-grid">
                    <label>
                        批次名称
                        <input
                            required
                            maxLength={120}
                            value={draft.name}
                            onChange={(e) => update("name", e.target.value)}
                            placeholder="例如：相同种子下比较推理强度"
                        />
                    </label>
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
                        。刚升级工作台时，请先在任务结束后重启服务。
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
                    <p className="field-help">
                        选择已保存 API
                        配置即可复用密钥。自定义连接的密钥仅用于本次执行，点击“保存此
                        API
                        配置”才会保存到系统凭证库。浏览器模板和任务配置均不保存密钥。
                    </p>
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
                                    只能选择一个强度，须符合本次接口的支持范围。
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
                                    多个强度用逗号分隔，须符合接口支持范围。
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
                                <small>留空使用 preset 的默认种子。</small>
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
                                <small>不包含 end_turn。</small>
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
                            <p>
                                按全局和服务端点并发上限调度。关闭页面不会停止运行。
                            </p>
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
                        type="button"
                        variant="soft"
                        color="gray"
                        disabled={!!preview.error}
                        onClick={() => {
                            onSave(safeTemplate(draft));
                            setSaved(true);
                        }}
                    >
                        <FloppyDisk />
                        {saved ? "已保存无凭证模板" : "保存为模板"}
                    </Button>
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
        </Dialog.Content>
    );
}
