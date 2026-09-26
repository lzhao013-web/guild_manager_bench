import { ACTION_LABELS, normalizeReplay, compareReplays, numeric } from './compare-model.mjs';

const $ = id => document.getElementById(id);
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const fmt = value => numeric(value) === null ? '未知' : value.toLocaleString('zh-CN', { maximumFractionDigits: 1 });
const signed = value => numeric(value) === null ? '未知' : `${value > 0 ? '+' : ''}${fmt(value)}`;
const statuses = { completed: '已完成', failed: '失败', interrupted: '已中断', running: '进行中', incomplete: '未完成' };
const slots = Object.fromEntries(['A', 'B'].map(side => [side, { model: null, runId: null, ticket: 0, busy: false }]));
const state = { comparison: null, turn: null, listTicket: 0 };
const initialParams = new URLSearchParams(window.location.search);

function errorMessage(error) { return error instanceof Error ? error.message : String(error); }
async function getJSON(url) {
  const response = await fetch(url);
  if (!response.ok) throw new Error(`读取失败 (HTTP ${response.status})`);
  return response.json();
}

async function loadRunList() {
  const ticket = ++state.listTicket;
  $('listStatus').textContent = '正在读取存档列表…';
  try {
    const data = await getJSON('/api/llm/runs');
    if (!Array.isArray(data.runs)) throw new Error('存档列表格式不正确');
    if (ticket !== state.listTicket) return;
    for (const side of ['A', 'B']) {
      const select = $(`run${side}`), selected = select.value;
      select.replaceChildren(new Option('选择服务端存档…', ''));
      for (const run of data.runs) {
        const label = `${run.model || run.run_id} · ${run.preset || '?'} · ${statuses[run.status] || run.status} · ${run.created_at || run.run_id}`;
        select.add(new Option(label, run.run_id));
      }
      select.value = selected || slots[side].runId || '';
    }
    $('listStatus').textContent = data.runs.length ? `${data.runs.length} 份服务端存档 · 也可直接打开本地文件` : '暂无服务端存档，可在上方选择两个本地 replay.json';
  } catch (error) {
    if (ticket !== state.listTicket) return;
    $('listStatus').textContent = `存档列表不可用：${errorMessage(error)}。仍可打开本地文件。`;
  }
}

async function loadSlot(side, loader, label, runId = null) {
  const slot = slots[side], ticket = ++slot.ticket;
  slot.busy = true;
  $(`load${side}`).disabled = true;
  $(`file${side}`).disabled = true;
  $(`source${side}`).textContent = '正在读取并整理回合…';
  $('loadError').hidden = true;
  try {
    const replay = await loader();
    const model = normalizeReplay(replay, label);
    if (ticket !== slot.ticket) return;
    slot.model = model;
    slot.runId = runId;
    const rounds = model.rounds.size;
    $(`source${side}`).textContent = `${model.meta.model} · ${rounds} 个回合编号 · ${statuses[model.meta.status] || model.meta.status} · ${label}`;
    $(`source${side}`).classList.add('loaded');
    $(`run${side}`).value = runId || '';
    updateComparison();
  } catch (error) {
    if (ticket !== slot.ticket) return;
    $(`source${side}`).textContent = slot.model ? `加载失败，保留原跑局：${slot.model.meta.model}` : '尚未成功加载跑局';
    $('loadError').textContent = `${side} 侧：${errorMessage(error)}`;
    $('loadError').hidden = false;
  } finally {
    if (ticket === slot.ticket) {
      slot.busy = false;
      $(`load${side}`).disabled = false;
      $(`file${side}`).disabled = false;
    }
  }
}

function loadRun(side, runId) {
  if (!runId) return;
  return loadSlot(side, () => getJSON(`/api/llm/runs/${encodeURIComponent(runId)}/replay`), runId, runId);
}

function updateURL() {
  const params = new URLSearchParams();
  if (slots.A.runId) params.set('left', slots.A.runId);
  if (slots.B.runId) params.set('right', slots.B.runId);
  if (state.turn !== null) params.set('turn', state.turn);
  const query = params.toString();
  window.history.replaceState(null, '', `${window.location.pathname}${query ? `?${query}` : ''}`);
}

