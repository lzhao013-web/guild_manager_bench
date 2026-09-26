import { buildProfile } from './profile-model.mjs';
import { numeric } from './compare-model.mjs';

const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const fmt = value => numeric(value) === null ? '未知' : value.toLocaleString('zh-CN', { maximumFractionDigits: 1 });
const pct = (part, total) => total > 0 ? `${(part / total * 100).toFixed(0)}%` : '—';
const statusName = value => ({ completed: '已完成', failed: '失败', running: '进行中', interrupted: '已中断', incomplete: '未完成' }[value] || value);
const empty = message => `<p class="profile-note">${esc(message)}</p>`;
const ref = turn => ({ turn, attempt: null, step: null });

function evidenceButton(event, label = null) {
  return `<button type="button" class="proof-link" data-evidence-turn="${event.turn}" data-attempt="${event.attempt ?? ''}" data-step="${event.step ?? ''}" aria-label="查看第 ${event.turn} 回合${event.step === null || event.step === undefined ? '' : `操作 ${event.step + 1}`}的依据">${esc(label ?? `T${event.turn}${event.step === null || event.step === undefined ? '' : ` · #${event.step + 1}`}`)}</button>`;
}
function evidenceLinks(events) {
  if (!events.length) return '';
  return `<div class="proof-links">${events.slice(0, 4).map(e => evidenceButton(e)).join('')}${events.length > 4 ? `<details><summary>还有 ${events.length - 4} 条依据</summary><div class="proof-links">${events.slice(4).map(e => evidenceButton(e)).join('')}</div></details>` : ''}</div>`;
}
function meter(label, value, total, detail, events = []) {
  const width = total > 0 ? Math.max(0, Math.min(100, value / total * 100)) : 0;
  return `<div class="profile-meter"><div><strong>${esc(label)}</strong><span>${esc(detail)}</span></div><div class="meter-track"><i style="width:${width}%"></i></div>${evidenceLinks(events)}</div>`;
}
function stat(label, value) { return `<div class="profile-stat"><strong>${esc(value)}</strong><span>${esc(label)}</span></div>`; }
function card(id, number, title, body) {
  return `<section class="profile-card" data-profile-card="${id}"><h3><span>${number}</span>${title}</h3>${body}</section>`;
}

function trainingCard(p) {
  const t = p.training;
  let body = `<div class="profile-stats">${stat('已知分配经验', fmt(t.knownAmount))}${stat('已识别接收者', t.recipients.length)}${stat('成功分配调用', t.calls)}</div>`;
  if (t.recipients.length) {
    body += t.recipients.map(r => meter(r.label, r.amount, t.knownAmount,
      `${fmt(r.amount)} 经验 · ${pct(r.amount, t.knownAmount)}${r.unknownAmounts ? ` · 另有 ${r.unknownAmounts} 次金额未知` : ''}`, r.events)).join('');
  } else body += empty('没有可识别的培养对象。');
  if (t.unresolvedTargets) body += empty(`另有 ${t.unresolvedTargets} 次分配无法识别对象，已知金额 ${fmt(t.unresolvedAmount)}，未合并为某一位成员。`);
  if (t.unknownAmounts) body += empty(`${t.unknownAmounts} 次分配量未知。占比的分母只包含已知金额，不把缺失金额当零。`);
  body += empty('按成员 ID 区分接收者，同名成员不会合并。只描述本局投入，不评价培养是否最优。');
  return card('training', '01', '培养路线', body);
}

