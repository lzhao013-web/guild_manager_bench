import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import type { Job } from "./types";
import type { WatchPage } from "./watchModel";
import type { EventPage, EventRecord } from "./types";

export class ApiError extends Error {
    constructor(message: string, public status: number, public detail: unknown) { super(message); }
}

export async function api<T>(path: string, options?: RequestInit): Promise<T> {
    const response = await fetch(path, {
        ...options,
        headers: { "Content-Type": "application/json", ...options?.headers },
    });
    if (!response.ok) {
        const body = await response.json().catch(() => null);
        const detail = body?.detail;
        throw new ApiError(
            typeof detail === "string"
                ? detail
                : Array.isArray(detail)
                  ? detail.map((e: { msg: string }) => e.msg).join("，")
                  : detail && typeof detail === "object" && "message" in detail ? String(detail.message) : `请求失败（HTTP ${response.status}）`,
            response.status, detail,
        );
    }
    return response.json();
}

export function useLiveWatch(job: Job) {
    const query = useQuery({
        queryKey: ["watch", job.id, "live"],
        queryFn: ({ signal }) => api<WatchPage>(`/api/workbench/jobs/${job.id}/watch`, { signal }),
        refetchInterval: result => result.state.error ? 2500 : false,
    });
    useEffect(() => {
        // 摘要游标推进时追上事件边界，终态也必须读取最后一批结果。
        if (query.data && job.cursor > query.data.cursor) void query.refetch();
    }, [job.cursor, query.data?.cursor]);
    return query;
}

export function useEvents(id: string, active: boolean) {
    const [records, setRecords] = useState<EventRecord[]>([]);
    const [error, setError] = useState("");
    const [loading, setLoading] = useState(true);
    const cursor = useRef(0);
    useEffect(() => {
        let cancelled = false;
        let timer: ReturnType<typeof setTimeout>;
        let controller: AbortController;
        cursor.current = 0;
        setRecords([]);
        setError("");
        setLoading(true);
        async function poll() {
            controller = new AbortController();
            try {
                const page = await api<EventPage>(
                    `/api/workbench/jobs/${id}/events?after=${cursor.current}`,
                    { signal: controller.signal },
                );
                if (cancelled) return;
                cursor.current = page.cursor;
                setRecords((previous) =>
                    [...previous, ...page.events].slice(-600),
                );
                setError("");
                setLoading(false);
                // 正在追赶日志时尽快翻页，读取终态后不再轮询。
                if (active || page.has_more)
                    timer = setTimeout(poll, page.has_more ? 20 : 1000);
            } catch (cause) {
                if (cancelled) return;
                setError(
                    cause instanceof Error ? cause.message : String(cause),
                );
                setLoading(false);
                timer = setTimeout(poll, 2500);
            }
        }
        void poll();
        return () => {
            cancelled = true;
            clearTimeout(timer);
            controller?.abort();
        };
    }, [id, active]);
    return { records, error, loading };
}