function updateComparison() {
  if (!slots.A.model || !slots.B.model) {
    $('emptyState').querySelector('h2').textContent = '再选择另一局，即可开始对照';
    updateURL();
    return;
  }
  const comparison = compareReplays(slots.A.model, slots.B.model);
  state.comparison = comparison;
  $('sourceSummary').textContent = `A · ${slots.A.model.meta.model}  /  B · ${slots.B.model.meta.model}`;
  $('sourcePicker').open = comparison.rounds.length === 0;
  document.body.classList.toggle('compare-ready', comparison.rounds.length > 0);
  const requested = state.turn ?? Number(initialParams.get('turn'));
  state.turn = comparison.rounds.some(r => r.number === requested) ? requested : comparison.rounds[0]?.number ?? null;
  $('emptyState').hidden = comparison.rounds.length > 0;
  $('workspace').hidden = false;
  if (!comparison.rounds.length) {
    $('emptyState').querySelector('h2').textContent = '两份存档都没有可对照的回合记录';
    $('emptyState').querySelector('p').textContent = '可以重新选择存档。仅有最终统计的 JSON 不能用于行动级复盘。';
  }
  renderConditions(comparison);
  renderHighlights(comparison);
  $('turnSelect').replaceChildren(...comparison.rounds.map(r => new Option(`第 ${r.number} 回合`, r.number)));
  renderRound();
}

function conditionValue(value) {
  if (value === null || value === undefined) return '未记录';
  const text = typeof value === 'object' ? JSON.stringify(value) : String(value);
  return text.length > 180 ? `${text.slice(0, 180)}…` : text;
}

function renderConditions(comparison) {
  const unknown = comparison.conditions.some(c => c.state === 'unknown');
  const title = comparison.incompatible ? '存在不同的实验条件' : unknown ? '部分实验条件未记录' : '已记录的实验条件一致';
  const warning = comparison.incompatible
    ? '本页仍可查看行动差异，但不能据此认定模型优劣。已停用“分差扩大最多”自动推荐。'
    : unknown ? '可核对的字段已列出。分数差仅供参考，不把缺失配置视为相同。' : '相同实验条件不代表相同模型输出。下面只比较已记录的行为和结果。';
  $('conditions').innerHTML = `<details class="condition-box">
    <summary><strong>${title}</strong>${comparison.conditions.map(c => `<span class="condition-chip ${c.state}">${esc(c.label)} · ${{ same: '相同', different: '不同', unknown: '未知' }[c.state]}</span>`).join('')}</summary>
    <p class="condition-warning">${warning}</p>
    <table class="comparison-table condition-detail"><thead><tr><th>条件</th><th>A</th><th>B</th></tr></thead><tbody>${comparison.conditions.map(c => `<tr><th>${esc(c.label)}</th><td>${esc(conditionValue(c.left))}</td><td>${esc(conditionValue(c.right))}</td></tr>`).join('')}</tbody></table>
    </details>${comparison.incompatible || unknown ? `<p class="condition-warning">${warning}</p>` : ''}`;
}

function renderHighlights(comparison) {
  const definitions = [
    ['widening', '分差扩大最多', comparison.incompatible ? '实验条件不同，不作推荐' : '按相邻回合的绝对分差增量'],
    ['divergence', '首个行动分歧', '成功经营操作或讨伐安排不同'],
    ['failure', '首个失败节点', '工具失败、失败尝试或讨伐战败'],
  ];
  $('highlights').innerHTML = definitions.map(([key, title, note]) => {
    const turn = comparison.highlights[key];
    return `<button class="highlight" type="button" data-turn="${turn ?? ''}" ${turn === null ? 'disabled' : ''} aria-label="${title}${turn === null ? '，无可用回合' : `，第 ${turn} 回合`}"><span class="highlight-title">${title}</span><span class="highlight-turn">${turn === null ? '—' : `第 ${turn} 回合`}</span><span class="highlight-note">${note}</span></button>`;
  }).join('');
}

function goToTurn(number) {
  if (!state.comparison?.rounds.some(r => r.number === number)) return;
  state.turn = number;
  renderRound();
  $('roundContent').scrollIntoView({ block: 'start' });
}

function shiftRound(offset) {
  const rounds = state.comparison?.rounds;
  if (!rounds) return;
  const index = rounds.findIndex(r => r.number === state.turn);
  if (rounds[index + offset]) goToTurn(rounds[index + offset].number);
}