function investmentCard(p) {
  const i = p.investment;
  let body = `<div class="profile-stats">${stat('已知金币支出', fmt(i.knownGold))}${stat('金额已知 / 投资调用', `${i.calls - i.unknownCosts} / ${i.calls}`)}</div>`;
  for (const category of i.categories) {
    body += meter(category.label, category.knownGold, i.knownGold,
      `${fmt(category.knownGold)} 金币 · ${category.events.length} 次${category.events.length ? ` · 首次 T${category.events[0].turn}` : ''}${category.unknownCosts ? ` · ${category.unknownCosts} 笔金额未知` : ''}`, category.events);
  }
  if (i.phases.length) {
    body += '<h4>投资发生在哪一段？</h4><div class="phase-grid">';
    body += i.phases.map(phase => `<div><span>T${phase.start}–${phase.end}</span><strong>${phase.observedRounds ? `${fmt(phase.knownGold)} 金币` : '尚无完成记录'}</strong><small>${phase.observedRounds} 个已完成回合${phase.unknownCosts ? ` · ${phase.unknownCosts} 笔金额未知` : ''}</small>${evidenceLinks(phase.events)}</div>`).join('');
    body += '</div>';
  }
  body += empty('分段按声明的总回合数等分，仅用于展示。支出来自操作前后快照，不用最终余额倒推，不将失败尝试并入投资。');
  return card('investment', '02', '投资节奏', body);
}

function equipmentCard(p) {
  const e = p.equipment;
  let body = `<div class="profile-stats">${stat('制作', e.crafted)}${stat('穿戴', e.equipped)}${stat('卸下', e.unequipped)}${stat('可确认替换', e.swaps.length)}</div>`;
  body += evidenceLinks(e.swaps.length ? e.swaps : e.crafts.concat(e.equips));
  if (e.unknownSwaps) body += empty(`${e.unknownSwaps} 次穿戴缺少对象或原槽位信息，无法判断是不是替换。`);
  if (e.snapshot) {
    body += `<h4>第 ${e.snapshot.turn} 回合末的装备库存 ${evidenceButton(ref(e.snapshot.turn), '查看依据')}</h4>`;
    body += `<p>${e.snapshot.total} 件装备中，${e.snapshot.free.length} 件明确未穿戴${e.snapshot.unknown ? `，${e.snapshot.unknown} 件归属未知` : ''}。</p>`;
    if (e.snapshot.free.length) body += `<div class="inventory-tags">${e.snapshot.free.map(item => `<span>${esc(item.name || item.instance_id)}</span>`).join('')}</div>`;
  } else body += empty('缺少最后已完成回合的装备快照。');
  body += empty('首次穿戴不算替换。库存未穿戴不等于无用，可能为后续角色或搭配预留。');
  return card('equipment', '03', '装备使用', body);
}

function habitsCard(p) {
  const h = p.habits;
  let body = `<div class="profile-stats">${stat('工具调用记录', h.totalCalls)}${stat('已确认失败', h.failed)}${stat('结果未知', h.unknown)}${stat('模型回复', h.responseCount)}</div>`;
  body += h.groups.filter(g => g.count).map(g => meter(g.label, g.count, h.totalCalls, `${g.count} 次 · ${pct(g.count, h.totalCalls)}`, g.events)).join('');
  body += `<h4>失败后，下一次调用做了什么？</h4>`;
  const labels = { same: '同一工具、相同参数', arguments: '同一工具、调整参数', tool: '改用其他工具', none: '该次尝试内未见后续调用' };
  if (h.followups.length) {
    body += '<div class="followup-list">' + Object.entries(labels).map(([kind, label]) => {
      const subset = h.followups.filter(f => f.kind === kind);
      return subset.length ? `<div><strong>${label} · ${subset.length} 次</strong>${evidenceLinks(subset.map(f => f.event))}</div>` : '';
    }).join('') + '</div>';
  } else body += empty('未记录到失败工具调用。');
  body += `<h4>成功写入备忘录 ${h.memos.length} 次</h4>${evidenceLinks(h.memos)}`;
  body += empty('本节包含全部已记录尝试。只描述失败后的紧邻调用，不把调整参数等同于问题已解决，也不推断模型是否遵守备忘录。');
  return card('habits', '04', '行动习惯', body);
}

function resourceChart(points, key, label) {
  const known = points.filter(p => p[key] !== null);
  if (!known.length) return empty(`${label}快照未记录。`);
  const max = Math.max(1, ...known.map(p => p[key]));
  return `<div class="resource-chart" role="group" aria-label="逐回合${label}">${points.map(point => `<button type="button" data-evidence-turn="${point.turn}" class="${point[key] === null ? 'missing' : ''}" title="第 ${point.turn} 回合，${label} ${fmt(point[key])}" aria-label="第 ${point.turn} 回合，${label} ${fmt(point[key])}"><i style="height:${point[key] === null ? 0 : Math.max(0, point[key]) / max * 80}px"></i><span>${point.turn}</span></button>`).join('')}</div>`;
}

