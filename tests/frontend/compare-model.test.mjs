import test from 'node:test';
import assert from 'node:assert/strict';
import { normalizeReplay, compareReplays } from '../../web/replay/compare-model.mjs';

const clone = value => structuredClone(value);
import { observation, tool, turn, replay } from './compare-fixtures.mjs';
const compare = (a, b) => compareReplays(normalizeReplay(a), normalizeReplay(b));

// 所有样例都是构造的记录，分数只用于验证展示与对齐，不代表真实模型评测。
test('aligns by actual turn number, including gaps and unequal run lengths', () => {
  const result = compare(replay([turn(1, 10), turn(3, 40)]), replay([turn(2, 20), turn(3, 30)]));
  assert.deepEqual(result.rounds.map(r => r.number), [1, 2, 3]);
  assert.equal(result.rounds[0].right, null);
  assert.equal(result.rounds[1].left, null);
  assert.equal(result.rounds[2].gap, 10);
  assert.equal(result.rounds[2].gapGrowth, null);
});

test('keeps failed attempts separate and does not count retried actions twice', () => {
  const failed = { ...turn(1, null, [tool('recruit_adventurer', { candidate_id: 1 })]), status: 'failed', failure_reason: 'network', steps: [] };
  const success = turn(1, 10, [tool('recruit_adventurer', { candidate_id: 1 })]);
  const result = compare(replay([failed, success]), replay([turn(1, 10)]));
  const left = result.rounds[0].left;
  assert.equal(result.rounds.length, 1);
  assert.equal(left.attempts.length, 2);
  assert.equal(left.selected.counts.recruit_adventurer, 1);
  assert.ok(result.rounds[0].hasFailure);
});

test('missing scores stay unknown and zero is a real score', () => {
  const result = compare(replay([turn(1, null), turn(2, 0)]), replay([turn(1, 100), turn(2, 0)]));
  assert.equal(result.rounds[0].gap, null);
  assert.equal(result.rounds[1].gap, 0);
  assert.equal(result.rounds[1].gapGrowth, null);
  assert.equal(result.rounds[1].left.scoreDelta, null);
});

test('finds the largest widening of absolute score gap, not simply the largest score', () => {
  const result = compare(replay([turn(1, 100), turn(2, 120), turn(3, 130)]), replay([turn(1, 95), turn(2, 140), turn(3, 135)]));
  assert.equal(result.rounds[1].gap, -20);
  assert.equal(result.rounds[1].gapGrowth, 15);
  assert.equal(result.highlights.widening, 2);
});

test('identical actions in different sessions are not differences merely because IDs differ', () => {
  const make = id => {
    const before = observation(1);
    before.adventurers[0].adventurer_id = id;
    const step = tool('allocate_experience', { adventurer_id: id, amount: 20 });
    return replay([{ ...turn(1, 10, [step]), observation_before: before }]);
  };
  assert.deepEqual(compare(make('session-a-hero'), make('session-b-hero')).rounds[0].differences, []);
});

test('resolves stable numeric refs after party membership changes, without positional guessing', () => {
  const before = observation(1, 100, { adventurers: [
    { adventurer_id: 'hero-b', template_id: 'mage', name: '法师', level: 1 },
  ], party_size: 1 });
  const allocation = tool('allocate_experience', { adventurer_id: 2, amount: 30 }, {
    result: { ok: true, _llm_refs: { adventurer: { 'removed-hero': 1, 'hero-b': 2 } } },
  });
  const normalized = normalizeReplay(replay([{ ...turn(1, 0, [allocation]), observation_before: before }]));
  assert.match(normalized.rounds.get(1).selected.actions[0].title, /法师/);
});

test('detects allocation target and amount differences, not query order or model prose', () => {
  const a = turn(1, 10, [tool('get_party'), tool('allocate_experience', { adventurer_id: 1, amount: 20 })]);
  const b = turn(1, 10, [tool('allocate_experience', { adventurer_id: 1, amount: 40 }), tool('get_monsters')]);
  const result = compare(replay([a]), replay([b]));
  assert.ok(result.rounds[0].differences.some(d => d.includes('经验分配')));
  assert.equal(result.highlights.divergence, 1);
});

test('does not label an unresolved target as a proven decision difference', () => {
  const a = { ...turn(1, 0, [tool('allocate_experience', { adventurer_id: 'unknown-a', amount: 20 })]), observation_before: null };
  const b = { ...turn(1, 0, [tool('allocate_experience', { adventurer_id: 'unknown-b', amount: 20 })]), observation_before: null };
  assert.deepEqual(compare(replay([a]), replay([b])).rounds[0].differences, []);
});

test('uses snapshots for action effects and does not attribute a missing earlier snapshot to the next action', () => {
  const after = observation(1, 50);
  const steps = [tool('purchase_upgrade', { upgrade_id: 'unknown' }), tool('craft_equipment', { recipe_id: 'sword' }, { observation_after: after })];
  const selected = normalizeReplay(replay([turn(1, 0, steps)])).rounds.get(1).selected;
  assert.deepEqual(selected.actions[1].changes, []);
});