function renderRound() {
  const comparison = state.comparison;
  if (!comparison) return;
  const index = comparison.rounds.findIndex(r => r.number === state.turn);
  const round = comparison.rounds[index];
  $('prevRound').disabled = index <= 0;
  $('nextRound').disabled = index < 0 || index === comparison.rounds.length - 1;
  $('turnTitle').textContent = round ? `第 ${round.number} 回合` : '暂无回合';
  $('turnCount').textContent = round ? `${index + 1} / ${comparison.rounds.length} 个回合编号` : '';
  $('turnSelect').value = String(state.turn);
  $('turnSelect').disabled = !round;
  $('roundList').innerHTML = comparison.rounds.map(r => `<button type="button" class="round-link" data-turn="${r.number}" aria-current="${r.number === state.turn}" aria-label="查看第 ${r.number} 回合">
    <span class="round-flags">${r.differences.length ? '<span class="round-flag">异</span>' : ''}${r.hasFailure ? '<span class="round-flag failure">!</span>' : ''}</span><strong>第 ${r.number} 回合</strong><small>${r.gap !== null ? `A − B ${esc(signed(r.gap))}` : r.left && r.right ? '评分未齐全' : '仅一侧有记录'}</small></button>`).join('');
  $('roundContent').innerHTML = round ? `${renderSummary(round)}<div class="side-panels">${renderSide('A', round.left, round.number)}${renderSide('B', round.right, round.number)}</div>` : '';
  updateURL();
}

function transition(attempt, key) {
  if (!attempt) return '无此回合';
  const before = numeric(attempt.before?.[key]), after = numeric(attempt.after?.[key]);
  return `${fmt(before)} → ${fmt(after)}${before !== null && after !== null ? `<span class="metric-change">${signed(after - before)}</span>` : ''}`;
}

function scoreCell(round) {
  if (!round) return '无此回合';
  return `${fmt(round.selected.score)}${round.scoreDelta !== null ? `<span class="metric-change">${signed(round.scoreDelta)}</span>` : ''}`;
}

function countCell(attempt, name) {
  if (!attempt) return '无此回合';
  if (!attempt.hasSteps) return '未记录';
  const unknown = attempt.actions.filter(s => s.name === name && s.success === null).length;
  return `${attempt.counts[name]} 次${unknown ? `<span class="metric-change">另有 ${unknown} 次结果未知</span>` : ''}`;
}

function renderSummary(round) {
  const a = round.left?.selected, b = round.right?.selected;
  const lead = round.gap === null ? '分数差未知' : round.gap === 0 ? '当前分数相同' : `${round.gap > 0 ? 'A' : 'B'} 分数高 ${fmt(Math.abs(round.gap))}`;
  const gapNote = round.gapGrowth === null ? '' : ` · 绝对分差较上回合 ${signed(round.gapGrowth)}`;
  const rows = [
    ['回合状态', a ? esc(statuses[a.status] || a.status) : '无此回合', b ? esc(statuses[b.status] || b.status) : '无此回合'],
    ['Rank Score', scoreCell(round.left), scoreCell(round.right)],
    ['金币', transition(a, 'gold'), transition(b, 'gold')],
    ['经验池', transition(a, 'experience_pool'), transition(b, 'experience_pool')],
    ['队伍人数', transition(a, 'party_size'), transition(b, 'party_size')],
  ];
  for (const [name, label] of Object.entries(ACTION_LABELS)) {
    const hasRecord = [a, b].some(attempt => attempt?.actions.some(action => action.name === name));
    if (hasRecord) rows.push([label, countCell(a, name), countCell(b, name)]);
  }
  const differenceNote = !round.decisionComparable ? '一侧缺少已完成回合或操作记录，不判断完整行动差异。'
    : round.differences.length ? round.differences.map(d => `<span>${esc(d)}</span>`).join('') : '未识别到已确认的行动差异。不代表操作顺序或所有细节相同。';
  return `<section class="round-summary" aria-label="本回合对照摘要"><div class="summary-heading"><h2>第 ${round.number} 回合 · 行动对照</h2><span class="score-gap ${round.gap > 0 ? 'color-a' : round.gap < 0 ? 'color-b' : ''}">${esc(lead)}${esc(gapNote)}</span></div>
    <div class="difference-list">${differenceNote}</div>
    <table class="comparison-table"><thead><tr><th>对照项</th><th>A · ${esc(slots.A.model.meta.model)}</th><th>B · ${esc(slots.B.model.meta.model)}</th></tr></thead><tbody>${rows.map(([label, left, right]) => `<tr><th>${esc(label)}</th><td>${left}</td><td>${right}</td></tr>`).join('')}</tbody></table>
    <p class="summary-note">资源显示回合开始 → 结束。未完成尝试仅显示最后已确认的局部状态。操作次数只计算主记录中已确认成功的操作，历史失败尝试另列。</p></section>`;
}

