import test from 'node:test';
import assert from 'node:assert/strict';
import { normalizeReplay } from '../../web/replay/compare-model.mjs';
import { buildProfile } from '../../web/replay/profile-model.mjs';
import { observation, tool, turn, replay } from './compare-fixtures.mjs';

const profile = source => buildProfile(normalizeReplay(source));
function train(amount, id = 1, extra = {}) { return tool('allocate_experience', { adventurer_id: id, amount }, extra); }
function spend(name, cost, extra = {}) {
  return tool(name, {}, { observation_after: observation(1, 100 - cost), ...extra });
}

test('summarizes training by canonical adventurer identity and gives evidence per allocation', () => {
  const before = observation(1);
  before.adventurers.push({ adventurer_id: 'hero-b', name: '先锋', template_id: 'warrior', level: 1 });
  const source = replay([{ ...turn(1, 0, [
    train(60, 1, { observation_after: before }), train(20, 2, { observation_after: before }),
  ]), observation_before: before }]);
  const result = profile(source);
  assert.equal(result.training.knownAmount, 80);
  assert.deepEqual(result.training.recipients.map(r => [r.id, r.amount]), [['hero-a', 60], ['hero-b', 20]]);
  assert.notEqual(result.training.recipients[0].label, result.training.recipients[1].label);
  assert.equal(result.training.recipients[0].events[0].turn, 1);
  assert.equal(result.training.recipients[0].events[0].step, 0);
  assert.match(result.insights[0].text, /75%/);
});

test('does not combine unresolved numeric refs across turns into one imaginary hero', () => {
  const a = turn(1, 0, [train(30)]), b = turn(2, 0, [train(40)]);
  a.observation_before = null;
  b.observation_before = null;
  const result = profile(replay([a, b]));
  assert.equal(result.training.recipients.length, 0);
  assert.equal(result.training.unresolvedAmount, 70);
  assert.equal(result.training.unresolvedTargets, 2);
  assert.match(result.insights[0].text, /无法识别/);
});

test('keeps missing allocation amounts unknown and does not invent shares', () => {
  const result = profile(replay([turn(1, 0, [tool('allocate_experience', { adventurer_id: 1 })])]));
  assert.equal(result.training.knownAmount, 0);
  assert.equal(result.training.unknownAmounts, 1);
  assert.match(result.insights[0].text, /未知/);
});

test('gold spending comes from consecutive snapshots, not final balance or guessed prices', () => {
  const result = profile(replay([turn(1, 0, [
    spend('recruit_adventurer', 30),
    tool('purchase_upgrade', { upgrade_id: 'unknown' }),
    spend('craft_equipment', 60),
  ])]));
  assert.equal(result.investment.knownGold, 30);
  assert.equal(result.investment.unknownCosts, 2);
  assert.equal(result.investment.calls, 3);
});

test('buckets investment by declared game horizon, not observed run length', () => {
  const source = replay([turn(1, 0, [spend('recruit_adventurer', 20)]), turn(4, 0, [spend('purchase_upgrade', 40)])]);
  source.turns[0].observation_before.max_turns = 9;
  const result = profile(source);
  assert.deepEqual(result.investment.phases.map(p => [p.start, p.end, p.knownGold]), [[1, 3, 20], [4, 6, 40], [7, 9, 0]]);
  assert.equal(result.investment.phases[2].observedRounds, 0);
  assert.equal(result.investment.first.purchase_upgrade.turn, 4);
});

test('completed main attempts drive economic profile; all attempts drive interaction statistics', () => {
  const failed = { ...turn(1, null), status: 'failed', steps: [train(5)] };
  const main = turn(1, 10, [train(50), tool('get_party')]);
  const source = replay([failed, main]);
  const result = profile(source);
  assert.equal(result.training.knownAmount, 50);
  assert.equal(result.coverage.completed, 1);
  assert.equal(result.coverage.attempts, 2);
  assert.equal(result.coverage.failedAttempts, 1);
  assert.equal(result.habits.totalCalls, 4);
});

test('explicit failures and unknown outcomes are never counted as successful training', () => {
  const failed = train(60, 1, { result: { ok: false }, content: '失败' });
  const unknown = train(50, 1, { result: {}, content: '' });
  const result = profile(replay([turn(1, 0, [failed, unknown])]));
  assert.equal(result.training.calls, 0);
  assert.equal(result.coverage.unknownEconomicOutcomes, 1);
  assert.equal(result.habits.failed, 1);
  assert.equal(result.habits.unknown, 1);
});

