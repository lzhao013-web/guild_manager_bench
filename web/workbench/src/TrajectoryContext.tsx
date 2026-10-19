import { createContext, useContext, useEffect, useState } from "react";
import type { ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "./api";
import { isActive } from "./model";
import type { Job } from "./types";
import type { TrajectoryData, TrajectoryRow } from "./trajectoryModel";

interface HistoryContext {
    data: TrajectoryData | undefined;
    error: Error | null;
    pending: boolean;
    locating: boolean;
    locationError: string;
    viewTurn: number | null;
    selectedCursor?: number;
    locate: (row: TrajectoryRow) => void;
    refresh: () => void;
}
const Context = createContext<HistoryContext | null>(null);
export const useTrajectories = () => useContext(Context);

export function TrajectoryProvider({ job, enabled, viewTurn, selectedCursor, onLocate, children }: {
    job: Job;
    enabled: boolean;
    viewTurn: number | null;
    selectedCursor?: number;
    onLocate: (attempt: number, cursor: number | null, origin: TrajectoryData["origin"]) => Promise<void>;
    children: ReactNode;
}) {
    const query = useQuery({
        queryKey: ["trajectories", job.id],
        queryFn: ({ signal }) => api<TrajectoryData>(`/api/workbench/jobs/${job.id}/trajectories`, { signal }),
        enabled,
        refetchInterval: enabled && isActive(job) ? 5000 : false,
    });
    useEffect(() => {
        // 终态不再定时轮询，但仍需追上最后的操作和回合提交记录。
        if (enabled && !isActive(job) && query.data) void query.refetch();
    }, [enabled, job.id, job.status]);
    const [locating, setLocating] = useState(false);
    const [locationError, setLocationError] = useState("");
    async function locate(row: TrajectoryRow) {
        setLocating(true);
        setLocationError("");
        try {
            await onLocate(row.attempt, row.cursor, query.data!.origin);
        } catch (error) {
            setLocationError((error as Error).message);
        } finally {
            setLocating(false);
        }
    }
    return <Context.Provider value={{ data: query.data, error: query.error, pending: query.isPending, locating, locationError,
        viewTurn, selectedCursor, locate, refresh: () => void query.refetch() }}>{children}</Context.Provider>;
}