function resourcesCard(p) {
  const r = p.resources;
  let body = '<div class="resource-headlines">';
  for (const [key, peak, label] of [['gold', r.peakGold, '已记录金币峰值'], ['xp', r.peakXp, '已记录经验池峰值']]) {
    body += `<div><span>${label}</span><strong>${fmt(peak?.[key])}</strong>${peak ? evidenceButton(ref(peak.turn)) : ''}</div>`;
  }
  body += '</div><h4>回合末金币</h4>' + resourceChart(r.points, 'gold', '金币');
  body += '<h4>回合末经验池</h4>' + resourceChart(r.points, 'xp', '经验池');
  if (r.longestXp) body += `<p>最长连续 ${r.longestXp.length} 个已记录回合末仍有经验余额：T${r.longestXp.start}–T${r.longestXp.end}。${evidenceButton(ref(r.longestXp.start))}${r.longestXp.end !== r.longestXp.start ? evidenceButton(ref(r.longestXp.end)) : ''}</p>`;
  body += `<details class="resource-table"><summary>查看逐回合数值与覆盖情况</summary><table><thead><tr><th>回合</th><th>金币</th><th>经验池</th><th>未穿戴装备</th></tr></thead><tbody>${r.points.map(point => `<tr><td>${evidenceButton(ref(point.turn))}</td><td>${fmt(point.gold)}</td><td>${fmt(point.xp)}</td><td>${point.freeEquipment ? `${point.freeEquipment.free.length}${point.freeEquipment.unknown ? ` + ${point.freeEquipment.unknown} 件归属未知` : ''}` : '未知'}</td></tr>`).join('')}</tbody></table></details>`;
  body += empty('只看已完成回合的边界。缺失回合或快照会中断连续记录，余额不等于积压，更不直接表示策略错误。');
  return card('resources', '05', '资源利用', body);
}

export class ProfileView {
  constructor(root) {
    this.root = root;
    this.profile = null;
    root.addEventListener('click', event => {
      const button = event.target.closest('button[data-evidence-turn]');
      if (!button || !this.profile) return;
      const attempt = button.dataset.attempt;
      const step = button.dataset.step;
      this.go(Number(button.dataset.evidenceTurn), attempt ? Number(attempt) : null, step ? Number(step) : null);
    });
    root.addEventListener('change', event => {
      if (event.target.matches('[data-proof-turn]')) this.go(Number(event.target.value));
      if (event.target.matches('[data-proof-attempt]')) this.go(this.selectedTurn, Number(event.target.value));
    });
  }

  show(normalized, { runId = null, turn = null } = {}) {
    this.profile = buildProfile(normalized);
    this.runId = runId;
    const p = this.profile, c = p.coverage;
    this.root.classList.add('profile-view');
    this.root.innerHTML = `<header class="profile-heading"><div><p class="profile-eyebrow">本局经营画像</p><h2>${esc(p.meta.model)}</h2><p>${esc(p.meta.source || p.meta.session)} · ${esc(statusName(p.meta.status))}</p></div><span class="profile-badge">事实归纳 · 每项可追溯</span></header>
      <div class="profile-coverage">已完成 <strong>${c.completed} / ${fmt(c.maxTurns)}</strong> 回合 · 已记录 ${c.attempts} 次尝试 · 其中 ${c.failedAttempts} 次失败。经营投入只统计每回合的主完成记录，行动习惯包含全部尝试。</div>
      ${c.missingSteps || c.unknownEconomicOutcomes ? `<p class="profile-warning">数据不完整：${c.missingSteps} 个已完成回合缺少操作列表，${c.unknownEconomicOutcomes} 次经营操作结果未知。以下描述仅代表可确认的记录。</p>` : ''}
      <section class="profile-insights" aria-label="本局画像摘要">${p.insights.map(i => `<article><span>${esc(i.title)}</span><p>${esc(i.text)}</p>${evidenceLinks(i.events)}</article>`).join('')}</section>
      <p class="profile-note">这是一局的行为记录，不代表该模型在其他种子或配置下的稳定能力。不生成额外能力评分，不调用 AI 猜测动机。</p>
      <div class="profile-grid">${trainingCard(p)}${investmentCard(p)}${equipmentCard(p)}${habitsCard(p)}${resourcesCard(p)}</div>
      <section class="profile-proof" aria-label="画像依据"><header><div><span class="profile-eyebrow">回到记录核查</span><h3 data-proof-heading aria-live="polite">画像依据</h3></div><label>回合 <select data-proof-turn aria-label="画像依据回合">${[...p.replay.rounds.keys()].map(n => `<option value="${n}">第 ${n} 回合</option>`).join('')}</select></label></header><div data-proof-body></div></section>`;
    this.selectedTurn = p.replay.rounds.has(turn) ? turn : p.replay.rounds.keys().next().value ?? null;
    if (this.selectedTurn !== null) this.renderEvidence(this.selectedTurn);
    else {
      this.root.querySelector('[data-proof-turn]').disabled = true;
      this.root.querySelector('[data-proof-body]').innerHTML = empty('没有可核查的回合记录。');
    }
  }

