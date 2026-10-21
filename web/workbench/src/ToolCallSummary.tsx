import { TOOLS } from "./model";
import { OPERATION_CATEGORIES, TOOL_CALL_STATUS } from "./toolCalls";
import type { ToolCallCount, ToolCallStatus } from "./toolCalls";
import "./tool-calls.css";

export function ToolCallFilters({ tools, type, status, onType, onStatus, scope }: {
    tools: ToolCallCount[];
    type: string;
    status: "all" | ToolCallStatus;
    onType: (value: string) => void;
    onStatus: (value: "all" | ToolCallStatus) => void;
    scope: string;
}) {
    return <div className="tool-call-filters">
        <select aria-label={`筛选${scope}工具`} value={type} onChange={event => onType(event.target.value)}>
            <option value="all">全部工具调用</option>
            <optgroup label="工具类别">{OPERATION_CATEGORIES.map(category => <option key={category.id} value={`category:${category.id}`}>{category.label}</option>)}</optgroup>
            <optgroup label="具体工具">{tools.map(tool => <option key={tool.name} value={tool.name}>{tool.label}（{tool.total}）</option>)}</optgroup>
            {type !== "all" && !type.startsWith("category:") && !tools.some(tool => tool.name === type) && <option value={type}>{TOOLS[type] || type}（本回合无调用）</option>}
        </select>
        <select aria-label={`筛选${scope}状态`} value={status} onChange={event => onStatus(event.target.value as "all" | ToolCallStatus)}>
            <option value="all">全部执行状态</option>
            {Object.entries(TOOL_CALL_STATUS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
        </select>
        <p>查询与预览均列出，不代表实际收益。工具调用与对应结果合并计一次。</p>
    </div>;
}

export function ToolCallSummary({ tools, selected, onTool, label }: {
    tools: ToolCallCount[];
    selected: string;
    onTool: (name: string) => void;
    label: string;
}) {
    return <details className="tool-call-summary">
        <summary>按工具汇总 · {tools.length} 种 · {tools.reduce((total, tool) => total + tool.total, 0)} 次调用</summary>
        <section aria-label={label}>
            <table><thead><tr><th>工具</th><th>次数</th><th>成功</th><th>失败</th><th>执行中</th><th>结果未明</th></tr></thead>
                <tbody>{tools.map(tool => <tr key={tool.name}>
                    <th scope="row"><button aria-label={`筛选工具 ${tool.label}`} aria-pressed={selected === tool.name}
                        title={tool.name} onClick={() => onTool(tool.name)}>{tool.label}</button></th>
                    <td>{tool.total}</td><td>{tool.succeeded}</td><td>{tool.failed}</td><td>{tool.pending}</td><td>{tool.recorded}</td>
                </tr>)}</tbody>
            </table>
            {!tools.length && <p>尚无已记录的工具调用。</p>}
        </section>
    </details>;
}
