import { useEffect, useId, useRef, useState } from "react";
import { Button } from "@radix-ui/themes";
import { ArrowsClockwise } from "@phosphor-icons/react";
import { api } from "./api";
import type { Provider } from "./types";

export interface CatalogConnection {
    provider: Provider;
    base_url: string;
    api_key: string;
    profile_id?: string | null;
}
export interface CatalogModel {
    id: string;
    name: string;
}

export function ModelPicker({
    connection,
    value,
    onChange,
    knownModels = [],
    disabledReason,
    onModels,
}: {
    connection: CatalogConnection;
    value: string;
    onChange: (model: string) => void;
    knownModels?: string[];
    disabledReason?: string;
    onModels?: (models: CatalogModel[]) => void;
}) {
    const id = useId();
    const [models, setModels] = useState<CatalogModel[]>([]);
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState("");
    const [fetched, setFetched] = useState(false);
    const [truncated, setTruncated] = useState(false);
    const ticket = useRef(0);
    const abort = useRef<AbortController | null>(null);
    useEffect(() => {
        ticket.current += 1;
        abort.current?.abort();
        setModels([]);
        setError("");
        setBusy(false);
        setFetched(false);
        setTruncated(false);
        return () => {
            ticket.current += 1;
            abort.current?.abort();
        };
    }, [
        connection.provider,
        connection.base_url,
        connection.api_key,
        connection.profile_id,
    ]);
    async function load() {
        const current = ++ticket.current;
        abort.current?.abort();
        abort.current = new AbortController();
        setBusy(true);
        setError("");
        try {
            const profile = connection.profile_id;
            const result = await api<{
                models: CatalogModel[];
                truncated: boolean;
            }>(
                profile
                    ? `/api/workbench/api-profiles/${encodeURIComponent(profile)}/models`
                    : "/api/workbench/api-profiles/models",
                {
                    method: "POST",
                    signal: abort.current.signal,
                    ...(profile
                        ? {}
                        : {
                              body: JSON.stringify({
                                  provider: connection.provider,
                                  base_url: connection.base_url || null,
                                  api_key: connection.api_key || null,
                              }),
                          }),
                },
            );
            if (ticket.current !== current) return;
            setModels(result.models);
            setTruncated(result.truncated);
            setFetched(true);
            onModels?.(result.models);
        } catch (cause) {
            if (ticket.current === current) setError((cause as Error).message);
        } finally {
            if (ticket.current === current) setBusy(false);
        }
    }
    const fetchedIds = new Set(models.map((m) => m.id));
    const historical = [...new Set(knownModels)].filter(
        (model) => !fetchedIds.has(model),
    );
    return (
        <div className="model-picker">
            <label htmlFor={id}>模型名称</label>
            <div className="model-picker-controls">
                <input
                    id={id}
                    list={`${id}-models`}
                    maxLength={200}
                    value={value}
                    onChange={(e) => onChange(e.target.value)}
                    placeholder={
                        connection.profile_id
                            ? "选择模型，或留空使用配置默认值"
                            : "选择或输入模型 ID，留空使用服务端配置"
                    }
                />
                <Button
                    highContrast
                    type="button"
                    variant="soft"
                    size="2"
                    loading={busy}
                    disabled={!!disabledReason}
                    onClick={() => void load()}
                >
                    <ArrowsClockwise />
                    获取模型
                </Button>
            </div>
            <datalist id={`${id}-models`}>
                {models.map((model) => (
                    <option key={model.id} value={model.id}>
                        {model.name}
                    </option>
                ))}
                {historical.map((model) => (
                    <option key={model} value={model}>
                        历史记录 / 已保存模型（未验证）
                    </option>
                ))}
            </datalist>
            {fetched && (
                <p className="field-help" role="status">
                    API 返回 {models.length} 个模型
                    {truncated ? "，列表已截断" : ""}
                    。可从输入框建议中选择，也可手动输入。列举成功不代表模型支持当前协议、工具调用或指定推理强度。
                </p>
            )}
            {!fetched && historical.length > 0 && (
                <p className="field-help">
                    输入框包含 {historical.length}{" "}
                    个历史或已保存模型，不代表当前仍可用。点击“获取模型”查询实时目录。
                </p>
            )}
            {disabledReason && <p className="field-help">{disabledReason}</p>}
            {error && (
                <p className="error-text field-help" role="alert">
                    {error}。仍可手动填写模型 ID。
                </p>
            )}
        </div>
    );
}
