import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

const legacySource = readFileSync(new URL('../../web/static/app.js', import.meta.url), 'utf8');
const replaySource = readFileSync(new URL('../../web/replay/app.js', import.meta.url), 'utf8');
const replayHtml = readFileSync(new URL('../../web/replay/index.html', import.meta.url), 'utf8');

function setup(source) {
  const elements = new Map();
  function element(selector) {
    if (!elements.has(selector)) elements.set(selector, { textContent: '', value: '' });
    return elements.get(selector);
  }
  const context = vm.createContext({
    URLSearchParams,
    window: { location: { search: '', hash: '' }, addEventListener() {} },
    document: {
      getElementById: id => element(`#${id}`),
      querySelector: selector => selector === '#ovScore' ? null : element(selector),
      querySelectorAll: () => [],
      addEventListener() {},
    },
  });
  vm.runInContext(source, context);
  return { context, element };
}

function replayFixture(turnRank, finalRank) {
  return {
    score: { score: 99.87, total_score: 88.76, rank_score: finalRank },
    turns: [{
      turn: 1, status: 'completed', rank_score: turnRank, steps: [],
      observation_before: { turn: 1, max_turns: 35, gold: 100, experience_pool: 20, materials: {}, adventurers: [] },
    }],
  };
}

test('旧操作页评分只读取 rank，保留零分且不回退到旧 Arena 分', () => {
  const { context } = setup(legacySource);
  for (const [score, expected] of [
    [{ score: 99.87, rank_score: 8123.45 }, '段位 8123'],
    [{ score: 99.87, rank_score: 0 }, '段位 0'],
    [{ score: 99.87 }, ''],
    [{ score: 99.87, rank_score: null }, ''],
    [null, ''],
  ]) {
    context.scoreFixture = score;
    assert.equal(vm.runInContext('formatScore(scoreFixture)', context), expected);
  }
});

test('旧操作页运行总结和归档摘要均不展示旧分数', () => {
  const { context } = setup(legacySource);
  context.replayFixture = { ...replayFixture(null, 8123.45), turns: [] };
  const summary = vm.runInContext('renderRunSummary({ score: replayFixture.score, stats: {}, turns: 35 })', context);
  const replay = vm.runInContext('state.llm.replay.data = replayFixture; renderLlmReplay()', context);
  for (const html of [summary, replay]) {
    assert.match(html, /段位 8123/);
    assert.doesNotMatch(html, /99\.87|88\.76|得分/);
  }
});

test('回放页删除旧分数卡片，仍显示正式回合 rank 或最终 rank', () => {
  assert.doesNotMatch(replayHtml, /id="ovScore"/);
  assert.match(replayHtml, /id="ovRank"/);
  const { context, element } = setup(replaySource);
  for (const [turnRank, finalRank, expected] of [
    [1234, 8123.45, 1234],
    [0, 8123.45, 0],
    [null, 8123.45, 8123],
    [null, null, '—'],
  ]) {
    context.replayFixture = replayFixture(turnRank, finalRank);
    vm.runInContext('S.replay = replayFixture; renderOverview()', context);
    assert.equal(element('#ovRank').textContent, expected);
  }
});

test('回放无快照时清空 rank，不留下上一份运行的分数', () => {
  const { context, element } = setup(replaySource);
  context.replayFixture = replayFixture(1234, 8123.45);
  vm.runInContext('S.replay = replayFixture; renderOverview()', context);
  assert.equal(element('#ovRank').textContent, 1234);
  vm.runInContext('S.replay = null; renderOverview()', context);
  assert.equal(element('#ovRank').textContent, '—');
});
