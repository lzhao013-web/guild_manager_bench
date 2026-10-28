import { useEffect, useState } from "react";
import { AlertDialog, Button, Dialog } from "@radix-ui/themes";
import {
    DownloadSimple,
    MagnifyingGlass,
    PencilSimple,
    Plus,
    Trash,
    Play,
} from "@phosphor-icons/react";
import { api } from "./api";
import { PROVIDERS } from "./ModelConnection";
import { ModelPicker } from "./ModelPicker";
import { Empty } from "./ui";
import { ResizableTable } from "./ResizableTable";
import type { ApiProfile, Provider } from "./types";

const PROFILE_COLUMNS = [
    { id: "name", label: "配置 / 服务地址", minWidth: 160 },
    { id: "provider", label: "协议", minWidth: 100 },
    { id: "model", label: "默认模型", minWidth: 140 },
    { id: "key", label: "API Key 状态", minWidth: 90 },
    { id: "actions", label: "操作", minWidth: 260 },
];

export const readApiProfiles = () =>
    api<{ profiles: ApiProfile[] }>("/api/workbench/api-profiles");

export function ApiProfilesPanel({
    profiles,
    loading,
    error,
    onChanged,
    onUse,
}: {
    profiles: ApiProfile[];
    loading: boolean;
    error?: string;
    onChanged: () => void;
    onUse: (profile: ApiProfile) => void;
}) {
    const [filter, setFilter] = useState("");
    const visible = profiles
        .filter((profile) =>
            `${profile.name} ${profile.provider} ${profile.base_url} ${profile.default_model || ""}`
                .toLowerCase()
                .includes(filter.toLowerCase()),
        )
        .sort((a, b) => b.updated_at.localeCompare(a.updated_at));
    const [editor, setEditor] = useState<ApiProfile | "new" | null>(null);
    const [deleting, setDeleting] = useState<ApiProfile | null>(null);
    const [importing, setImporting] = useState(false);
    const [confirmImport, setConfirmImport] = useState(false);
    const [busy, setBusy] = useState(false);
    const [message, setMessage] = useState("");
    const [failure, setFailure] = useState("");
    async function importHistory() {
        setImporting(true);
        setFailure("");
        setMessage("");
        try {
            const report = await api<{
                created: number;
                skipped_existing: number;
                skipped_unresolved: number;
                source_count: number;
            }>("/api/workbench/api-profiles/import-history", {
                method: "POST",
            });
            setMessage(
                `已导入 ${report.created} 份配置，跳过 ${report.skipped_existing} 份已有配置、${report.skipped_unresolved} 条无法还原的命令。读取 ${report.source_count} 份本机历史文件，未向模型服务发送请求。`,
            );
            setConfirmImport(false);
        } catch (cause) {
            setFailure((cause as Error).message);
            setConfirmImport(false);
        } finally {
            setImporting(false);
            onChanged();
        }
    }
    async function remove() {
        if (!deleting) return;
        setBusy(true);
        setFailure("");
        try {
            await api(
                `/api/workbench/api-profiles/${encodeURIComponent(deleting.id)}`,
                { method: "DELETE" },
            );
            setDeleting(null);
        } catch (cause) {
            setFailure((cause as Error).message);
            setDeleting(null);
        } finally {
            setBusy(false);
            onChanged();
        }
    }
    return (
        <section className="api-profiles-section">
            <div className="api-profiles-toolbar">
                <div>
                    <h2>可复用的 API 连接</h2>
                    <p className="muted">
                        编辑时可直接查看和修改 API Key。新建实验和续跑只需选择配置。
                    </p>
                </div>
                <Button
                    highContrast
                    variant="soft"
                    onClick={() => setConfirmImport(true)}
                >
                    <DownloadSimple />
                    从命令历史导入
                </Button>
                <Button highContrast onClick={() => setEditor("new")}>
                    <Plus />
                    新增 API 配置
                </Button>
            </div>
            <p className="callout">
                历史导入只还原命令中的协议、地址、凭证和模型，不代表密钥仍有效。点击“编辑
                / 模型”可获取实时模型目录。列举模型不会发起推理请求。
            </p>
            {message && (
                <p className="callout" role="status">
                    {message}
                </p>
            )}
            {(failure || error) && (
                <p className="callout error" role="alert">
                    {failure || error}
                    。如果刚升级前端，请在现有任务结束后重启服务。
                </p>
            )}
            <div className="api-profiles-filter">
                <label className="run-search">
                    <MagnifyingGlass />
                    <input
                        aria-label="搜索 API 配置"
                        value={filter}
                        onChange={(e) => setFilter(e.target.value)}
                        placeholder="搜索配置、服务地址或默认模型"
                    />
                </label>
                <span className="muted">
                    {visible.length} / {profiles.length} 份配置
                </span>
            </div>
            {loading ? (
                <div className="skeleton-lines" aria-label="正在读取 API 配置">
                    <i />
                    <i />
                    <i />
                </div>
            ) : visible.length ? (
                <div className="api-profile-table-wrap">
                    <ResizableTable className="run-table api-profile-table" storageKey="api-profiles" columns={PROFILE_COLUMNS}>
                        <thead>
                            <tr>
                                <th>配置 / 服务地址</th>
                                <th>协议</th>
                                <th>默认模型</th>
                                <th>API Key</th>
                                <th>操作</th>
                            </tr>
                        </thead>
                        <tbody>
                            {visible.map((profile) => (
                                <tr key={profile.id}>
                                    <td>
                                        <strong>{profile.name}</strong>
                                        <small className="archive-id">
                                            {profile.base_url}
                                        </small>
                                        <small className="profile-source">
                                            {profile.source ===
                                            "command_history"
                                                ? "命令历史导入"
                                                : "手动配置"}{" "}
                                            / {profile.known_models.length}{" "}
                                            个历史模型
                                        </small>
                                    </td>
                                    <td>{PROVIDERS[profile.provider]}</td>
                                    <td className="mono">
                                        {profile.default_model ||
                                            "服务端环境配置"}
                                    </td>
                                    <td>
                                        {profile.credential_present
                                            ? "已保存"
                                            : "使用环境变量"}
                                    </td>
                                    <td>
                                        <div className="api-profile-actions">
                                            <Button
                                                highContrast
                                                size="1"
                                                variant="soft"
                                                onClick={() => onUse(profile)}
                                            >
                                                <Play />
                                                新建实验
                                            </Button>
                                            <Button
                                                highContrast
                                                size="1"
                                                variant="soft"
                                                color="gray"
                                                onClick={() =>
                                                    setEditor(profile)
                                                }
                                            >
                                                <PencilSimple />
                                                编辑 / 模型
                                            </Button>
                                            <button
                                                aria-label={`删除 API 配置 ${profile.name}`}
                                                title="删除 API 配置"
                                                className="text-button error-text"
                                                onClick={() =>
                                                    setDeleting(profile)
                                                }
                                            >
                                                <Trash size={16} />
                                            </button>
                                        </div>
                                    </td>
                                </tr>
                            ))}
                        </tbody>
                    </ResizableTable>
                </div>
            ) : profiles.length ? (
                <Empty
                    title="没有匹配的 API 配置"
                    action={
                        <Button
                            highContrast
                            variant="soft"
                            onClick={() => setFilter("")}
                        >
                            清除搜索
                        </Button>
                    }
                >
                    试试服务地址或模型名称。
                </Empty>
            ) : (
                !error && (
                    <Empty
                        title="保存一次，以后直接选择"
                        action={
                            <Button
                                highContrast
                                onClick={() => setEditor("new")}
                            >
                                新增 API 配置
                            </Button>
                        }
                    >
                        可以导入已有命令中的配置，或添加新的模型服务连接。
                    </Empty>
                )
            )}
            <Dialog.Root
                open={editor !== null}
                onOpenChange={(open) => {
                    if (!open) setEditor(null);
                }}
            >
                {editor && (
                    <ProfileEditor
                        key={editor === "new" ? "new" : editor.id}
                        profile={editor === "new" ? undefined : editor}
                        onSaved={() => {
                            setEditor(null);
                            setMessage(
                                "API 配置已保存，可以在新建实验或续跑时直接选择。",
                            );
                            onChanged();
                        }}
                    />
                )}
            </Dialog.Root>
            <AlertDialog.Root
                open={deleting !== null}
                onOpenChange={(open) => {
                    if (!open && !busy) setDeleting(null);
                }}
            >
                <AlertDialog.Content maxWidth="470px">
                    <AlertDialog.Title>
                        删除 {deleting?.name}？
                    </AlertDialog.Title>
                    <AlertDialog.Description>
                        删除配置及其系统凭证引用。已经加入队列的 run
                        不受影响。
                    </AlertDialog.Description>
                    <div className="dialog-footer">
                        <AlertDialog.Cancel>
                            <Button
                                highContrast
                                variant="soft"
                                color="gray"
                                disabled={busy}
                            >
                                取消
                            </Button>
                        </AlertDialog.Cancel>
                        <Button
                            highContrast
                            color="red"
                            loading={busy}
                            onClick={() => void remove()}
                        >
                            确认删除
                        </Button>
                    </div>
                </AlertDialog.Content>
            </AlertDialog.Root>
            <AlertDialog.Root
                open={confirmImport}
                onOpenChange={(open) => {
                    if (!importing) setConfirmImport(open);
                }}
            >
                <AlertDialog.Content maxWidth="480px">
                    <AlertDialog.Title>
                        导入本机命令历史中的 API？
                    </AlertDialog.Title>
                    <AlertDialog.Description>
                        只读取已知位置的 PowerShell、Bash 和 Zsh
                        命令历史，解析本项目运行命令中的参数，不执行命令。密钥存入当前系统用户的凭证库，不输出原始命令，不自动访问历史服务地址。重复连接会跳过。
                    </AlertDialog.Description>
                    <div className="dialog-footer">
                        <AlertDialog.Cancel>
                            <Button
                                highContrast
                                variant="soft"
                                color="gray"
                                disabled={importing}
                            >
                                取消
                            </Button>
                        </AlertDialog.Cancel>
                        <Button
                            highContrast
                            loading={importing}
                            onClick={() => void importHistory()}
                        >
                            确认导入
                        </Button>
                    </div>
                </AlertDialog.Content>
            </AlertDialog.Root>
        </section>
    );
}

