import { ArrowSquareOut, Coins, Lock } from "@phosphor-icons/react";
import { fmt } from "./model";
import { MATERIALS, requirementLines, SLOTS } from "./sceneModel";
import { SkillCards, StatTiles } from "./SceneParts";
import { EquipmentIcon } from "./EquipmentIcon";
import type { CraftingRecipe, Observation } from "./types";

export function RecipeDetails({ recipe, observation, onInventory }: {
    recipe: CraftingRecipe;
    observation: Observation;
    onInventory: (slot: string) => void;
}) {
    const missing = requirementLines(recipe.missing);
    return <div className="scene-entity-detail recipe-detail">
        <div className="scene-entity-hero">
            <div className="entity-item-glyph"><EquipmentIcon templateId={recipe.output_template_id} slot={recipe.output_slot} size={72} /></div>
            <div><h3>{recipe.output_name}</h3><span className="scene-tag">{SLOTS[recipe.output_slot] || recipe.output_slot}</span>
                <p className={recipe.can_craft ? "scene-positive" : "scene-note"}>{recipe.can_craft ? "当前满足制作条件" : "当前未满足制作条件"}</p>
                <p className="entity-price"><Coins size={15} /> 当前费用 <b>{fmt(recipe.gold_cost)}</b> 金币
                    {recipe.base_gold_cost !== undefined && recipe.base_gold_cost !== recipe.gold_cost && <small>原价 {fmt(recipe.base_gold_cost)}</small>}</p>
                <p className="scene-note">适用职业：{recipe.output_allowed_class_names.join("、") || "不限职业"}</p>
                <div className="scene-missing">{missing.map(line => <span key={line}><Lock size={12} />{line}</span>)}</div>
            </div>
        </div>
        <section className="recipe-materials"><h3>制作材料</h3>
            {Object.keys(recipe.material_costs).length ? <dl>{Object.entries(recipe.material_costs).map(([id, count]) => <div key={id}>
                <dt>{MATERIALS[id] || id}</dt><dd>需要 {fmt(count)} / 持有 {fmt(observation.materials[id] || 0)}</dd>
            </div>)}</dl> : <p className="scene-note">无需材料</p>}
        </section>
        <h3>成品属性加成</h3><StatTiles stats={recipe.output_stats} modifier />
        <h3>成品技能</h3><SkillCards skills={recipe.output_skills} />
        <button className="scene-text-action" onClick={() => onInventory(recipe.output_slot)}>查看同槽库存<ArrowSquareOut size={13} /></button>
    </div>;
}