function renderActions(actions) {
  if (!actions.length) return '<p class="small-note">没有可展示的经营操作或工具失败记录。</p>';
  return `<ol class="action-list">${actions.map(action => `<li class="action-item ${action.success === false ? 'failed' : action.success === null ? 'unknown' : ''}">
    <div class="action-title">${action.success === false ? '失败 · ' : action.success === null ? '结果未知 · ' : ''}${esc(action.title)}</div>
    ${action.error ? `<p class="action-change">${esc(action.error)}</p>` : ''}
    ${action.changes.slice(0, 4).map(c => `<p class="action-change">${esc(c)}</p>`).join('')}
    <details class="evidence"><summary>操作 #${action.index + 1} · 参数与原始返回${action.changes.length > 4 ? ` · 另有 ${action.changes.length - 4} 项变化` : ''}</summary>
    ${action.changes.slice(4).map(c => `<p class="action-change">${esc(c)}</p>`).join('')}<pre>${esc(JSON.stringify(action.arguments, null, 2))}</pre><pre>${esc(action.content || '未记录返回内容')}</pre></details>
    </li>`).join('')}</ol>`;
}

function renderBattles(attempt) {
  if (attempt.battles === null) return '<p class="small-note">没有结构化战斗结果，不能判断为零场讨伐。可在下方展开原始记录。</p>';
  if (!attempt.battles.length) return '<p class="small-note">本回合未安排讨伐。</p>';
  return attempt.battles.map(battle => `<div class="battle-row"><div class="battle-title"><span>${esc(battle.hero)} → ${esc(battle.monster)}</span><span class="${battle.won === true ? 'outcome-win' : battle.won === false ? 'outcome-loss' : ''}">${battle.won === true ? '胜利' : battle.won === false ? '战败' : '结果未知'}</span></div>
    <small>金币 ${fmt(battle.gold)} · 经验 ${fmt(battle.experience)}${battle.materials ? Object.entries(battle.materials).map(([name, count]) => ` · ${esc(name)} ${esc(count)}`).join('') : ''}</small></div>`).join('');
}

function renderParty(attempt) {
  const obs = attempt.after;
  if (!Array.isArray(obs?.adventurers)) return '<p class="small-note">缺少回合后的队伍快照。</p>';
  if (!obs.adventurers.length) return '<p class="small-note">队伍为空。</p>';
  const gear = new Map((Array.isArray(obs.equipment_inventory) ? obs.equipment_inventory : []).map(item => [item.instance_id, item.name]));
  return obs.adventurers.map(member => `<div class="party-row"><span>${esc(member.name)}<small>${Array.isArray(member.equipment) ? member.equipment.map(item => esc(gear.get(item.instance_id) || item.instance_id)).join('、') || '无装备' : '装备未记录'}</small></span><span>Lv.${fmt(member.level)}<small>生命 ${fmt(member.resources?.current_hp)} / ${fmt(member.effective_stats?.hp)}</small></span></div>`).join('');
}