test('shows known resource and level changes and preserves the original replay', () => {
  const after = observation(1, 100, { experience_pool: 40 });
  after.adventurers[0].level = 2;
  const source = replay([turn(1, 10, [tool('allocate_experience', { adventurer_id: 1, amount: 40 }, { observation_after: after })])]);
  const original = clone(source);
  const action = normalizeReplay(source).rounds.get(1).selected.actions[0];
  assert.ok(action.changes.some(c => c.includes('经验池') && c.includes('80') && c.includes('40')));
  assert.ok(action.changes.some(c => c.includes('等级') && c.includes('2')));
  assert.deepEqual(source, original);
});

test('compares actual hunt assignments and marks losses, but excludes previews', () => {
  const battle = { adventurer_id: 'hero-a', adventurer_name: '先锋', monster_id: 'monster-a', monster_name: '森林狼', won: false, reward: { gold: 0, experience: 0, materials: {} } };
  const a = turn(1, 0);
  a.steps[0] = tool('end_turn', { hunts: [{ adventurer_id: 1, monster_id: 1 }] }, { result: { ok: true, turn_result: { battles: [battle] } } });
  const result = compare(replay([a]), replay([turn(1, 0, [tool('preview_battle')])]));
  assert.equal(result.rounds[0].left.selected.battles[0].won, false);
  assert.ok(result.rounds[0].differences.some(d => d.includes('讨伐')));
  assert.equal(result.highlights.failure, 1);
});

test('missing intermediate snapshots do not turn identical named hunts into a difference', () => {
  const battle = { adventurer_id: 'hero-a', adventurer_name: '先锋', monster_id: 'monster-a', monster_name: '森林狼', won: true };
  const a = turn(1, 10), b = turn(1, 10);
  for (const record of [a, b]) record.steps[0].result.turn_result.battles = [battle];
  a.steps.unshift(tool('allocate_experience', { adventurer_id: 1, amount: 20 }));
  b.steps.unshift(tool('allocate_experience', { adventurer_id: 1, amount: 20 }, { observation_after: observation(1) }));
  assert.deepEqual(compare(replay([a]), replay([b])).rounds[0].differences, []);
});

test('unknown battles in old records are not displayed as zero battles', () => {
  const old = { ...turn(1, null), steps: [tool('end_turn')] };
  assert.equal(normalizeReplay(replay([old])).rounds.get(1).selected.battles, null);
});

test('reads legacy JSON and textual success, while explicit structured failure takes precedence', () => {
  const steps = [
    { type: 'tool_result', name: 'recruit_adventurer', arguments: { candidate_id: 1 }, content: '{"ok":true}' },
    { type: 'tool_result', name: 'craft_equipment', arguments: { recipe_id: 1 }, content: '成功 craft_equipment', result: { ok: false, error: '失败' } },
  ];
  const result = normalizeReplay(replay([turn(1, 10, steps)])).rounds.get(1).selected;
  assert.equal(result.counts.recruit_adventurer, 1);
  assert.equal(result.counts.craft_equipment, 0);
  assert.equal(result.failedTools, 1);
});

test('failed partial turns do not receive completed-turn scores or settlement labels', () => {
  const partial = { ...turn(2, 999), status: 'failed', steps: [tool('get_party')] };
  const result = compare(replay([turn(1, 20), partial]), replay([turn(1, 10), turn(2, 30)]));
  assert.equal(result.rounds[1].left.selected.score, null);
  assert.equal(result.rounds[1].gap, null);
  assert.equal(result.rounds[1].decisionComparable, false);
});

test('warns about incompatible conditions and does not rank widening on those records', () => {
  const a = replay([turn(1, 10), turn(2, 100)]);
  const b = replay([turn(1, 10), turn(2, 20)], { data: { ...a.data, game_seed: 43 } });
  const result = compare(a, b);
  assert.ok(result.conditions.some(c => c.state === 'different' && c.label.includes('游戏种子')));
  assert.equal(result.highlights.widening, null);
});

test('missing experiment metadata is unknown, never asserted to match', () => {
  const a = replay([], { data: {}, config: {}, final_observation: null });
  const result = compare(a, clone(a));
  assert.ok(result.conditions.every(c => c.state === 'unknown'));
  assert.deepEqual(result.rounds, []);
});

test('unknown tool outcomes and missing action records do not imply different decisions', () => {
  const unknown = tool('recruit_adventurer', { candidate_id: 1 }, { result: {}, content: '' });
  const a = replay([turn(1, 10, [unknown])]);
  const b = replay([turn(1, 10, [tool('recruit_adventurer', { candidate_id: 1 })])]);
  assert.deepEqual(compare(a, b).rounds[0].differences, []);
  delete a.turns[0].steps;
  const result = compare(a, b);
  assert.equal(result.rounds[0].decisionComparable, false);
  assert.equal(result.rounds[0].gap, 0);
});

test('only the adjacent next turn can supply an absent end-of-turn snapshot', () => {
  const first = turn(1, 10);
  delete first.steps[0].observation_after;
  const withGap = replay([first, turn(3, 30)]);
  assert.equal(normalizeReplay(withGap).rounds.get(1).selected.after, null);
  const adjacent = replay([first, turn(2, 20)]);
  assert.equal(normalizeReplay(adjacent).rounds.get(1).selected.after.turn, 2);
});

test('rejects unrelated JSON and malformed turn numbers at the input boundary', () => {
  assert.throws(() => normalizeReplay({ hello: 'world' }), /LLM/);
  assert.throws(() => normalizeReplay(replay([{ ...turn(1, 0), turn: 'wrong' }])), /回合/);
});