  go(turn, attempt = null, step = null) {
    if (!this.profile.replay.rounds.has(turn)) return;
    this.renderEvidence(turn, attempt, step);
    const target = step === null ? this.root.querySelector('.profile-proof') : this.root.querySelector(`[data-proof-step="${step}"]`);
    target.scrollIntoView({ block: 'start' });
    this.root.dispatchEvent(new CustomEvent('profile-navigate', { detail: { turn } }));
  }

  renderEvidence(turn, attemptIndex = null, stepIndex = null) {
    const round = this.profile.replay.rounds.get(turn);
    const index = attemptIndex ?? round.attempts.indexOf(round.selected);
    const attempt = round.attempts[index];
    this.selectedTurn = turn;
    this.root.querySelector('[data-proof-heading]').textContent = `第 ${turn} 回合 · 第 ${index + 1} 次尝试`;
    this.root.querySelector('[data-proof-turn]').value = String(turn);
    const link = this.runId ? `/replay/?turn=${turn}#run=${encodeURIComponent(this.runId)}` : null;
    this.root.querySelector('[data-proof-body]').innerHTML = `<div class="proof-context"><span>${esc(statusName(attempt.status))}${attempt === round.selected && attempt.completed ? ' · 经营统计主记录' : ' · 不计入经营投入'}</span><label>尝试 <select data-proof-attempt aria-label="画像依据尝试">${round.attempts.map((a, i) => `<option value="${i}" ${i === index ? 'selected' : ''}>${i + 1} · ${esc(statusName(a.status))}</option>`).join('')}</select></label>${link ? `<a href="${esc(link)}" target="_blank" rel="noopener">打开该回合主记录 ↗</a>` : ''}</div>
      ${attempt.failureReason ? `<p class="profile-warning">${esc(attempt.failureReason)}</p>` : ''}
      <div class="proof-balances">回合开始金币 ${fmt(attempt.before?.gold)} → ${attempt.completed ? '结束' : '最后已知'} ${fmt(attempt.after?.gold)} · 经验池 ${fmt(attempt.before?.experience_pool)} → ${fmt(attempt.after?.experience_pool)}</div>
      ${attempt.actions.length ? attempt.actions.map(a => `<details class="proof-action ${a.index === stepIndex ? 'selected' : ''}" data-proof-step="${a.index}" ${a.index === stepIndex ? 'open' : ''}><summary><span>#${a.index + 1} ${esc(a.title)}</span><small>${a.success === true ? '成功' : a.success === false ? '失败' : '结果未知'}</small></summary>${a.changes.map(c => `<p>${esc(c)}</p>`).join('')}<pre>${esc(JSON.stringify(a.arguments, null, 2))}</pre><pre>${esc(a.content || '未记录工具返回')}</pre></details>`).join('') : empty('本次尝试没有工具返回记录。')}`;
  }

  clear() { this.profile = null; this.root.replaceChildren(); }
}
