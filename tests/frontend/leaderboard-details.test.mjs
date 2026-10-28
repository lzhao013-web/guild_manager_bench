import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import { materialLabel, slotLabel, skillKind, skillDetailText, skillEffectText, skillConditionText,
  renderRunUpgrades, renderTactics, renderDevelopment } from '../../web/leaderboard/details.mjs';

const automatic = {
  skill_id: 'talent_mana_reflux', kind: 'active', trigger: 'action_start', once_per_battle: true,
  condition: {type:'self_mp_pct_lte', value:.25},
  effects: [{type:'mp_restore_percent', value:.3, minimum:12, target:'self'}],
};

test('自动回复不冒充主动选择，百分比和最低回复有中文说明', () => {
  assert.equal(skillKind(automatic), '自动回复');
  const text = skillDetailText(automatic);
  for (const expected of ['自身行动开始','状态结算后','不占主动选择','不额外普攻','每战一次','30%最大MP','至少12MP']) assert.ok(text.includes(expected), text);
  assert.doesNotMatch(text, /mp_restore_percent|附赠/);
  assert.equal(skillKind({...automatic, trigger:undefined}), '主动');
});

test('攻击比例伤害和状态持续时间符合战斗语义', () => {
  assert.equal(skillEffectText({type:'atk_ratio_damage',value:2.1}), '造成攻击力210%的无视防御伤害');
  const status = skillEffectText({type:'apply_status',status:{name:'法幕',duration:6,effects:[{type:'heal_percent',value:.08}]}});
  assert.match(status, /持续持有者6次行动/);
  assert.match(status, /每次行动开始恢复8%/);
  assert.doesNotMatch(status, /回合/);
  assert.match(skillConditionText({type:'action_index_gte',value:9}), /双方累计行动/);
});

test('印记、核心和高级材料名称使用中文', () => {
  assert.equal(slotLabel('ultimate'), '终极印记');
  assert.equal(materialLabel('siege_core'), '破城核心');
  assert.equal(materialLabel('aegis_core'), '不屈核心');
  assert.equal(materialLabel('renewal_core'), '轮回核心');
  assert.equal(materialLabel('mithril_shard'), '秘银碎片');
});

test('天赋显示经验材料、分支前置和经营效果，不为旧full补字段', () => {
  const html = renderRunUpgrades([{upgrade_id:'a',name:'教范',branch:'economy',tier:2,gold_cost:100,experience_cost:80,
    material_costs:{herb:2,arcane_dust:2},required_upgrade_ids:['base'],required_upgrade_ids_names:['训练场'],
    exclusive_upgrade_ids_names:['另一终阶'],hunt_experience_bonus_percent:6}]);
  for (const expected of ['经营','2阶','100金币','80经验','草药 2','奥术之尘 2','前置：训练场','互斥：另一终阶','讨伐经验增加6%']) assert.ok(html.includes(expected), html);
  const old = renderRunUpgrades([{name:'旧升级',gold_cost:20,stats:{attack:3}}]);
  assert.match(old, /20金币/);
  assert.doesNotMatch(old, /经验|材料：|经营|前置/);
});

test('战术名称取自记录，未记录不伪造默认配置', () => {
  const html = renderTactics({skill_priority:['star'],disabled_skills:['missile'],available_active_skills:[{skill_id:'star',name:'星陨'},{skill_id:'missile',name:'飞弹'}]});
  assert.match(html, /优先：星陨（其余默认）/);
  assert.match(html, /停用：飞弹/);
  assert.equal(renderTactics(undefined), '');
});

test('所有记录文本均转义，包括材料、技能、分支和事件', () => {
  const payload = '<img src=x onerror=bad()>';
  for (const html of [
    renderRunUpgrades([{name:payload,branch:payload,gold_cost:1,skills:[{name:payload,effects:[{type:'apply_status',status:{name:payload,effects:[]}}]}]}]),
    renderTactics({skill_priority:[payload],disabled_skills:[],available_active_skills:[]}),
  ]) {
    assert.doesNotMatch(html, /<img/);
    assert.match(html, /&lt;img/);
  }
});

