import { useEffect, useState } from "react";
import { Button } from "@radix-ui/themes";
import { FloppyDisk } from "@phosphor-icons/react";
import { api } from "./api";
import { ModelPicker } from "./ModelPicker";
import type { ModelRow } from "./model";
import type { ApiProfile, Provider } from "./types";

export const PROVIDERS: Record<Provider, string> = {
    openai: "OpenAI Chat Completions",
    "openai-responses": "OpenAI Responses",
    anthropic: "Anthropic Messages",
    google: "Google Gemini 原生 API",
};

export function ModelConnection({
    row,
    profiles,
    onChange,
    onSaved,
    keepModel = false,
    loading = false,
    loadError,
}: {
    row: ModelRow;
    profiles: ApiProfile[];
    onChange: (patch: Partial<ModelRow>) => void;
    onSaved?: (profile: ApiProfile) => void;
    keepModel?: boolean;
    loading?: boolean;
    loadError?: string;
}) {
    const [savedProfile, setSavedProfile] = useState<ApiProfile | null>(null);
    const available = (
        savedProfile && !profiles.some((p) => p.id === savedProfile.id)
            ? [...profiles, savedProfile]
            : [...profiles]
    ).sort((a, b) => b.updated_at.localeCompare(a.updated_at));
    const selected = available.find((p) => p.id === row.profile_id);
    const [name, setName] = useState("");
    const [saving, setSaving] = useState(false);
    const [error, setError] = useState("");
    useEffect(() => {
        if (
            selected &&
            (row.provider !== selected.provider ||
                row.base_url !== selected.base_url ||
                row.api_key)
        ) {
            onChange({
                provider: selected.provider,
                base_url: selected.base_url,
                api_key: "",
            });
        }
    }, [selected?.id, selected?.updated_at, row.profile_id]);
    function choose(id: string) {
        setError("");
        const profile = available.find((p) => p.id === id);
        if (profile)
            onChange({
                profile_id: id,
                provider: profile.provider,
                base_url: profile.base_url,
                model: keepModel
                    ? row.model || profile.default_model || ""
                    : profile.default_model || "",
                api_key: "",
            });
        else onChange({ profile_id: null, api_key: "" });
    }
    async function save() {
        setSaving(true);
        setError("");
        try {
            let host = "环境配置";
            if (row.base_url) host = new URL(row.base_url).host;
            const profile = await api<ApiProfile>(
                "/api/workbench/api-profiles",
                {
                    method: "POST",
                    body: JSON.stringify({
                        name:
                            name.trim() ||
                            `${host} / ${PROVIDERS[row.provider]}`,
                        provider: row.provider,
                        base_url: row.base_url.trim() || null,
                        api_key: row.api_key.trim() || null,
                        default_model: row.model.trim() || null,
                    }),
                },
            );
            setSavedProfile(profile);
            onSaved?.(profile);
            onChange({
                profile_id: profile.id,
                provider: profile.provider,
                base_url: profile.base_url,
                api_key: "",
            });
        } catch (cause) {
            setError((cause as Error).message);
        } finally {
            setSaving(false);
        }
    }
    return (
        <>
            <label className="profile-select-label">
                API 配置
                <select
                    aria-label="API 配置"
                    value={row.profile_id || ""}
                    onChange={(e) => choose(e.target.value)}
                >
                    <option value="">自定义连接 / 服务端环境配置</option>
                    {row.profile_id && !selected && (
                        <option value={row.profile_id}>
                            {loading
                                ? "正在读取保存的配置…"
                                : loadError
                                  ? "暂时无法读取配置"
                                  : "配置已不存在，请重新选择"}
                        </option>
                    )}
                    {available.map((profile) => (
                        <option key={profile.id} value={profile.id}>
                            {profile.name}
                        </option>
                    ))}
                </select>
            </label>
            {row.profile_id && !selected && !loading && !loadError && (
                <p className="callout error" role="alert">
                    找不到这份 API 配置。请选择其它配置，或手动填写连接信息。
                </p>
            )}
            <div className="form-grid connection-fields">
                <label>
                    接口类型
                    <select
                        aria-label="接口类型"
                        disabled={!!row.profile_id}
                        value={selected?.provider || row.provider}
                        onChange={(e) =>
                            onChange({ provider: e.target.value as Provider })
                        }
                    >
                        {Object.entries(PROVIDERS).map(([value, label]) => (
                            <option key={value} value={value}>
                                {label}
                            </option>
                        ))}
                    </select>
                </label>
                <label>
                    服务地址
                    <input
                        type="url"
                        readOnly={!!row.profile_id}
                        value={selected?.base_url || row.base_url}
                        onChange={(e) => onChange({ base_url: e.target.value })}
                        placeholder="留空使用服务端环境配置"
                    />
                </label>
                {!row.profile_id ? (
                    <label>
                        API Key
                        <input
                            type="password"
                            autoComplete="off"
                            value={row.api_key}
                            onChange={(e) =>
                                onChange({ api_key: e.target.value })
                            }
                            placeholder="留空使用服务端凭证"
                        />
                    </label>
                ) : (
                    <div className="credential-note">
                        <strong>凭证</strong>
                        <span>
                            {!selected
                                ? "配置尚未加载"
                                : selected.credential_present
                                  ? "使用已保存的 API Key"
                                  : "使用服务端环境配置"}
                        </span>
                    </div>
                )}
                {row.provider === "anthropic" && (
                    <label>
                        Anthropic 思考
                        <select
                            aria-label="Anthropic 思考"
                            value={
                                row.thinking == null ? "" : String(row.thinking)
                            }
                            onChange={(e) =>
                                onChange({
                                    thinking:
                                        e.target.value === ""
                                            ? null
                                            : e.target.value === "true",
                                })
                            }
                        >
                            <option value="">沿用服务端配置</option>
                            <option value="true">启用 adaptive thinking</option>
                            <option value="false">禁用思考</option>
                        </select>
                    </label>
                )}
            </div>
            <ModelPicker
                connection={{ ...row, profile_id: row.profile_id }}
                value={row.model}
                onChange={(model) => onChange({ model })}
                knownModels={
                    selected
                        ? [
                              ...selected.known_models,
                              ...(selected.default_model
                                  ? [selected.default_model]
                                  : []),
                          ]
                        : []
                }
                disabledReason={
                    row.profile_id && !selected
                        ? loading
                            ? "正在读取 API 配置"
                            : loadError
                              ? "API 配置读取失败，请刷新页面重试"
                              : "请选择有效的 API 配置后再获取模型"
                        : undefined
                }
            />
            {!row.profile_id && (
                <div className="save-api-inline">
                    <label>
                        API 配置名称
                        <input
                            value={name}
                            maxLength={120}
                            onChange={(e) => setName(e.target.value)}
                            placeholder="可选，默认按服务地址命名"
                        />
                    </label>
                    <Button
                        highContrast
                        type="button"
                        variant="soft"
                        loading={saving}
                        onClick={() => void save()}
                    >
                        <FloppyDisk />
                        保存此 API 配置
                    </Button>
                </div>
            )}
            {error && (
                <p className="error-text field-help" role="alert">
                    {error}
                </p>
            )}
        </>
    );
}
