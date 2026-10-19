import { Popover } from "@radix-ui/themes";
import { Cube } from "@phosphor-icons/react";
import { fmt } from "./model";
import { MATERIALS } from "./sceneModel";

export function SceneMaterials({ materials }: { materials: Record<string, number> | undefined }) {
    const entries = Object.entries(materials || {});
    return (
        <div className="spectator-materials">
            <span>
                <Cube size={13} />
                材料
            </span>
            <b>
                {materials === undefined
                    ? "未知"
                    : entries
                          .slice(0, 3)
                          .map(([id, n]) => `${MATERIALS[id] || id} ${fmt(n)}`)
                          .join(" / ") || "无"}
            </b>
            {entries.length > 3 && (
                <Popover.Root>
                    <Popover.Trigger>
                        <button className="materials-trigger" aria-label="查看全部材料">
                            全部 {entries.length} 种
                        </button>
                    </Popover.Trigger>
                    <Popover.Content className="materials-popover" maxWidth="420px">
                        <h3>当前观看位置的材料</h3>
                        <div className="material-tile-grid">
                            {entries.map(([id, n]) => (
                                <div key={id}>
                                    <Cube size={15} />
                                    <span>{MATERIALS[id] || id}</span>
                                    <b className="mono">{fmt(n)}</b>
                                </div>
                            ))}
                        </div>
                    </Popover.Content>
                </Popover.Root>
            )}
        </div>
    );
}
