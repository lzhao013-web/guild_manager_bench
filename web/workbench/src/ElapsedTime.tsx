import { useEffect, useState } from "react";
import { duration } from "./model";

/** 秒级时钟只影响耗时文本，不驱动列表、图表或整个工作区重新渲染。 */
export function ElapsedTime({ start, end, live }: {
    start: string | null; end?: string | null; live: boolean;
}) {
    const [now, setNow] = useState(Date.now);
    useEffect(() => {
        if (!live || !start || end) return;
        let timer: ReturnType<typeof setInterval> | undefined;
        const updateVisibility = () => {
            clearInterval(timer);
            if (document.visibilityState === "hidden") return;
            setNow(Date.now());
            timer = setInterval(() => setNow(Date.now()), 1000);
        };
        updateVisibility();
        document.addEventListener("visibilitychange", updateVisibility);
        return () => {
            clearInterval(timer);
            document.removeEventListener("visibilitychange", updateVisibility);
        };
    }, [start, end, live]);
    return <>{duration(start, end, now)}</>;
}