type KeyReadState =
    | { state: "loading" }
    | { state: "ready"; value: string }
    | { state: "error"; message: string };

function ProfileEditor({
    profile,
    onSaved,
}: {
    profile?: ApiProfile;
    onSaved: () => void;
}) {
    const [name, setName] = useState(profile?.name || "");
    const [provider, setProvider] = useState<Provider>(
        profile?.provider || "openai",
    );
    const [base, setBase] = useState(profile?.base_url || "");
    const [model, setModel] = useState(profile?.default_model || "");
    const [key, setKey] = useState("");
    const [keyRead, setKeyRead] = useState<KeyReadState>(
        profile ? { state: "loading" } : { state: "ready", value: "" },
    );
    const [keyEdited, setKeyEdited] = useState(false);
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState("");
    useEffect(() => {
        if (!profile) return;
        const controller = new AbortController();
        let current = true;
        void api<{ api_key: string | null }>(
            `/api/workbench/api-profiles/${encodeURIComponent(profile.id)}/key`,
            { method: "POST", cache: "no-store", signal: controller.signal },
        ).then(
            (result) => {
                if (!current) return;
                const value = result.api_key ?? "";
                setKey(value);
                setKeyRead({ state: "ready", value });
            },
            (cause: Error) => {
                if (current) setKeyRead({ state: "error", message: cause.message });
            },
        );
        return () => {
            current = false;
            controller.abort();
        };
    }, [profile?.id]);
    const cannotSave = keyRead.state === "loading" ||
        (keyRead.state === "error" && !keyEdited);
    const changed =
        profile &&
        (provider !== profile.provider ||
            base !== profile.base_url ||
            keyRead.state !== "ready" ||
            key !== keyRead.value);
    async function submit(event: React.FormEvent) {
        event.preventDefault();
        if (cannotSave) return;
        setBusy(true);
        setError("");
        try {
            await api(
                profile
                    ? `/api/workbench/api-profiles/${encodeURIComponent(profile.id)}`
                    : "/api/workbench/api-profiles",
                {
                    method: profile ? "PUT" : "POST",
                    body: JSON.stringify({
                        name: name.trim(),
                        provider,
                        base_url: base.trim() || null,
                        api_key: key.trim() || null,
                        default_model: model.trim() || null,
                        clear_api_key: !!profile && !key.trim(),
                    }),
                },
            );
            onSaved();
        } catch (cause) {
            setError((cause as Error).message);
        } finally {
            setBusy(false);
        }
    }
    return (
        <Dialog.Content maxWidth="710px" className="create-dialog">
            <div className="dialog-title">
                <div>
                    <Dialog.Title>
                        {profile ? "编辑 API 配置" : "新增 API 配置"}
                    </Dialog.Title>
                    <Dialog.Description>
                        保存连接即可复用。模型 ID 可手动填写，也可从 API
                        的模型目录中选择。
                    </Dialog.Description>
                </div>
                <Dialog.Close>
                    <Button highContrast variant="soft" color="gray">
                        关闭
                    </Button>
                </Dialog.Close>
            </div>
            <form onSubmit={submit}>
                <div className="form-grid">
                    <label>
                        API 配置名称
                        <input
                            required
                            value={name}
                            maxLength={120}
                            onChange={(e) => setName(e.target.value)}
                            placeholder="例如：我的 OpenAI 或本地模型服务"
                        />
                    </label>
                    <label>
                        接口类型
                        <select
                            aria-label="接口类型"
                            value={provider}
                            onChange={(e) =>
                                setProvider(e.target.value as Provider)
                            }
                        >
                            {Object.entries(PROVIDERS).map(([value, text]) => (
                                <option key={value} value={value}>
                                    {text}
                                </option>
                            ))}
                        </select>
                    </label>
                    <label>
                        服务地址
                        <input
                            type="url"
                            value={base}
                            onChange={(e) => setBase(e.target.value)}
                            placeholder="留空使用服务端环境配置"
                        />
                    </label>
                    <label>
                        API Key
                        <input
                            type="text"
                            autoComplete="off"
                            autoCapitalize="none"
                            spellCheck={false}
                            disabled={keyRead.state === "loading"}
                            value={key}
                            onChange={(e) => {
                                setKey(e.target.value);
                                setKeyEdited(true);
                            }}
                            placeholder={keyRead.state === "loading"
                                ? "正在读取已保存的 API Key"
                                : "留空使用服务端环境变量"}
                        />
                    </label>
                </div>
                <p className="field-help">
                    API Key 直接明文显示，可修改后保存。清空后保存会移除已保存的 Key，改用服务端环境变量。
                </p>
                {keyRead.state === "error" && (
                    <p className="callout error" role="alert">
                        读取已保存的 API Key 失败：{keyRead.message}。可直接填写新的 Key 后保存，未修改前不会覆盖原配置。
                    </p>
                )}
                <ModelPicker
                    connection={{
                        provider,
                        base_url: base,
                        api_key: key,
                        profile_id: profile?.id,
                    }}
                    value={model}
                    onChange={setModel}
                    knownModels={profile?.known_models}
                    disabledReason={keyRead.state === "loading"
                        ? "正在读取已保存的 API Key。"
                        : changed
                            ? "连接或 API Key 已修改，请先保存配置后再获取模型。"
                            : undefined}
                />
                <p className="field-help">
                    OpenAI / Responses 使用 /models，Anthropic 使用
                    /v1/models，Google 原生 API 使用
                    /models。服务地址按对应适配器的基础地址填写，不包含认证信息或查询参数。保存后使用当前填写的地址和 API Key。
                </p>
                {error && (
                    <p className="callout error" role="alert">
                        {error}
                    </p>
                )}
                <div className="dialog-footer">
                    <span className="field-help">
                        Key 不写入浏览器本地存储。
                    </span>
                    <Button highContrast type="submit" loading={busy} disabled={cannotSave}>
                        保存 API 配置
                    </Button>
                </div>
            </form>
        </Dialog.Content>
    );
}
