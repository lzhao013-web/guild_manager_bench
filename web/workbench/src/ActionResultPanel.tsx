import type { ReactNode } from "react";
import { ActionInputs } from "./ActionInputPanel";
import { actionBrief } from "./actionModel";
import type { ActionEntry } from "./actionModel";
import { isPlan } from "./actionPlan";
import type { PlanPreviewLink } from "./actionPlan";
import { ActionPlanPanel } from "./ActionPlanPanel";
import { ToolResultPanel } from "./ToolResultPanel";
import { RecruitmentResultPanel } from "./RecruitmentResultPanel";
import { MemberActionResultPanel } from "./MemberActionResultPanel";
import { AssetResultPanel } from "./AssetResultPanel";
import { TacticsResultPanel } from "./TacticsResultPanel";
import { MonsterRefreshResultPanel } from "./MonsterRefreshResultPanel";
import { EndTurnResultPanel } from "./EndTurnResultPanel";

export function ActionResults({ entry, recordDetails, previewLink }: { entry: ActionEntry; recordDetails?: ReactNode; previewLink?: PlanPreviewLink }) {
    const summary = actionBrief(entry);
    const plan = isPlan(entry);
    const queryList = entry.kind === "query" && entry.returned.tables.some(table => !["battles", "events", "tactics"].includes(table.kind));
    const inspection = <>
        {entry.kind !== "system" && <details className="plan-disclosure" key={entry.frame.cursor}>
            <summary>输入参数</summary>
            <ActionInputs input={entry.input} />
        </details>}
        {recordDetails}
    </>;
    if (plan) return <ActionPlanPanel entry={entry} inspection={inspection} previewLink={previewLink} />;
    if (entry.event.name === "end_turn" && entry.status === "succeeded" && entry.event.result && !entry.returned.preview)
        return <EndTurnResultPanel entry={entry} inspection={inspection} />;
    const recruitment = entry.event.name === "recruit_adventurer" && entry.status === "succeeded"
        ? entry.returned.tables.find(table => table.kind === "members" && table.records.length > 0) : undefined;
    if (recruitment) return <RecruitmentResultPanel entry={entry} data={recruitment} inspection={inspection} />;
    const assetKind = entry.event.name === "craft_equipment" ? "equipment" : entry.event.name === "purchase_upgrade" ? "upgrades" : null;
    const asset = entry.status === "succeeded" && assetKind
        ? entry.returned.tables.find(table => table.kind === assetKind && table.source === "action_output" && table.records.length > 0) : undefined;
    if (asset) return <AssetResultPanel entry={entry} data={asset} inspection={inspection} />;
    if (entry.status === "succeeded" && entry.event.result && ["refresh_monsters", "preview_monster_refresh"].includes(entry.event.name))
        return <MonsterRefreshResultPanel entry={entry} inspection={inspection} />;
    if (entry.status === "succeeded" && entry.event.name === "configure_tactics")
        return <TacticsResultPanel entry={entry} inspection={inspection} />;
    if (entry.status === "succeeded" && ["equip_item", "unequip_item", "allocate_experience"].includes(entry.event.name))
        return <MemberActionResultPanel entry={entry} inspection={inspection} />;
    return <>
        {!queryList && <section className="action-result" aria-label="执行结果"><h4>{entry.returned.preview ? "预览结果" : "执行结果"}</h4>
            {summary && <p className={entry.status === "failed" ? "action-detail-warning" : "action-detail-summary"}>{summary}</p>}
        </section>}
        <ToolResultPanel entry={entry} />
        <section className="action-inspection" aria-label="排查详情">{inspection}</section>
    </>;
}