function development() {
  return {challenges:{defeated:1,total:3,entries:[{name:'巨像',defeated:true,turn:30}]},
    refresh:{previews:2,paid:3,failed:1,unconfirmed:1,known_gold_spent:20,unknown_cost_count:1},
    seals:[{name:'星陨印记',adventurer_name:'魔弹',recipe_materials:{siege_core:1}}],
    events:[{type:'upgrade',turn:5,name:'教范',costs:{gold:100,experience:80,materials:{herb:2}}}],unlocated_purchases:1};
}

test('经营摘要区分预览、付费、失败和未知，不把未知费用补成零', () => {
  const html = renderDevelopment(development(), 'events');
  for (const text of ['1 / 3','刷新预览','付费刷新','已知20金币，另1次费用未记录','刷新失败 1','刷新结果未确认 1','破城核心 1','星陨印记','T30击败','T5','80经验','草药 2']) assert.ok(html.includes(text), html);
  assert.equal(renderDevelopment(undefined, 'events'), '');
  const partial = development(); partial.challenges.total=null; partial.challenges.entries[0].turn=null;
  assert.match(renderDevelopment(partial, 'events'), /回合未记录/);
  assert.doesNotMatch(renderDevelopment(partial, 'events'), /1 \/ 3/);
  partial.events[0].costs = {gold:80,experience:null,materials:null};
  assert.match(renderDevelopment(partial, 'events'), /80金币（部分未记录）/);
});

test('全部工具可查看，新工具使用中文且保留内部名称供核对', () => {
  const source = readFileSync(new URL('../../web/leaderboard/app.js', import.meta.url), 'utf8');
  const labels = source.slice(source.indexOf('function toolLabel('),source.indexOf('// Tab Navigation'));
  const tools = source.slice(source.indexOf('function toolBreakdownItems('),source.indexOf('function rankContributorItems('));
  const context = vm.createContext({esc:x=>String(x), fmtInt:x=>String(x)});
  vm.runInContext(labels + tools, context);
  for (const name of ['get_forecast','get_battle_log','configure_tactics','refresh_monsters','preview_monster_refresh']) assert.notEqual(context.toolLabel(name), name);
  assert.equal(context.toolLabel('preview_team_power'),'方案试算');
  const names = Array.from({length:13}, (_,i)=>`tool-${i}`);
  const html = context.renderToolBreakdown({by_name:Object.fromEntries(names.map(name=>[name,1]))});
  assert.doesNotMatch(html, /<details/);
  assert.equal((html.match(/class="tool-chip"/g)||[]).length,13);
});

test('长角色弹层限制在按钮上下可用区域，不覆盖触发按钮', () => {
  const source = readFileSync(new URL('../../web/leaderboard/app.js', import.meta.url), 'utf8');
  const position = source.slice(source.indexOf('function positionAdventurerTooltip('), source.indexOf('function renderAdventurerTooltip('));
  for (const [width,height,top] of [[1536,872,100],[1536,872,500],[390,844,760]]) {
    const context = vm.createContext({window:{innerWidth:width,innerHeight:height},hideAdventurerTooltip:()=>assert.fail('visible anchor cannot hide')});
    vm.runInContext(position,context);
    const anchor = {getBoundingClientRect:()=>({top,bottom:top+14,left:200,width:42})};
    const tooltip = {style:{},offsetWidth:Math.min(450,width-24),get offsetHeight(){return Math.min(1500,parseFloat(this.style.maxHeight));}};
    context.positionAdventurerTooltip(anchor,tooltip);
    const y=parseFloat(tooltip.style.top),x=parseFloat(tooltip.style.left),h=tooltip.offsetHeight;
    assert.ok(y>=11 && y+h<=height-11);
    assert.ok(x>=11 && x+tooltip.offsetWidth<=width-11);
    assert.ok(y>=top+14+9 || y+h<=top-9);
  }
});
