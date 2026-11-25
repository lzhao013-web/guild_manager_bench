import "./equipment.css";

export type EquipmentStage = "t1" | "t2" | "t3";

/** full / advanced 的模板 ID 已明确包含阶级，未分阶的旧模板保持未知。 */
export function equipmentStage(templateId: string | undefined): EquipmentStage | undefined {
    return /^(t[123])_/.exec(templateId || "")?.[1] as EquipmentStage | undefined;
}

export function equipmentDescription(templateId: string | undefined) {
    const stage = equipmentStage(templateId);
    return stage ? `装备阶级 ${stage.toUpperCase()}` : "装备阶级未记录";
}

export function EquipmentBadge({ templateId }: { templateId?: string }) {
    const stage = equipmentStage(templateId);
    if (!stage) return null;
    return <span className="equipment-badge" data-equipment-stage={stage} title={equipmentDescription(templateId)}>
        {stage.toUpperCase()}
    </span>;
}