test('tracks next calls after failures without claiming that a problem was fixed', () => {
  const fail = args => tool('equip_item', args, { result: { ok: false }, content: '失败 equip_item' });
  const source = replay([turn(1, 0, [
    fail({ adventurer_id: 1, equipment_instance_id: 'x' }),
    fail({ adventurer_id: 1, equipment_instance_id: 'x' }),
    tool('equip_item', { adventurer_id: 1, equipment_instance_id: 'y' }),
    fail({ adventurer_id: 1, equipment_instance_id: 'x' }), tool('get_party'),
  ])]);
  const result = profile(source);
  assert.deepEqual(result.habits.followups.map(f => f.kind), ['same', 'arguments', 'tool']);
  assert.equal(result.habits.followups[0].event.step, 0);
  assert.equal(result.habits.followups[1].next.step, 2);
});

test('memo observations only describe successful writes, not adherence to plans', () => {
  const source = replay([turn(1, 0, [tool('write_memo', { content: '下回合招募' }), tool('get_monsters')]), turn(2, 0)]);
  const result = profile(source);
  assert.equal(result.habits.memos.length, 1);
  assert.equal(result.habits.memos[0].turn, 1);
  assert.equal(result.habits.groups.find(g => g.key === 'queries').count, 1);
});

test('equipment replacement is different from first equip and missing snapshots', () => {
  const before = observation(1);
  before.equipment_inventory = [{ instance_id: 'old', name: '木剑', slot: 'weapon', equipped_by: 'hero-a' }, { instance_id: 'new', name: '铁剑', slot: 'weapon', equipped_by: null }];
  before.adventurers[0].equipment = [{ slot: 'weapon', instance_id: 'old' }];
  const equipped = structuredClone(before);
  equipped.adventurers[0].equipment = [{ slot: 'weapon', instance_id: 'new' }];
  const equip = tool('equip_item', { adventurer_id: 1, equipment_instance_id: 2 }, { observation_after: equipped });
  const source = replay([{ ...turn(1, 0, [equip]), observation_before: before }]);
  const result = profile(source);
  assert.equal(result.equipment.equipped, 1);
  assert.equal(result.equipment.swaps.length, 1);
  assert.equal(result.equipment.swaps[0].turn, 1);
  delete before.adventurers[0].equipment;
  assert.equal(profile(source).equipment.unknownSwaps, 1);
});

test('unknown ownership is not classified as unequipped inventory', () => {
  const record = turn(1, 0);
  record.steps.at(-1).observation_after.equipment_inventory = [{ instance_id: 'a', name: '未知装备' }, { instance_id: 'b', name: '铁剑', equipped_by: null }];
  const result = profile(replay([record]));
  assert.equal(result.equipment.snapshot.free.length, 1);
  assert.equal(result.equipment.snapshot.unknown, 1);
});

test('resource streaks do not cross missing turns, missing data, or zero balances', () => {
  const source = replay([turn(1, 0), turn(2, 0), turn(4, 0), turn(5, 0), turn(6, 0)]);
  source.turns[3].steps.at(-1).observation_after.experience_pool = null;
  source.turns[4].steps.at(-1).observation_after.experience_pool = 0;
  const result = profile(source);
  assert.deepEqual(result.resources.longestXp, { start: 1, end: 2, length: 2 });
  assert.equal(result.resources.points.length, 5);
});

test('interrupted partial state is not substituted for an earlier completed boundary', () => {
  const first = turn(1, 0);
  delete first.steps.at(-1).observation_after;
  const source = replay([first], { status: 'interrupted', final_observation: observation(2, 999) });
  const result = profile(source);
  assert.equal(result.resources.last.gold, null);
});

test('empty or failed-only archives expose incomplete coverage instead of a fictional style', () => {
  const failed = { ...turn(1, 0), status: 'failed', steps: [train(50)] };
  const result = profile(replay([failed], { status: 'failed' }));
  assert.equal(result.coverage.completed, 0);
  assert.equal(result.resources.last, null);
  assert.equal(result.training.calls, 0);
  assert.ok(result.insights.slice(0, 3).every(i => i.text.includes('已完成回合')));
});

test('absence statements link to recorded rounds instead of becoming untraceable claims', () => {
  const result = profile(replay([turn(1, 0), turn(2, 0)]));
  assert.ok(result.insights.every(i => i.events.length > 0));
  assert.deepEqual(result.insights[0].events.map(e => e.turn), [1, 2]);
});

test('profile generation leaves replay and score data unchanged', () => {
  const source = replay([turn(1, 123, [train(50)])], { score: { rank_score: 123 } });
  const original = structuredClone(source);
  profile(source);
  assert.deepEqual(source, original);
});
