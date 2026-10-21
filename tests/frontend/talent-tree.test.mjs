import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

function render(upgrades) {
  const elements = new Map();
  const context = vm.createContext({
    URLSearchParams,
    window: { location: { search: '' }, addEventListener() {} },
    document: { getElementById(id) {
      if (!elements.has(id)) elements.set(id, { innerHTML: '' });
      return elements.get(id);
    } },
    observation: { finished: false, global_upgrades: upgrades },
  });
  vm.runInContext(readFileSync(new URL('../../web/static/app.js', import.meta.url), 'utf8'), context);
  vm.runInContext('state.observation = observation; renderUpgrades(observation);', context);
  return elements.get('upgrades').innerHTML;
}

const legacy = {
  upgrade_id: 'training', name: '训练场', description: '', gold_cost: 65,
  stats: { attack: 3 }, party_size_bonus: 0, skills: [], required_upgrade_ids: [],
  unlocked: false, can_purchase: true, missing: {},
};
const talent = {
  ...legacy, upgrade_id: 'war', name: '战争学院', branch: 'martial', tier: 4,
  experience_cost: 1500, material_costs: { dragon_scale: 18 },
  exclusive_upgrade_ids: ['arcane'], craft_gold_discount_percent: 0,
  hunt_experience_bonus_percent: 0, can_purchase: false,
  missing: { experience: 1000, materials: { dragon_scale: 8 }, exclusive_upgrade_ids: ['arcane'] },
};

test('talent UI exposes resource deficits and disables mutually exclusive choices', () => {
  const html = render([talent, { ...talent, upgrade_id: 'arcane', name: '秘法结社', branch: 'arcane', unlocked: true, missing: {}, exclusive_upgrade_ids: ['war'] }]);
  for (const text of ['武备天赋', '奥术天赋', '公共经验 1500', '龙鳞:18', '公共经验 1000', '龙鳞:8', '互斥锁定', '秘法结社', '不可洗点']) {
    assert.ok(html.includes(text), text);
  }
  assert.match(html, /disabled[^>]+purchaseUpgrade\('war'\)/);
});

test('legacy upgrades retain their original grouping and gold-only presentation', () => {
  const html = render([legacy]);
  assert.ok(html.includes('战斗强化'));
  assert.ok(html.includes('金币 65'));
  assert.ok(!html.includes('公共经验'));
  assert.ok(!html.includes('互斥'));
});

test('economic effects are visible and data is HTML-escaped', () => {
  const html = render([{ ...talent, branch: 'economy', name: '<research>', description: '<unsafe>', craft_gold_discount_percent: 25, hunt_experience_bonus_percent: 15, hunt_gold_bonus_percent: 10, elite_boss_gold_bonus_percent: 18, monster_refresh_discount_percent: 20 }]);
  assert.ok(html.includes('经营支线'));
  assert.ok(html.includes('降低 25%'));
  assert.ok(html.includes('增加 15%'));
  assert.ok(html.includes('金币增加 10%'));
  assert.ok(html.includes('金币额外增加 18%'));
  assert.ok(html.includes('刷新基础价降低 20%'));
  assert.ok(html.includes('&lt;research&gt;'));
  assert.ok(html.includes('&lt;unsafe&gt;'));
});
