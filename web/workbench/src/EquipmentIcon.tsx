import { SlotIcon } from "./SceneParts";
import { equipmentStage } from "./EquipmentBadge";

const images = import.meta.glob<string>("../../assets/icons/equipment/*.webp", {
    eager: true,
    import: "default",
    query: "?url",
});
export function EquipmentIcon({ templateId, slot, size = 24 }: {
    templateId?: string;
    slot: string;
    size?: number;
}) {
    const url = images[`../../assets/icons/equipment/${templateId}.webp`];
    return url ? <img className="equipment-icon" data-equipment-stage={equipmentStage(templateId)} src={url} alt="" width={size} height={size} loading="lazy" />
        : <SlotIcon slot={slot} size={size} />;
}
