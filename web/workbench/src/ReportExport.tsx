import { useRef, useState } from "react";
import { Button } from "@radix-ui/themes";
import { DownloadSimple } from "@phosphor-icons/react";
import { apiResponse } from "./api";
import "./report-export.css";

export function useReportExport() {
    const pending = useRef(false);
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState("");
    const [message, setMessage] = useState("");
    async function exportReport(runId: string) {
        if (pending.current) return;
        pending.current = true;
        setBusy(true);
        setError("");
        setMessage("");
        try {
            const response = await apiResponse(`/api/llm/runs/${encodeURIComponent(runId)}/report`);
            const url = URL.createObjectURL(await response.blob());
            const link = document.createElement("a");
            link.href = url;
            link.download = "guild-report.html";
            document.body.append(link);
            try {
                link.click();
            } finally {
                link.remove();
                // 下载由浏览器异步接管，下一轮事件循环再释放对象 URL。
                setTimeout(() => URL.revokeObjectURL(url), 1000);
            }
            setMessage("战报已生成，请查看浏览器下载。");
        } catch (cause) {
            setError((cause as Error).message);
        } finally {
            pending.current = false;
            setBusy(false);
        }
    }
    return { exportReport, busy, error, message };
}

export function ReportExportButton({ runId }: { runId: string }) {
    const { exportReport, busy, error, message } = useReportExport();
    return <div className="report-export">
        <Button highContrast size="1" variant="soft" color="gray" disabled={busy} loading={busy}
            title="导出最终阵容、Rank、关键节点和模型用量，不含模型正文或连接配置"
            onClick={() => void exportReport(runId)}><DownloadSimple />导出战报</Button>
        {error && <span className="error-text" role="alert">导出失败：{error}</span>}
        {message && <span className="muted" role="status">{message}</span>}
    </div>;
}
