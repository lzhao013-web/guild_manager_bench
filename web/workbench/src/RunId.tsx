export function shortRunId(id: string): string {
    return id.replace(/^archive:/, "").split("_")[0].slice(-8);
}

export function RunId({ id }: { id: string }) {
    return <span className="run-id mono" title={`运行标识：${id}`} aria-label={`运行标识 ${id}`}>#{shortRunId(id)}</span>;
}
