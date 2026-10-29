import test from 'node:test';
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';
import { ProfileView } from '../../web/replay/profile-view.mjs';
import { normalizeReplay } from '../../web/replay/compare-model.mjs';
import { tool, turn, replay } from './compare-fixtures.mjs';

function render(source) {
  const document = new JSDOM('<main></main>').window.document;
  const root = document.querySelector('main');
  new ProfileView(root).show(normalizeReplay(source));
  return root;
}

test('profile keeps records and evidence without interpretation disclaimers', () => {
  const root = render(replay([turn(1, 100, [tool('allocate_experience', { adventurer_id: 1, amount: 30 })])]));
  assert.equal(root.querySelectorAll('.profile-card').length, 5);
  assert.ok(root.querySelector('button[data-evidence-turn="1"]'));
  assert.match(root.querySelector('[data-profile-card="training"]').textContent, /30 经验/);
  assert.doesNotMatch(root.textContent, /不代表|不评价|不推断|不把|不直接表示|不调用 AI|只描述本局|仅用于展示/);
});

test('profile still reports missing records, unknown amounts, and failed attempts', () => {
  const missing = turn(1, 100);
  delete missing.steps;
  const failed = { ...turn(2, null), status: 'failed', failure_reason: '连接断开' };
  const complete = turn(2, 200, [tool('allocate_experience', { adventurer_id: 1 })]);
  const root = render(replay([missing, failed, complete]));
  assert.match(root.querySelector('.profile-warning').textContent, /1 个已完成回合缺少操作列表/);
  assert.match(root.querySelector('[data-profile-card="training"]').textContent, /1 次经验分配未记录数量/);
  assert.match(root.querySelector('.profile-coverage').textContent, /1 次失败/);
});
