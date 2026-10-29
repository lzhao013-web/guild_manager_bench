import { Children, cloneElement, isValidElement, useId, useLayoutEffect, useRef } from "react";
import type { ReactNode } from "react";
import { createColumnSizing } from "../../assets/table-resize.mjs";
import type { ResizeColumn } from "../../assets/table-resize.mjs";
import "../../assets/table-resize.css";

export type { ResizeColumn } from "../../assets/table-resize.mjs";

function headings(children: ReactNode, columns: readonly ResizeColumn[]) {
    return Children.map(children, section => {
        if (!isValidElement<{ children?: ReactNode }>(section) || section.type !== "thead") return section;
        return cloneElement(section, {}, Children.map(section.props.children, row => {
            if (!isValidElement<{ children?: ReactNode }>(row) || row.type !== "tr") return row;
            return cloneElement(row, {}, Children.toArray(row.props.children).map((cell, index) => {
                if (!isValidElement<{ children?: ReactNode; "aria-label"?: string }>(cell) || cell.type !== "th") return cell;
                const column = columns[index];
                return cloneElement(cell, { "aria-label": column.label }, cell.props.children,
                    <div key={`resize:${column.id}`} className="column-resize-handle" data-column-resize={column.id}
                        role="separator" tabIndex={0} aria-orientation="vertical" aria-label={`调整${column.label}列宽`}
                        title="拖动调整列宽，双击恢复默认" />);
            }));
        }));
    });
}

export function ResizableTable({ storageKey, columns, className = "", scroll = false, children }: {
    storageKey: string;
    columns: readonly ResizeColumn[];
    className?: string;
    scroll?: boolean;
    children: ReactNode;
}) {
    const table = useRef<HTMLTableElement>(null);
    const id = useId();
    const schema = JSON.stringify(columns);
    useLayoutEffect(() => {
        const controller = createColumnSizing(table.current!, { key: storageKey, columns });
        return () => controller.destroy();
    }, [storageKey, schema, scroll]);
    const content = <table ref={table} id={id} className={`resizable-table ${className}`}>
        <colgroup>{columns.map(column => <col key={column.id} />)}</colgroup>
        {headings(children, columns)}
    </table>;
    return scroll ? <div className="column-table-scroll">{content}</div> : content;
}
