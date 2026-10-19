import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

const source = readFileSync(new URL('../../web/replay/app.js', import.meta.url), 'utf8');
const replay = { session_id: 'requested', turns: [{ turn: 3, status: 'completed', rank_score: 7 }] };
const tick = () => new Promise(resolve => setImmediate(resolve));

function setup(listResponse, hash = '#run=requested') {
  const elements = new Map();
  function element(selector) {
    if (!elements.has(selector)) {
      const el = {
        value: '', children: [], style: {}, addEventListener() {},
        appendChild(child) { this.children.push(child); },
        set innerHTML(value) { this.children = []; this.html = value; },
      };
      elements.set(selector, el);
    }
    return elements.get(selector);
  }
  const requests = [];
  const context = vm.createContext({
    URLSearchParams,
    console: { error() {} },
    window: { location: { hash, search: '?turn=3' }, history: { replaceState() {} } },
    document: {
      querySelector: element, querySelectorAll: () => [],
      getElementById: id => element(`#${id}`), createElement: () => ({}), addEventListener() {},
    },
    fetch: async url => {
      requests.push(url);
      if (url === '/api/llm/runs') return listResponse;
      assert.equal(url, '/api/llm/runs/requested/replay');
      return { ok: true, json: async () => replay };
    },
  });
  vm.runInContext(source, context);
  vm.runInContext(`
    hasObservations = () => true;
    needsRankScores = () => false;
    stopPlayback = hideBattleOverlay = updateAll = () => {};
  `, context);
  return { context, requests, selector: element('#runSelector') };
}

test('指定 run 的回放不等待慢列表，列表完成后保留选择', async () => {
  let release;
  const pendingList = new Promise(resolve => { release = resolve; });
  const { context, requests, selector } = setup(pendingList);
  const loading = vm.runInContext('init()', context);
  try {
    await tick();
    assert.ok(requests.includes('/api/llm/runs/requested/replay'));
    assert.equal(vm.runInContext('S.replay.session_id', context), 'requested');
    assert.equal(vm.runInContext('S.currentTurnIdx', context), 0);
  } finally {
    release({ json: async () => ({ runs: [{ run_id: 'requested', status: 'completed' }] }) });
    await loading;
  }
  assert.equal(selector.value, 'requested');
  assert.equal(selector.children.filter(option => option.value === 'requested').length, 1);
});

test('列表缺少目标或读取失败时仍能加载指定回放', async () => {
  for (const list of [
    () => Promise.resolve({ json: async () => ({ runs: [] }) }),
    () => Promise.reject(new Error('list unavailable')),
  ]) {
    const { context, selector } = setup(list());
    await vm.runInContext('init()', context);
    assert.equal(vm.runInContext('S.replay.session_id', context), 'requested');
    assert.equal(selector.value, 'requested');
  }
});

test('未指定 run 时只加载列表，不自动加载其他回放', async () => {
  const { context, requests } = setup(Promise.resolve({ json: async () => ({ runs: [] }) }), '');
  await vm.runInContext('init()', context);
  assert.deepEqual(requests, ['/api/llm/runs']);
  assert.equal(vm.runInContext('S.replay', context), null);
});