function renderSide(side, round, turnNumber) {
  const slot = slots[side];
  const header = `<div class="side-heading"><span class="side-mark">${side}</span><h2>${esc(slot.model.meta.model)}</h2></div>`;
  if (!round) return `<article class="side-panel side-${side.toLowerCase()}">${header}<div class="blank-side">这局没有第 ${turnNumber} 回合的记录。<br>不沿用其他回合的状态或分数。</div></article>`;
  const attempt = round.selected;
  const mainActions = attempt.actions.filter(a => a.economic || a.success === false);
  const otherActions = attempt.actions.filter(a => !a.economic && a.success !== false);
  const otherAttempts = round.attempts.filter(a => a !== attempt);
  const selectedIndex = round.attempts.indexOf(attempt) + 1;
  const playerLink = slot.runId ? `/replay/?turn=${turnNumber}#run=${encodeURIComponent(slot.runId)}` : null;
  return `<article class="side-panel side-${side.toLowerCase()}" aria-label="${side} 侧回合详情">${header}
    <div class="side-subheading"><span class="status-tag ${attempt.completed ? 'completed' : attempt.status === 'failed' ? 'failed' : 'incomplete'}">${esc(statuses[attempt.status] || attempt.status)}</span><span>主记录：第 ${selectedIndex} / ${round.attempts.length} 次尝试</span>${playerLink ? `<a href="${esc(playerLink)}" target="_blank" rel="noopener">打开单局播放器 ↗</a>` : ''}</div>
    ${attempt.failureReason ? `<div class="side-section"><p class="compare-alert">${esc(attempt.failureReason)}</p></div>` : ''}
    <section class="side-section"><h3>经营操作 · ${mainActions.length} 条记录</h3>${attempt.hasSteps ? renderActions(mainActions) : '<p class="small-note">缺少操作记录。</p>'}</section>
    <section class="side-section"><h3>实际讨伐 · ${attempt.battles === null ? '记录不完整' : `${attempt.battles.length} 场`}</h3>${renderBattles(attempt)}</section>
    <details class="side-details"><summary>${attempt.completed ? '回合后阵容' : '最后已确认的阵容'}</summary>${renderParty(attempt)}</details>
    <details class="side-details"><summary>查询、预览、备忘与结算原文 · ${otherActions.length} 条</summary>${renderActions(otherActions)}</details>
    <details class="side-details"><summary>模型实际输出与重试提示 · ${attempt.notes.length} 条</summary>${attempt.notes.map(note => `<div><h3>${note.type === 'model' ? '模型正文' : '重试提示'}</h3><pre class="trace-text">${esc(note.text || '无正文')}</pre>${note.reasoning ? `<details class="evidence"><summary>API 推理文本 / 摘要</summary><pre>${esc(note.reasoning)}</pre></details>` : ''}</div>`).join('') || '<p class="small-note">未记录模型输出。</p>'}</details>
    ${otherAttempts.length ? `<details class="attempt-history"><summary>其他尝试 · ${otherAttempts.length} 次，不并入上方经营统计</summary>${otherAttempts.map(a => `<div class="attempt-record"><strong>第 ${round.attempts.indexOf(a) + 1} 次尝试 · ${esc(statuses[a.status] || a.status)}</strong>${a.failureReason ? `<p>${esc(a.failureReason)}</p>` : ''}${renderActions(a.actions)}</div>`).join('')}</details>` : ''}
    </article>`;
}

for (const side of ['A', 'B']) {
  $(`load${side}`).addEventListener('click', () => loadRun(side, $(`run${side}`).value));
  $(`file${side}`).addEventListener('change', event => {
    const file = event.target.files[0];
    if (file) loadSlot(side, async () => JSON.parse(await file.text()), file.name);
    event.target.value = '';
  });
}
$('refreshRuns').addEventListener('click', loadRunList);
$('prevRound').addEventListener('click', () => shiftRound(-1));
$('nextRound').addEventListener('click', () => shiftRound(1));
$('turnSelect').addEventListener('change', event => goToTurn(Number(event.target.value)));
for (const container of ['highlights', 'roundList']) {
  $(container).addEventListener('click', event => {
    const button = event.target.closest('button[data-turn]');
    if (button && !button.disabled) goToTurn(Number(button.dataset.turn));
  });
}
document.addEventListener('keydown', event => {
  if (!state.comparison || event.defaultPrevented || event.altKey || event.ctrlKey || event.metaKey) return;
  if (event.target.closest('input, select, textarea, button, a, summary, [contenteditable]')) return;
  if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') {
    event.preventDefault();
    shiftRound(event.key === 'ArrowLeft' ? -1 : 1);
  }
});

await loadRunList();
await Promise.all(['A', 'B'].map(side => {
  const runId = initialParams.get(side === 'A' ? 'left' : 'right');
  return runId && slots[side].ticket === 0 ? loadRun(side, runId) : undefined;
}));
