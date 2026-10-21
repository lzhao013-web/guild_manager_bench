import { SlotIcon } from "./SceneParts";

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
    return url ? <img className="equipment-icon" src={url} alt="" width={size} height={size} loading="lazy" />
        : <SlotIcon slot={slot} size={size} />;
}
