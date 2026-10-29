import { useId, useState } from "react";
import { Popover } from "@radix-ui/themes";
import { MagnifyingGlass, Plus } from "@phosphor-icons/react";
import { STATUS } from "./model";
import { RunId } from "./RunId";
import "./run-picker.css";

interface RunOption {
    key: string;
    name: string;
    preset: string;
    status: string;
    model: string;
    aliases: string[];
}

export function RunPicker({ label, options, onSelect, disabled = false }: {
    label: string;
    options: RunOption[];
    onSelect: (id: string) => void;
    disabled?: boolean;
}) {
    const [open, setOpen] = useState(false);
    const [search, setSearch] = useState("");
    const [active, setActive] = useState<string | null>(null);
    const id = useId();
    const terms = search.trim().toLowerCase().split(/\s+/).filter(Boolean);
    const matches = options.filter(option => {
        const text = `${option.name} ${option.model} ${option.preset} ${option.key} ${option.aliases.join(" ")}`.toLowerCase();
        return terms.every(term => text.includes(term));
    });
    const visible = matches.slice(0, 50);
    const index = Math.max(0, visible.findIndex(option => option.key === active));
    function choose(key: string) {
        onSelect(key);
        setOpen(false);
    }
    return <Popover.Root open={open && !disabled} onOpenChange={value => {
        setOpen(value);
        if (value) { setSearch(""); setActive(null); }
    }}>
        <Popover.Trigger><button className="run-picker-trigger" aria-label={label} disabled={disabled} title={disabled ? "最多选择四个运行" : label}>
            <Plus size={15} />{label}
        </button></Popover.Trigger>
        <Popover.Content className="run-picker" size="1" align="start" collisionPadding={12} aria-label={label}>
            <label className="run-picker-search"><MagnifyingGlass size={16} />
                <input role="combobox" aria-label="搜索运行名称、模型、preset 或 ID" placeholder="名称、模型、preset 或 ID"
                    aria-expanded="true" aria-controls={`${id}-options`} aria-autocomplete="list"
                    aria-activedescendant={visible.length ? `${id}-${index}` : undefined}
                    value={search} onChange={event => { setSearch(event.target.value); setActive(null); }}
                    onKeyDown={event => {
                        if (event.nativeEvent.isComposing || !visible.length) return;
                        if (event.key === "Enter") { event.preventDefault(); choose(visible[index].key); }
                        if (event.key === "ArrowDown" || event.key === "ArrowUp") {
                            event.preventDefault();
                            const next = (index + (event.key === "ArrowDown" ? 1 : -1) + visible.length) % visible.length;
                            setActive(visible[next].key);
                            document.getElementById(`${id}-${next}`)!.scrollIntoView({ block: "nearest" });
                        }
                    }} />
            </label>
            <div id={`${id}-options`} className="run-picker-options" role="listbox" aria-label="可添加的运行">
                {visible.map((option, i) => <button type="button" key={option.key} id={`${id}-${i}`} role="option"
                    aria-selected={i === index} tabIndex={-1} onClick={() => choose(option.key)}>
                    <strong>{option.name}</strong><RunId id={option.key} />
                    <small>{option.preset || "未记录 preset"} · {STATUS[option.status as keyof typeof STATUS] || option.status}{option.key.startsWith("archive:") ? " · CLI 存档标记" : ""}</small>
                </button>)}
            </div>
            <p className="run-picker-count" role="status">{!matches.length ? "没有匹配的运行" : matches.length > visible.length
                ? `匹配 ${matches.length} 个，显示前 ${visible.length} 个，请输入关键词缩小范围` : `${matches.length} 个可选运行`}</p>
        </Popover.Content>
    </Popover.Root>;
}
