import type { ActionInput, InputValue } from "./actionInputs";
import { ResizableTable } from "./ResizableTable";

function Value({ value }: { value: InputValue }) {
    if (value.kind === "array" && value.children.length) {
        if (value.children.every(field => field.value.kind === "object")) {
            const columns = [...new Map(value.children.flatMap(row => row.value.children.map(field => [field.key, field.label] as const)))];
            if (columns.length) return <ResizableTable className="action-input-table" scroll
                storageKey={`action-input:${JSON.stringify(columns.map(([key]) => key).sort())}`}
                columns={[{ id: "row", label: "序号", minWidth: 56 },
                    ...columns.map(([key, label]) => ({ id: `field:${key}`, label: `${label} ${key}`, minWidth: 100 }))]}>
                <thead><tr><th scope="col">序号</th>{columns.map(([key, label]) => <th scope="col" key={key}>{label} <code>{key}</code></th>)}</tr></thead>
                <tbody>{value.children.map((row, i) => <tr key={row.key}><th scope="row">{row.label.startsWith("派遣") ? row.label : i + 1}</th>{columns.map(([key]) => {
                    const field = row.value.children.find(field => field.key === key);
                    return <td key={key}>{field ? <Value value={field.value} /> : "未提供"}</td>;
                })}</tr>)}</tbody></ResizableTable>;
        }
        return <ol className="action-input-list">{value.children.map(field => <li key={field.key}><Value value={field.value} /></li>)}</ol>;
    }
    if (value.children.length) return <dl className="action-input-fields">{value.children.map(field => <div key={field.key} className={`action-input-field ${field.value.children.length ? "is-group" : ""}`}>
        <dt>{field.label}{field.label !== field.key && <code>{field.key}</code>}</dt>
        <dd><Value value={field.value} /></dd>
    </div>)}</dl>;
    return <><span className="action-input-value">{value.text}</span>{value.original !== null && <code className="action-input-original">原值：{value.original}</code>}</>;
}

export function ActionInputs({ input }: { input: ActionInput }) {
    return <section className="action-inputs" aria-label="输入参数">
        <h4>输入参数</h4>
        {input.state === "missing" ? <p className="muted">没有保存这次操作的参数。</p>
            : input.value.kind === "object" && !input.value.children.length ? <p className="muted">无输入参数</p>
            : <Value value={input.value} />}
    </section>;
}
