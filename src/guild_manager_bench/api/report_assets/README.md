# 战报缩略图

这些文件仅随应用分发，不写入运行存档。导出时按需读取，由 `report_art.py` 去重并限制图片总预算。

- `portraits/` 来源于 `web/workbench/src/portraits/*.webp`，缩放为 160×160。
- `equipment/` 来源于 `web/assets/icons/equipment/*.webp`，缩放为 64×64。
- `equipment-map.json` 仅保留装备图标 `index.json` 的 `id → asset_id` 映射，不包含游戏数值。

当前图片使用 Chromium Canvas 的 `drawImage` 缩放，再以 `toDataURL('image/webp', 0.72)` 编码。运行时不需要图片处理依赖。更新原素材后按上述尺寸与质量重新生成对应缩略图，并运行 `tests/test_run_report.py` 检查体积预算与离线引用。

角色与装备的展示名称、数值和事件仍来自原始存档。这些插图不表示历史规则版本。
