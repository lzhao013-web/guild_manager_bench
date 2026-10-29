export interface ResizeColumn {
    id: string;
    label: string;
    minWidth: number;
}
export interface ColumnSizing {
    destroy(): void;
}
export function createColumnSizing(table: HTMLTableElement, options: {
    key: string;
    columns: readonly ResizeColumn[];
}): ColumnSizing;
export function enhanceResizableTable(table: HTMLTableElement, options: {
    key: string;
    columns: readonly ResizeColumn[];
}): () => void;
