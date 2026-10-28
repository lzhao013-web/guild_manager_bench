// 排行榜明细只解释记录中的字段，不从当前游戏配置补值。
const esc = value => String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'}[char]));
const number = value => Number(value).toLocaleString('zh-CN', { maximumFractionDigits: 2 });
const percent = value => `${Math.round(value * 100)}%`;
const missing = '未记录';

export function materialLabel(id) {
  return {iron_ore:'铁矿石', wood:'木材', leather:'皮革', herb:'草药', bone:'骨头', beast_hide:'兽皮', sharp_claw:'利爪', arcane_dust:'奥术之尘', spider_silk:'蛛丝', mithril_shard:'秘银碎片', dragon_scale:'龙鳞', dragon_blood:'龙血', demon_core:'恶魔核心', soul_shard:'灵魂碎片', siege_core:'破城核心', aegis_core:'不屈核心', renewal_core:'轮回核心'}[id] || id;
}
export function branchLabel(id) {
  return {common:'公共', economy:'经营', martial:'武备', survival:'生存', arcane:'奥术'}[id] || id || '升级';
}
export function statLabel(key) {
  return {hp:'HP', mp:'MP', attack:'攻击', defense:'防御', speed:'速度', recovery:'战后回血', mp_recovery:'战后回魔'}[key] || key || '属性';
}
export function slotLabel(slot) {
  return {main_hand:'右手', off_hand:'左手', two_hand:'双手', hand:'单手', boots:'鞋子', helmet:'头盔', armor:'护甲', accessory:'饰品', ultimate:'终极印记'}[slot] || slot || '装备';
}
export function statModifierText(stats) {
  const items = Object.entries(stats || {}).filter(([,value]) => Number(value) !== 0);
  return items.length ? items.map(([key,value]) => `${statLabel(key)} ${Number(value)>0?'+':''}${value}`).join(' · ') : '无属性加成';
}
export function skillKind(skill) {
  return skill.trigger === 'action_start' ? '自动回复' : skill.kind === 'active' ? '主动' : '被动';
}
export function skillConditionText(condition) {
  if (!condition || condition.type === 'always') return '';
  const pct = condition.value == null ? missing : percent(condition.value);
  const labels = {
    self_hp_pct_lte:`自身HP ≤ ${pct}`, self_hp_pct_gte:`自身HP ≥ ${pct}`,
    target_hp_pct_lte:`目标HP ≤ ${pct}`, target_hp_pct_gte:`目标HP ≥ ${pct}`,
    self_mp_pct_lte:`自身MP ≤ ${pct}`, self_mp_pct_gte:`自身MP ≥ ${pct}`,
    target_mp_pct_lte:`目标MP ≤ ${pct}`, target_mp_pct_gte:`目标MP ≥ ${pct}`,
    action_index_lte:`双方累计行动序号 ≤ ${condition.value}`,
    action_index_gte:`双方累计行动序号 ≥ ${condition.value}`,
  };
  if (labels[condition.type]) return labels[condition.type];
  if (condition.type === 'all' || condition.type === 'any') {
    return `（${(condition.conditions || []).map(skillConditionText).filter(Boolean).join(condition.type === 'all' ? ' 且 ' : ' 或 ')}）`;
  }
  return condition.type || '';
}
export function skillEffectText(effect, statusContext = false) {
  if (!effect || typeof effect !== 'object') return '';
  const v = effect.value;
  const tick = statusContext ? '持有者每次行动开始' : '';
  if (effect.type === 'damage_multiplier') return `扣防后普攻伤害 ×${v}`;
  if (effect.type === 'damage_bonus') return `普攻伤害 +${v}`;
  if (effect.type === 'true_damage') return statusContext ? `${tick}受到${v}点无视防御伤害` : `造成${v}点无视防御伤害`;
  if (effect.type === 'atk_ratio_damage') return `造成攻击力${percent(v)}的无视防御伤害`;
  if (effect.type === 'self_damage') return `自身损失${v}HP`;
  if (effect.type === 'heal') return `${tick || (effect.target === 'self' ? '自身' : '目标')}恢复${v}HP`;
  if (effect.type === 'heal_percent') return `${tick || (effect.target === 'self' ? '自身' : '目标')}恢复${percent(v)}最大HP`;
  if (effect.type === 'mp_restore') return `${tick || (effect.target === 'self' ? '自身' : '目标')}恢复${v}MP`;
  if (effect.type === 'mp_restore_percent') return `${tick || (effect.target === 'self' ? '自身' : '目标')}恢复${percent(v)}最大MP${effect.minimum ? `，至少${effect.minimum}MP` : ''}`;
  if (effect.type === 'stat_bonus') return `${statLabel(effect.stat)} ${v>0?'+':''}${v}`;
  if (effect.type === 'stat_multiplier') return `${statLabel(effect.stat)} ×${v}`;
  if (effect.type === 'apply_status' && effect.status) {
    const status = effect.status;
    const duration = status.duration ? `，持续持有者${status.duration}次行动` : '';
    const effects = (status.effects || []).map(e => skillEffectText(e, true)).filter(Boolean).join('，');
    return `对${effect.target === 'self' ? '自身' : '目标'}施加${status.name || '状态'}${duration}${effects ? `（${effects}）` : ''}`;
  }
  return `${effect.type || '效果'}${v != null ? ` ${v}` : ''}`;
}
export function skillDetailText(skill) {
  const parts = [];
  if (skill.trigger === 'action_start') parts.push('自身行动开始、状态结算后存活时自动触发，不占主动选择，不额外普攻');
  if (skill.mp_cost > 0) parts.push(`消耗${skill.mp_cost}MP`);
  if (skill.free && skill.trigger !== 'action_start') parts.push('即时，附赠普攻');
  if (skill.once_per_battle) parts.push('每战一次');
  const condition = skillConditionText(skill.condition);
  if (condition) parts.push(`条件：${condition}`);
  (skill.effects || []).map(effect => skillEffectText(effect)).filter(Boolean).forEach(text => parts.push(text));
  return parts.join('，') || '无额外说明';
}
function materialsText(materials) {
  return Object.entries(materials || {}).filter(([,amount]) => amount > 0).map(([id,amount]) => `${materialLabel(id)} ${number(amount)}`).join('、');
}
function costText(costs) {
  const parts = [];
  if (costs.gold != null) parts.push(`${number(costs.gold)}金币`);
  if (costs.experience != null) parts.push(`${number(costs.experience)}经验`);
  const materials = materialsText(costs.materials);
  if (materials) parts.push(materials);
  if (!parts.length) return '费用未记录';
  return parts.join(' · ') + (Object.values(costs).some(value => value === null) ? '（部分未记录）' : '');
}
function upgradeEffectText(upgrade) {
  const parts = [];
  const stats = statModifierText(upgrade.stats);
  if (stats !== '无属性加成') parts.push(stats);
  if (upgrade.party_size_bonus > 0) parts.push(`队伍上限 +${upgrade.party_size_bonus}`);
  for (const [key,label] of Object.entries({craft_gold_discount_percent:'制作金币费用降低', hunt_experience_bonus_percent:'讨伐经验增加', hunt_gold_bonus_percent:'讨伐金币增加', elite_boss_gold_bonus_percent:'精英／首领金币增加', monster_refresh_discount_percent:'刷新基础价降低'})) {
    if (upgrade[key] > 0) parts.push(`${label}${upgrade[key]}%`);
  }
  for (const skill of upgrade.skills || []) parts.push(`${skill.name || skill.skill_id}：${skillDetailText(skill)}`);
  return parts.join('；') || upgrade.description || '已解锁';
}
export function renderRunUpgrades(upgrades) {
  if (!Array.isArray(upgrades) || !upgrades.length) return '';
  const groups = new Map();
  for (const upgrade of upgrades) {
    const branch = upgrade.branch || '';
    if (!groups.has(branch)) groups.set(branch, []);
    groups.get(branch).push(upgrade);
  }
  return [...groups].map(([branch,items]) => `<section class="upgrade-branch">${branch ? `<h4>${esc(branchLabel(branch))}</h4>` : ''}<div class="upgrade-list">${items.map(upgrade => {
    const name = upgrade.name || upgrade.upgrade_id || '未知升级';
    const dependencies = [];
    for (const [key,label] of [['required_upgrade_ids','前置'],['exclusive_upgrade_ids','互斥']]) {
      const names = upgrade[`${key}_names`] || upgrade[key];
      if (names?.length) dependencies.push(`${label}：${names.join('、')}`);
    }
    return `<div class="upgrade-item" title="${esc(upgrade.description || '')}">
      <div class="upgrade-item-head"><span class="upgrade-item-name">${esc(name)}${upgrade.tier ? ` · ${upgrade.tier}阶` : ''}</span>
      <span class="upgrade-item-cost">${esc(costText({gold:upgrade.gold_cost, experience:upgrade.experience_cost}))}</span></div>
      ${upgrade.material_costs && Object.keys(upgrade.material_costs).length ? `<p class="upgrade-item-effect">材料：${esc(materialsText(upgrade.material_costs))}</p>` : ''}
      <div class="upgrade-item-effect">${esc(upgradeEffectText(upgrade))}</div>
      ${dependencies.length ? `<div class="upgrade-item-effect">${esc(dependencies.join(' · '))}</div>` : ''}
    </div>`;
  }).join('')}</div></section>`).join('');
}
export function renderTactics(tactics) {
  if (!tactics) return '';
  const names = new Map((tactics.available_active_skills || []).map(skill => [skill.skill_id, skill.name || skill.skill_id]));
  const name = id => names.get(id) || id;
  const priority = tactics.skill_priority.length ? `${tactics.skill_priority.map(name).join(' → ')}（其余默认）` : '默认';
  const disabled = tactics.disabled_skills.length ? tactics.disabled_skills.map(name).join('、') : '无';
  return `<section class="adventurer-tooltip-section"><h4>战术配置</h4><p class="tactics-line">优先：${esc(priority)}</p><p class="tactics-line">停用：${esc(disabled)}</p></section>`;
}
function developmentEventText(event) {
  if (event.type === 'challenge') return `击败${event.name || event.challenge_id} · ${event.adventurer_name || '出战者未记录'}${event.materials ? ` · 获得${materialsText(event.materials)}` : ''}`;
  if (event.type === 'upgrade') return `购买${event.name} · ${costText(event.costs)}`;
  if (event.type === 'seal_craft') return `制作${event.name} · ${costText(event.costs)}`;
  if (event.type === 'seal_equip') return `${event.adventurer_name}装备${event.name}`;
  return `付费刷新 · ${event.gold == null ? '费用未记录' : `${number(event.gold)}金币`} · ${event.locked_count == null ? '锁定数量未记录' : `锁定${event.locked_count}个目标`}`;
}
export function renderDevelopment(development, eventsKey) {
  if (!development) return '';
  const {challenges,refresh,seals,events} = development;
  const progress = challenges.total == null ? `已击败${challenges.defeated}个挑战` : `${challenges.defeated} / ${challenges.total}`;
  const fees = refresh.unknown_cost_count ? `已知${number(refresh.known_gold_spent)}金币，另${refresh.unknown_cost_count}次费用未记录` : `${number(refresh.known_gold_spent)}金币`;
  return `<div class="run-card-section development-summary"><div class="run-section-title">经营摘要</div>
    <div class="development-metrics"><span>挑战 <strong>${esc(progress)}</strong></span><span>刷新预览 <strong>${refresh.previews}</strong> 次</span><span>付费刷新 <strong>${refresh.paid}</strong> 次</span><span>刷新支出 <strong>${esc(fees)}</strong></span>${refresh.failed ? `<span>刷新失败 ${refresh.failed} 次</span>` : ''}${refresh.unconfirmed ? `<span>刷新结果未确认 ${refresh.unconfirmed} 次</span>` : ''}</div>
    <div class="challenge-summary">${challenges.entries.map(entry => `<span>${esc(entry.name || entry.challenge_id)}：${entry.defeated ? (entry.turn == null ? '已击败，回合未记录' : `T${entry.turn}击败`) : '未击败'}</span>`).join('')}</div>
    <div class="seal-summary">${seals.length ? seals.map(seal => `<span>${esc(materialsText(seal.recipe_materials))}${seal.recipe_materials ? ' → ' : ''}${esc(seal.name)} · ${esc(seal.adventurer_name || '库存')}</span>`).join('') : '<span>未记录印记</span>'}</div>
    ${events.length ? `<details class="development-events" data-fold-key="${esc(eventsKey)}"><summary>经营记录（已完成回合，${events.length}项）</summary><ol>${events.map(event => `<li><span>T${event.turn}</span>${esc(developmentEventText(event))}</li>`).join('')}</ol></details>` : ''}
    ${development.unlocated_purchases ? `<p class="upgrade-item-effect">另有${development.unlocated_purchases}次成功购买缺少完整前后快照。</p>` : ''}
  </div>`;
}
