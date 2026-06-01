/**
 * Guild Manager Bench · Replay Viewer v2
 *
 * Visual playback of LLM benchmark runs.
 * - Auto-play skips read-only tool calls
 * - Each mutating step refreshes game state from observation_after
 * - end_turn battles display full-screen battle animation
 */

// ============================================================================
// Constants
// ============================================================================
const READ_TOOLS = new Set([
  'get_party', 'get_monsters', 'get_crafting', 'get_inventory',
  'get_upgrades', 'get_recruitment', 'get_events', 'preview_battle',
]);

const WRITE_TOOLS = new Set([
  'craft_equipment', 'purchase_upgrade', 'allocate_experience',
  'recruit_adventurer', 'dismiss_adventurer', 'equip_item', 'unequip_item',
  'end_turn',
]);

const TOOL_FOCUS = {
  recruit_adventurer:    { tab: 'adventurers', entityType: null,           idArg: null },          // special: find new adventurer from obs diff
  dismiss_adventurer:    { tab: 'adventurers', entityType: null,           idArg: null },          // card removed; flash remaining party
  allocate_experience:   { tab: 'adventurers', entityType: 'adventurer', idArg: 'adventurer_id' },
  equip_item:            { tab: 'adventurers', entityType: 'adventurer', idArg: 'adventurer_id' },
  unequip_item:          { tab: 'adventurers', entityType: 'adventurer', idArg: 'adventurer_id' },
  craft_equipment:       { tab: 'inventory',   entityType: null,           idArg: null },          // special: find new equipment from obs diff
  purchase_upgrade:      { tab: 'crafting',    entityType: 'upgrade',     idArg: 'upgrade_id' },
  end_turn:              { tab: 'monsters',    entityType: null,           idArg: null },
};

// ============================================================================
// State
// ============================================================================
const S = {
  replay: null,
  currentTurnIdx: 0,
  currentStepIdx: -1,
  playing: false,
  playSpeed: 1,
  playTimer: null,
  battleVisible: false,
  rankChartInstance: null,
};

// ============================================================================
// DOM
// ============================================================================
const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => document.querySelectorAll(sel);
const DOM = {
  runSelector: $('#runSelector'), loadButton: $('#loadButton'), runMeta: $('#runMeta'),
  timelineScroll: $('#timelineScroll'), turnBadge: $('#turnBadge'), stepBadge: $('#stepBadge'),
  adventurerCards: $('#adventurerCards'), monsterCards: $('#monsterCards'),
  inventoryCards: $('#inventoryCards'), recipeList: $('#recipeList'), upgradeList: $('#upgradeList'),
  llmScroll: $('#llmScroll'), statusText: $('#statusText'),
  btnFirst: $('#btnFirst'), btnPrevTurn: $('#btnPrevTurn'), btnPlay: $('#btnPlay'),
  btnNextTurn: $('#btnNextTurn'), btnLast: $('#btnLast'), speedSelect: $('#speedSelect'),
  stepBarFill: $('#stepBarFill'),
  ovTurn: $('#ovTurn'), ovMaxTurn: $('#ovMaxTurn'), ovGold: $('#ovGold'), ovExp: $('#ovExp'),
  ovMaterials: $('#ovMaterials'), ovParty: $('#ovParty'), ovScore: $('#ovScore'), ovRank: $('#ovRank'),
  ovStats: $('#ovStats'),
  actionToast: $('#actionToast'), battleOverlay: $('#battleOverlay'), battleStage: $('#battleStage'),
  chartOverlay: $('#chartOverlay'), rankChartCanvas: $('#rankChart'), btnRankChart: $('#btnRankChart'), chartClose: $('#chartClose'),
};

// ============================================================================
// Init
// ============================================================================
async function init() {
  bindEvents();
  const runIds = await loadRunList();
  const hash = window.location.hash;
  if (hash.startsWith('#run=')) {
    const runId = decodeURIComponent(hash.slice(5));
    if (runIds.has(runId)) {
      DOM.runSelector.value = runId;
      await loadReplay(runId);
    } else {
      window.history.replaceState(null, '', window.location.pathname + window.location.search);
      setStatus(`找不到跑局: ${runId}`, true);
    }
  }
}

function bindEvents() {
  DOM.loadButton.addEventListener('click', () => { const rid = DOM.runSelector.value; if (rid) loadReplay(rid); });
  DOM.btnFirst.addEventListener('click', () => { if (S.replay) goToTurn(firstCompleted()); });
  DOM.btnPrevTurn.addEventListener('click', prevTurn);
  DOM.btnPlay.addEventListener('click', togglePlay);
  DOM.btnNextTurn.addEventListener('click', nextTurn);
  DOM.btnLast.addEventListener('click', () => { if (S.replay) goToTurn(lastCompleted()); });
  DOM.speedSelect.addEventListener('change', () => { S.playSpeed = parseFloat(DOM.speedSelect.value); });
  DOM.battleOverlay.addEventListener('click', hideBattleOverlay);
  DOM.btnRankChart.addEventListener('click', showRankChart);
  DOM.chartClose.addEventListener('click', hideRankChart);
  DOM.chartOverlay.addEventListener('click', e => { if (e.target === DOM.chartOverlay || e.target.classList.contains('chart-backdrop')) hideRankChart(); });
  $$('#stateTabs .tab').forEach(t => t.addEventListener('click', () => switchTab(t.dataset.tab)));
  document.addEventListener('keydown', e => {
    if (e.target.tagName === 'INPUT' || e.target.tagName === 'SELECT') return;
    switch (e.key) {
      case ' ': e.preventDefault(); togglePlay(); break;
      case 'ArrowLeft': e.preventDefault(); prevTurn(); break;
      case 'ArrowRight': e.preventDefault(); nextTurn(); break;
      case 'ArrowUp': e.preventDefault(); prevStep(); break;
      case 'ArrowDown': e.preventDefault(); nextStep(); break;
      case 'Home': e.preventDefault(); if (S.replay) goToTurn(firstCompleted()); break;
      case 'End': e.preventDefault(); if (S.replay) goToTurn(lastCompleted()); break;
      case 'Escape': hideBattleOverlay(); hideRankChart(); break;
    }
  });
}

// ============================================================================
// Data
// ============================================================================
async function loadRunList() {
  const runIds = new Set();
  try {
    const r = await fetch('/api/llm/runs'); const d = await r.json();
    DOM.runSelector.innerHTML = '<option value="">— 选择 LLM 跑局 —</option>';
    (d.runs||[]).forEach(run => {
      const o = document.createElement('option'); o.value = run.run_id;
      runIds.add(run.run_id);
      const rank = run.rank_score != null ? ` · R${Math.round(run.rank_score)}` : '';
      o.textContent = `${(run.created_at||'').slice(0,15)} · ${run.preset||'?'} · T${run.turns} · ${run.status}${rank}${run.has_observations?' 📷':''}`;
      DOM.runSelector.appendChild(o);
    });
  } catch(e) { console.error(e); }
  return runIds;
}

async function loadReplay(runId) {
  setStatus('加载中...');
  try {
    let resp = await fetch(`/api/llm/runs/${encodeURIComponent(runId)}/replay`);
    if (!resp.ok) throw new Error(await responseErrorMessage(resp, `加载 replay 失败 (HTTP ${resp.status})`));
    let replay = await resp.json();
    if (!hasObservations(replay)) {
      setStatus('缺少回合快照，正在重建...');
      resp = await fetch(`/api/llm/runs/${encodeURIComponent(runId)}/rebuild`, { method: 'POST' });
      if (!resp.ok) throw new Error(await responseErrorMessage(resp, '重建失败'));
      replay = await resp.json();
    } else if (needsRankScores(replay)) {
      setStatus('缺少段位分，正在补全...');
      resp = await fetch(`/api/llm/runs/${encodeURIComponent(runId)}/rescore`, { method: 'POST' });
      if (!resp.ok) throw new Error(await responseErrorMessage(resp, '补分失败'));
      replay = await resp.json();
    }
    S.replay = replay; S.currentTurnIdx = firstCompleted(); S.currentStepIdx = -1;
    stopPlayback(); hideBattleOverlay();
    // Enable rank chart button if any turn has rank_score
    const hasRankData = (replay.turns||[]).some(t => t.rank_score != null);
    DOM.btnRankChart.disabled = !hasRankData;
    window.location.hash = `#run=${runId}`;
    updateAll(); setStatus(`已加载: ${runId}`);
    DOM.runMeta.innerHTML = `<span>${replay.session_id||runId}</span>`;
  } catch(e) { setStatus(`错误: ${e.message}`, true); console.error(e); }
}

async function responseErrorMessage(response, fallback) {
  try {
    const contentType = response.headers.get('content-type') || '';
    if (contentType.includes('application/json')) {
      const data = await response.json();
      return data?.detail || data?.error || fallback;
    }
    const text = (await response.text()).trim();
    return text || fallback;
  } catch {
    return fallback;
  }
}

function hasObservations(replay) {
  const t = replay.turns; return Array.isArray(t) && t.length && t[0].observation_before != null;
}

function needsRankScores(replay) {
  if (!replay || typeof replay !== 'object') return false;
  const turns = Array.isArray(replay.turns) ? replay.turns : [];
  const hasFinalObservation = replay.final_observation && typeof replay.final_observation === 'object';
  const score = replay.score && typeof replay.score === 'object' ? replay.score : null;
  if (hasFinalObservation && (!score || score.rank_score == null)) return true;
  if (hasFinalObservation && !hasRankScoreContributions(score)) return true;
  return turns.some((turn, index) => {
    if (!turn || typeof turn !== 'object' || turn.status !== 'completed' || turn.rank_score != null) return false;
    const nextTurn = turns[index + 1];
    return Boolean(
      (nextTurn && nextTurn.observation_before) ||
      (index === turns.length - 1 && hasFinalObservation)
    );
  });
}

function hasRankScoreContributions(score) {
  if (!score || typeof score !== 'object') return false;
  return contributionItems(score).length > 0;
}

// ============================================================================
// Effective Observation (key: uses step-level observation_after)
// ============================================================================
function getEffectiveObservation() {
  if (!S.replay) return null;
  const turn = currentTurn(); if (!turn) return null;

  // If we're at a step that has observation_after, use that as the current state
  if (S.currentStepIdx >= 0) {
    const steps = turn.steps || [];
    // Find the most recent write step with observation_after up to currentStepIdx
    for (let i = S.currentStepIdx; i >= 0; i--) {
      const step = steps[i];
      if (step && step.observation_after) {
        return step.observation_after;
      }
    }
  }

  // Fall back to turn-level observation_before
  return turn.observation_before || null;
}

function currentTurn() {
  if (!S.replay) return null;
  const t = S.replay.turns; if (S.currentTurnIdx<0||S.currentTurnIdx>=t.length) return null;
  return t[S.currentTurnIdx];
}

function currentStep() {
  if (S.currentStepIdx < 0) return null;
  const turn = currentTurn(); if (!turn) return null;
  const steps = turn.steps || []; if (S.currentStepIdx >= steps.length) return null;
  return steps[S.currentStepIdx];
}

// ============================================================================
// Navigation
// ============================================================================
function goToTurn(idx) {
  if (!S.replay) return; const t = S.replay.turns;
  if (idx<0) idx=0; if (idx>=t.length) idx=t.length-1;
  S.currentTurnIdx = idx; S.currentStepIdx = -1; updateAll();
}
function isCompleteTurn(t) { return t.status === 'completed' || t.status === 'failed'; }
function findPrevCompleted(fromIdx) {
  const turns = S.replay.turns;
  for (let i = fromIdx - 1; i >= 0; i--) { if (isCompleteTurn(turns[i])) return i; }
  return -1;
}
function findNextCompleted(fromIdx) {
  const turns = S.replay.turns;
  for (let i = fromIdx + 1; i < turns.length; i++) { if (isCompleteTurn(turns[i])) return i; }
  return -1;
}
function firstCompleted() {
  const turns = S.replay.turns;
  for (let i = 0; i < turns.length; i++) { if (isCompleteTurn(turns[i])) return i; }
  return 0;
}
function lastCompleted() {
  const turns = S.replay.turns;
  for (let i = turns.length - 1; i >= 0; i--) { if (isCompleteTurn(turns[i])) return i; }
  return turns.length - 1;
}
function prevTurn() {
  if (S.currentStepIdx >= 0) { S.currentStepIdx = -1; updateAll(); return; }
  const prev = findPrevCompleted(S.currentTurnIdx);
  if (prev >= 0) goToTurn(prev);
}
function nextTurn() {
  const next = findNextCompleted(S.currentTurnIdx);
  if (next >= 0) goToTurn(next);
}
function nextStep() {
  if (!S.replay) return; const turn = currentTurn(); if (!turn) return;
  const steps = turn.steps || [];
  if (S.currentStepIdx + 1 < steps.length) { S.currentStepIdx++; updateAll(); }
}
function prevStep() {
  if (S.currentStepIdx > 0) { S.currentStepIdx--; updateAll(); }
  else if (S.currentStepIdx === 0) { S.currentStepIdx = -1; updateAll(); }
}

// ============================================================================
// Playback with skip
// ============================================================================
function togglePlay() { S.playing ? stopPlayback() : startPlayback(); }

function startPlayback() {
  if (!S.replay) return; S.playing = true;
  DOM.btnPlay.textContent = '⏸'; DOM.btnPlay.classList.add('playing');
  hideBattleOverlay(); scheduleNextStep();
}

function stopPlayback() {
  S.playing = false; if (S.playTimer) clearTimeout(S.playTimer); S.playTimer = null;
  DOM.btnPlay.textContent = '▶'; DOM.btnPlay.classList.remove('playing');
}

function scheduleNextStep() {
  if (!S.playing) return;
  const turn = currentTurn(); if (!turn) { stopPlayback(); return; }
  const steps = turn.steps || [];
  const baseDelay = 1200 / S.playSpeed;

  // Find next meaningful step
  let nextIdx = S.currentStepIdx + 1;
  while (nextIdx < steps.length) {
    const s = steps[nextIdx];
    if (s.type === 'tool_result') { if (!READ_TOOLS.has(s.name)) break; nextIdx++; continue; }
    if (s.type === 'assistant') break;
    if (s.type === 'retry_prompt') break;
    nextIdx++;
  }

  if (nextIdx < steps.length) {
    const step = steps[nextIdx];
    const isMutating = step.type === 'tool_result' && WRITE_TOOLS.has(step.name);
    const isEndTurn = step.type === 'tool_result' && step.name === 'end_turn';
    const delay = isMutating ? baseDelay * 1.5 : isEndTurn ? baseDelay * 0.8 : baseDelay * 0.25;

    S.playTimer = setTimeout(() => {
      S.currentStepIdx = nextIdx; updateAll();

      if (isEndTurn && stepHasBattles(step)) {
        const battleDelay = Math.max(3500, baseDelay * 3.5);
        S.playTimer = setTimeout(() => {
          hideBattleOverlay();
          const nextTurn = findNextCompleted(S.currentTurnIdx);
          if (nextTurn >= 0) {
            S.currentTurnIdx = nextTurn; S.currentStepIdx = -1; updateAll();
            S.playTimer = setTimeout(() => scheduleNextStep(), baseDelay * 0.6);
          } else { stopPlayback(); }
        }, battleDelay);
      } else {
        S.playTimer = setTimeout(() => scheduleNextStep(), delay * 0.25);
      }
    }, delay);
  } else {
    S.playTimer = setTimeout(() => {
      const nextTurn = findNextCompleted(S.currentTurnIdx);
      if (nextTurn >= 0) {
        S.currentTurnIdx = nextTurn; S.currentStepIdx = -1; updateAll();
        S.playTimer = setTimeout(() => scheduleNextStep(), baseDelay * 1.0);
      } else { stopPlayback(); setStatus('播放完毕'); }
    }, baseDelay * 1.0);
  }
}

// ============================================================================
// Focus & Highlight
// ============================================================================
function focusOnCurrentStep() {
  if (S.currentStepIdx < 0) return;
  const step = currentStep(); if (!step || step.type !== 'tool_result') return;
  if (!WRITE_TOOLS.has(step.name)) return;

  const focus = TOOL_FOCUS[step.name];
  showActionToast(step);

  // Handle end_turn battles (for manual stepping too)
  if (step.name === 'end_turn' && stepHasBattles(step)) {
    showBattleOverlay(step);
  }

  if (!focus) return;
  if (focus.tab) switchTab(focus.tab);

  if (focus.entityType && focus.idArg) {
    const args = step.arguments || {};
    const entityId = args[focus.idArg];
    if (entityId) setTimeout(() => flashEntity(focus.entityType, entityId, step), 200);
  }

  if (step.name === 'end_turn') {
    const args = step.arguments || {};
    const hunts = args.hunts || [];
    hunts.forEach((hunt, i) => {
      setTimeout(() => {
        if (hunt.adventurer_id) flashEntity('adventurer', hunt.adventurer_id, step);
        if (hunt.monster_id) flashEntity('monster', hunt.monster_id, step);
      }, 250 + i * 150);
    });
    // Also show any new adventurers from observation_after
    if (step.observation_after) {
      const newAdvs = step.observation_after.adventurers || [];
      setTimeout(() => {
        newAdvs.forEach(a => flashEntity('adventurer', a.adventurer_id, step));
      }, 500);
    }
  }

  // For recruit, find the new adventurer from observation_after
  if (step.name === 'recruit_adventurer' && step.observation_after) {
    const obs = step.observation_after;
    const prevObs = currentTurn().observation_before;
    if (prevObs) {
      const prevIds = new Set((prevObs.adventurers||[]).map(a=>a.adventurer_id));
      const newAdvs = (obs.adventurers||[]).filter(a => !prevIds.has(a.adventurer_id));
      setTimeout(() => newAdvs.forEach(a => flashEntity('adventurer', a.adventurer_id, step)), 300);
    }
  }

  // For craft, find the newly created equipment from observation_after
  if (step.name === 'craft_equipment' && step.observation_after) {
    const obs = step.observation_after;
    const prevObs = currentTurn().observation_before;
    if (prevObs) {
      const prevIds = new Set((prevObs.equipment_inventory||[]).map(e=>e.instance_id));
      const newItems = (obs.equipment_inventory||[]).filter(e => !prevIds.has(e.instance_id));
      setTimeout(() => newItems.forEach(e => flashEntity('equipment', e.instance_id, step)), 300);
    }
  }

  // For dismiss, flash remaining adventurers to highlight party change
  if (step.name === 'dismiss_adventurer' && step.observation_after) {
    const remaining = step.observation_after.adventurers || [];
    setTimeout(() => remaining.forEach(a => flashEntity('adventurer', a.adventurer_id, step)), 200);
  }
}

function flashEntity(entityType, entityId, step) {
  let sel;
  switch (entityType) {
    case 'adventurer': sel = `.adventurer-card[data-id="${CSS.escape(entityId)}"]`; break;
    case 'monster': sel = `.entity-card[data-id="${CSS.escape(entityId)}"]`; break;
    case 'equipment': sel = `.entity-card[data-id="${CSS.escape(entityId)}"]`; break;
    case 'upgrade': flashListItems(DOM.upgradeList, entityId); return;
    case 'recipe': flashListItems(DOM.recipeList, entityId); return;
    default: return;
  }
  const el = document.querySelector(sel); if (!el) return;
  el.classList.remove('focus-flash'); void el.offsetWidth; el.classList.add('focus-flash');
  el.scrollIntoView({ block: 'center', behavior: 'smooth' });
}

function flashListItems(container, id) {
  for (const item of container.querySelectorAll('.list-item')) {
    if ((item.textContent||'').includes(id)) {
      item.classList.remove('focus-flash'); void item.offsetWidth; item.classList.add('focus-flash');
      item.scrollIntoView({ block: 'center', behavior: 'smooth' }); return;
    }
  }
}

// ============================================================================
// Toast
// ============================================================================
let toastTimer = null;
function showActionToast(step) {
  if (toastTimer) clearTimeout(toastTimer);
  const name = step.name || '', content = step.content || '';
  const firstLine = content.split('\n')[0].trim();
  let summary = firstLine;
  if (firstLine.startsWith('OK ')) summary = firstLine.slice(3);
  else if (firstLine.startsWith('FAIL ')) summary = '❌ ' + firstLine.slice(5);
  DOM.actionToast.innerHTML = `<span class="toast-icon">${stepIcon(name)}</span>${esc(summary)}`;
  DOM.actionToast.classList.add('show'); DOM.actionToast.setAttribute('aria-hidden','false');
  toastTimer = setTimeout(() => {
    DOM.actionToast.classList.remove('show'); DOM.actionToast.setAttribute('aria-hidden','true');
  }, 2500);
}
function stepIcon(n) {
  const m={recruit_adventurer:'📥',dismiss_adventurer:'📤',allocate_experience:'⭐',equip_item:'⚔️',unequip_item:'🔓',craft_equipment:'🔨',purchase_upgrade:'📈',end_turn:'⚔️'};
  return m[n]||'🔧';
}

// ============================================================================
// Battle Overlay (staged animation)
// ============================================================================
function stepHasBattles(step) {
  if (!step || step.name !== 'end_turn') return false;
  return /战斗/.test(step.content||'');
}

/** Extract HP change from content like "黑魔法师 HP: 250 -> 180" */
function parseHpChanges(content) {
  const map = {};
  const re = /(\S+?)\s*HP[:：]\s*(\d+)\s*->\s*(\d+)/g;
  let m;
  while ((m = re.exec(content)) !== null) {
    map[m[1]] = { before: parseInt(m[2]), after: parseInt(m[3]) };
  }
  return map;
}

/** Merge parsed battles with HP data from observations */
function enrichBattles(battles, step) {
  const hpChanges = parseHpChanges(step.content || '');
  const obsBefore = currentTurn().observation_before;
  const obsAfter = step.observation_after;

  const advBefore = keyById((obsBefore&&obsBefore.adventurers)||[], 'adventurer_id');
  const advAfter = keyById((obsAfter&&obsAfter.adventurers)||[], 'adventurer_id');
  const monsBefore = keyById((obsBefore&&obsBefore.monsters)||[], 'monster_id');

  return battles.map(b => {
    // Adventurer HP: try hpChanges first, then observation
    let advHpBefore = null, advHpAfter = null, advMaxHp = 100;
    if (hpChanges[b.adventurer]) {
      advHpBefore = hpChanges[b.adventurer].before;
      advHpAfter = hpChanges[b.adventurer].after;
    }
    for (const [id, adv] of Object.entries(advBefore)) {
      if (adv.name === b.adventurer) {
        if (advHpBefore === null && adv.resources) advHpBefore = adv.resources.current_hp;
        if (adv.effective_stats) advMaxHp = adv.effective_stats.hp || 100;
        break;
      }
    }
    if (advHpAfter === null) {
      for (const [id, adv] of Object.entries(advAfter)) {
        if (adv.name === b.adventurer && adv.resources) { advHpAfter = adv.resources.current_hp; break; }
      }
    }

    // Monster HP: get max HP from observation_before monster stats
    let monMaxHp = 100, monHpBefore = 100;
    console.log('[Battle] looking for monster:', b.monster, 'in', Object.entries(monsBefore).map(([id,m])=>m.name));
    for (const [id, mon] of Object.entries(monsBefore)) {
      if (mon.name === b.monster && mon.stats) {
        monMaxHp = mon.stats.hp || 100;
        monHpBefore = monMaxHp; // Monster starts at full HP
        console.log('[Battle] matched monster HP:', monMaxHp);
        break;
      }
    }
    const monHpAfter = b.won ? 0 : (monHpBefore * 0.4); // If won, monster dies; if lost, ~40% HP remaining

    return {
      ...b,
      advHpBefore: advHpBefore ?? 100,
      advHpAfter: advHpAfter ?? 50,
      advMaxHp,
      advHpBeforePct: advMaxHp > 0 ? Math.max(2, (advHpBefore ?? 100) / advMaxHp * 100) : 50,
      advHpAfterPct: advMaxHp > 0 ? Math.max(2, (advHpAfter ?? 50) / advMaxHp * 100) : 30,
      monHpBefore,
      monHpAfter,
      monMaxHp,
      monHpBeforePct: monMaxHp > 0 ? 100 : 60,
      monHpAfterPct: monMaxHp > 0 ? Math.max(2, monHpAfter / monMaxHp * 100) : 30,
    };
  });
}

function showBattleOverlay(step) {
  console.log('[Battle] showBattleOverlay called');
  DOM.battleOverlay.classList.remove('active');
  S.battleVisible = true;

  const content = step.content || '';
  const summary = content.split('\n')[0].replace('OK end_turn: ','').replace('OK end_turn','');
  const battles = enrichBattles(parseBattles(content), step);
  console.log('[Battle] parsed battles:', battles.length, battles);

  // Show backdrop immediately
  DOM.battleOverlay.classList.add('active');
  DOM.battleOverlay.setAttribute('aria-hidden','false');

  if (battles.length === 0) {
    // Fallback: show raw content rather than empty screen
    const hasBattleSection = /战斗/.test(content);
    const lines = content.split('\n').slice(0, 15).map(l => esc(l)).join('<br>');
    DOM.battleStage.innerHTML = `<div class="battle-stage-inner" style="flex-direction:column;max-width:600px;text-align:center">
      <div class="battle-summary-header" style="animation:combatant-enter 400ms ease-out both">⚔️ ${esc(summary)}</div>
      ${hasBattleSection ? `<div style="font-size:12px;color:var(--text-secondary);margin-top:8px;line-height:1.8">${lines}</div>` : ''}
      <div class="battle-result-badge win" style="animation:combatant-enter 400ms ease-out both">✓ 回合结束</div>
    </div>
    <div class="battle-close-hint">点击任意处关闭 · 按 ESC</div>`;
    return;
  }

  if (battles.length === 1) _showSingleBattle(battles[0], summary);
  else _showMultiBattle(battles, summary);
}

// ================================================================
// Single battle: VS display with staged animation
// ================================================================
function _showSingleBattle(b, summary) {
  // Build DOM with inline initial styles
  DOM.battleStage.innerHTML = `
    <div class="battle-stage-inner single">
      <div class="battle-combatant" data-side="left" style="animation:combatant-enter 500ms ease-out both">
        <div class="battle-avatar">⚔️</div>
        <div class="battle-name">${esc(b.adventurer)}</div>
        <div class="battle-class">冒险者</div>
        <div class="battle-hp-bar"><div class="battle-hp-fill" id="bhpAdv" style="width:${b.advHpBeforePct.toFixed(1)}%"></div></div>
        <div class="battle-hp-text">HP <span id="bhpAdvNum">${fmt(b.advHpBefore)}</span>/${fmt(b.advMaxHp)}</div>
      </div>
      <div class="battle-vs">
        <div class="battle-vs-icon" id="bvsIcon">⚡VS⚡</div>
        <div class="battle-result-badge" id="bbadge" style="opacity:0;transform:scale(0.5);transition:opacity 400ms ease,transform 400ms ease">${b.won?'🏆 胜利':'💀 战败'}</div>
        ${b.rewardText?`<div class="battle-rewards" id="breward" style="opacity:0;transition:opacity 400ms ease">${esc(b.rewardText)}</div>`:''}
      </div>
      <div class="battle-combatant" data-side="right" style="animation:combatant-enter 500ms ease-out 150ms both">
        <div class="battle-avatar">👹</div>
        <div class="battle-name">${esc(b.monster)}</div>
        <div class="battle-class">怪物 · T${b.monsterTier||'?'}</div>
        <div class="battle-hp-bar"><div class="battle-hp-fill" id="bhpMon" style="width:100%"></div></div>
        <div class="battle-hp-text">HP <span id="bhpMonNum">${fmt(b.monHpBefore)}</span>/${fmt(b.monMaxHp)}</div>
      </div>
    </div>
    <div class="battle-close-hint">点击任意处关闭 · 按 ESC</div>`;

  // Phase timing
  const advFrom = b.advHpBeforePct, advTo = b.advHpAfterPct;
  const monFrom = b.monHpBeforePct, monTo = b.monHpAfterPct;

  // Phase 1: Clash at 700ms
  setTimeout(() => {
    const vs = document.getElementById('bvsIcon');
    if (vs) { vs.textContent = '💥'; vs.style.animation = 'clash-flash 400ms ease-out'; }
    document.querySelectorAll('#battleStage .battle-combatant').forEach(el => {
      el.style.animation = 'shake 350ms ease-in-out';
    });
  }, 700);

  // Phase 2: HP bars animate at 1200ms
  setTimeout(() => {
    _animateHp('bhpAdv', advFrom, advTo, 900, () => {
      const n = document.getElementById('bhpAdvNum'); if (n) n.textContent = fmt(b.advHpAfter);
    });
    _animateHp('bhpMon', monFrom, monTo, 900, () => {
      const n = document.getElementById('bhpMonNum'); if (n) n.textContent = fmt(b.monHpAfter);
    });
  }, 1200);

  // Phase 3: Results at 2300ms
  setTimeout(() => {
    const badge = document.getElementById('bbadge');
    if (badge) { badge.style.opacity = '1'; badge.style.transform = 'scale(1)'; badge.classList.add(b.won?'win':'loss'); }
    const rew = document.getElementById('breward');
    if (rew) rew.style.opacity = '1';
    // Color combatants
    document.querySelectorAll('#battleStage .battle-combatant').forEach(el => {
      const side = el.dataset.side;
      el.classList.add(b.won ? (side==='left'?'winner':'loser') : (side==='left'?'loser':'winner'));
    });
  }, 2300);
}

// ================================================================
// Multi-battle: staggered rounds
// ================================================================
function _showMultiBattle(battles, summary) {
  const wins = battles.filter(b=>b.won).length;
  const rows = battles.map((b,i) => `
    <div class="battle-round" id="bround${i}" style="animation:combatant-enter 400ms ease-out ${i*100}ms both">
      <div class="battle-combatant mini" data-side="left">
        <span class="battle-avatar-sm">⚔️</span>
        <div><div class="battle-name-sm">${esc(b.adventurer)}</div><div class="battle-hp-text">HP <span id="bhpAdvNum${i}">${fmt(b.advHpBefore)}</span> → <span id="bhpAdvAfter${i}">${fmt(b.advHpAfter)}</span></div></div>
      </div>
      <div class="battle-result-badge" id="bbadge${i}" style="opacity:0;transform:scale(0.5);transition:opacity 400ms ease,transform 400ms ease">${b.won?'胜':'负'}</div>
      <div class="battle-combatant mini" data-side="right">
        <span class="battle-avatar-sm">👹</span>
        <div><div class="battle-name-sm">${esc(b.monster)}</div><div class="battle-hp-text">HP ?</div></div>
      </div>
    </div>`).join('');

  DOM.battleStage.innerHTML = `
    <div class="battle-stage-inner multi">
      <div class="battle-summary-header" style="animation:combatant-enter 400ms ease-out both">⚔️ ${esc(summary)} · ${wins} 胜 ${battles.length-wins} 负</div>
      <div class="battle-multi-list">${rows}</div>
      ${battles[0].rewardText?`<div class="battle-rewards" id="breward" style="opacity:0;transition:opacity 400ms ease">总奖励: ${esc(battles[0].rewardText)}</div>`:''}
    </div>
    <div class="battle-close-hint">点击任意处关闭 · 按 ESC</div>`;

  // Staggered results
  battles.forEach((b, i) => {
    setTimeout(() => {
      const badge = document.getElementById('bbadge'+i);
      if (badge) { badge.style.opacity = '1'; badge.style.transform = 'scale(1)'; badge.classList.add(b.won?'win':'loss'); }
      const round = document.getElementById('bround'+i);
      if (round) round.querySelectorAll('.battle-combatant').forEach(el => {
        el.classList.add(b.won ? (el.dataset.side==='left'?'winner':'loser') : (el.dataset.side==='left'?'loser':'winner'));
      });
    }, 800 + i * 350);
  });

  const lastDelay = 800 + battles.length * 350 + 400;
  setTimeout(() => {
    const rew = document.getElementById('breward');
    if (rew) rew.style.opacity = '1';
  }, lastDelay);
}

// ================================================================
// JS-driven HP bar animation (requestAnimationFrame)
// ================================================================
function _animateHp(elId, fromPct, toPct, durationMs, onDone) {
  const el = document.getElementById(elId);
  if (!el) return;
  const start = performance.now();
  function tick(now) {
    const p = Math.min((now - start) / durationMs, 1);
    // Ease-out cubic
    const t = 1 - Math.pow(1 - p, 3);
    el.style.width = (fromPct + (toPct - fromPct) * t).toFixed(1) + '%';
    if (p < 1) requestAnimationFrame(tick);
    else if (onDone) onDone();
  }
  requestAnimationFrame(tick);
}

function hideBattleOverlay() {
  S.battleVisible = false;
  DOM.battleOverlay.classList.remove('active');
  DOM.battleOverlay.setAttribute('aria-hidden','true');
}

// ============================================================================
// Rank Chart
// ============================================================================
function showRankChart() {
  if (!S.replay || !S.replay.turns) return;
  const turns = S.replay.turns;
  const labels = [];
  const data = [];
  const pointBgColors = [];

  turns.forEach((t, i) => {
    labels.push(t.turn != null ? t.turn : i + 1);
    data.push(t.rank_score != null ? Math.round(t.rank_score) : null);
    pointBgColors.push(i === S.currentTurnIdx ? '#f59e0b' : '#6366f1');
  });

  // Destroy previous instance
  if (S.rankChartInstance) { S.rankChartInstance.destroy(); S.rankChartInstance = null; }

  const ctx = DOM.rankChartCanvas.getContext('2d');
  S.rankChartInstance = new Chart(ctx, {
    type: 'line',
    data: {
      labels,
      datasets: [{
        label: 'Rank Score',
        data,
        borderColor: '#6366f1',
        backgroundColor: 'rgba(99, 102, 241, 0.10)',
        pointBackgroundColor: pointBgColors,
        pointBorderColor: pointBgColors,
        pointRadius: data.length > 50 ? 2 : 4,
        pointHoverRadius: 6,
        borderWidth: 2,
        fill: true,
        tension: 0.3,
        spanGaps: true,
      }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { display: false },
        tooltip: {
          backgroundColor: '#161c2e',
          borderColor: '#1d2440',
          borderWidth: 1,
          titleColor: '#e4e9f2',
          bodyColor: '#a8b2cc',
          padding: 10,
          cornerRadius: 6,
          callbacks: {
            title: items => `回合 ${items[0].label}`,
            label: item => item.raw != null ? `Rank: ${item.raw}` : '无数据',
          },
        },
      },
      scales: {
        x: {
          title: { display: true, text: '回合', color: '#636d8c', font: { size: 12 } },
          grid: { color: 'rgba(29, 36, 64, 0.6)' },
          ticks: { color: '#636d8c', maxTicksLimit: 20 },
        },
        y: {
          title: { display: true, text: 'Rank Score', color: '#636d8c', font: { size: 12 } },
          grid: { color: 'rgba(29, 36, 64, 0.6)' },
          ticks: { color: '#636d8c' },
          beginAtZero: false,
        },
      },
      animation: { duration: 400, easing: 'easeOutCubic' },
    },
  });

  DOM.chartOverlay.classList.add('active');
  DOM.chartOverlay.setAttribute('aria-hidden', 'false');
}

function hideRankChart() {
  DOM.chartOverlay.classList.remove('active');
  DOM.chartOverlay.setAttribute('aria-hidden', 'true');
  if (S.rankChartInstance) { S.rankChartInstance.destroy(); S.rankChartInstance = null; }
}

function parseBattles(content) {
  const battles = [];
  const lines = content.split('\n');
  console.log('[Battle] parsing content, lines:', lines.length);
  let inBattle = false;

  for (const line of lines) {
    if (/^战斗[:：]/.test(line.trim())) { inBattle = true; console.log('[Battle] found battle section'); continue; }
    if (!inBattle) continue;
    if (!line.trim().startsWith('-')) {
      if (line.trim() && !/^(变化|预算|新装备|已购买|新冒险者|总奖励)/.test(line.trim())) inBattle = false;
      continue;
    }

    // Format: "- N AdventurerName vs N MonsterName: 胜/负; 奖励 {...}"
    // Names may be single-word (Chinese) or multi-word (English)
    let m = line.match(/^\s*-\s+(\d+)\s+(.+?)\s+vs\s+(\d+)\s+(.+?)[:：]\s*(胜|负)/);
    if (!m) {
      console.log('[Battle] unmatched line:', line);
      continue;
    }

    const won = m[5] === '胜';
    let rewardText = '';
    const rw = line.match(/奖励\s*\{([^}]+)\}/);
    if (rw) {
      const parts = rw[1].split(/[,，]\s*/).map(p => {
        const kv = p.trim().split(/[:：]/);
        if (kv.length < 2) return '';
        const k = kv[0].trim(); const v = kv[1].trim();
        const kl = { '金币':'💰','gold':'💰','经验':'⭐','experience':'⭐','铁':'🔩','iron':'🔩','木':'🪵','wood':'🪵','布':'🧵','cloth':'🧵','皮':'🪶','leather':'🪶','宝石':'💎','gem':'💎' };
        return `${kl[k]||k}${v}`;
      }).filter(Boolean);
      rewardText = parts.join(' ');
    }

    battles.push({
      adventurer: m[2], adventurerRef: m[1],
      monster: m[4], monsterRef: m[3], monsterTier: null,
      won, rewardText,
    });
    console.log('[Battle] parsed:', m[2], 'vs', m[4], won ? '胜' : '负');
  }

  return battles;
}

// ============================================================================
// Update All
// ============================================================================
function updateAll() {
  renderTimeline(); renderOverview(); renderGameState(); renderLLMTrace(); updateControls();
  if (S.currentStepIdx >= 0) focusOnCurrentStep();
}

// ============================================================================
// Timeline
// ============================================================================
function renderTimeline() {
  if (!S.replay) { DOM.timelineScroll.innerHTML='<div class="timeline-empty muted">加载存档后显示</div>'; DOM.turnBadge.textContent='—'; return; }
  const turns = S.replay.turns;
  const completedCount = turns.filter(t => t.status === 'completed' || t.status === 'failed').length;
  DOM.turnBadge.textContent = `${S.currentTurnIdx+1}/${turns.length}`;
  let html = '';
  turns.forEach((turn,idx) => {
    if (turn.status !== 'completed' && turn.status !== 'failed') return;
    let cls = 'timeline-turn'; if (idx===S.currentTurnIdx) cls+=' active'; if (idx===S.currentTurnIdx && S.currentStepIdx>=0) cls+=' step-active';
    const dotCls = turn.status==='completed'?'completed':'failed';
    const writeSteps = (turn.steps||[]).filter(s=>s.type==='tool_result'&&WRITE_TOOLS.has(s.name));
    const endS = writeSteps.find(s=>s.name==='end_turn');
    let summary = '';
    if (endS) { const bm = (endS.content||'').match(/(\d+)\s*场战斗[,，]\s*(\d+)\s*胜/); summary = bm?`⚔ ${bm[2]}/${bm[1]} 胜`:'✓ end_turn'; }
    if (turn.status==='failed') summary = summary||turn.failure_reason||'失败';
    const rankHtml = turn.rank_score != null ? `<div class="turn-rank">🏅 R${Math.round(turn.rank_score)}</div>` : '';
    html += `<div class="${cls}" data-turn="${idx}" onclick="goToTurn(${idx})"><div class="turn-dot ${dotCls}"></div><div class="turn-info"><div class="turn-label">回合 ${turn.turn}</div><div class="turn-summary">${summary||writeSteps.length+' 操作'}</div>${rankHtml}${formatTurnMeta(turn.timing_usage)}</div></div>`;
  });
  DOM.timelineScroll.innerHTML = html;
  const a = DOM.timelineScroll.querySelector('.timeline-turn.active'); if (a) a.scrollIntoView({block:'nearest',behavior:'smooth'});
}

// ============================================================================
// Overview
// ============================================================================
function renderOverview() {
  const obs = getEffectiveObservation();
  if (!obs) { DOM.ovTurn.textContent='—';DOM.ovMaxTurn.textContent='—';DOM.ovGold.textContent='—';DOM.ovExp.textContent='—';DOM.ovMaterials.textContent='—';DOM.ovParty.textContent='—';DOM.ovScore.textContent='—';DOM.ovStats.textContent='—'; return; }
  DOM.ovTurn.textContent = obs.turn||(S.currentTurnIdx+1);
  DOM.ovMaxTurn.textContent = obs.max_turns||'—';
  DOM.ovGold.textContent = fmt(obs.gold);
  DOM.ovExp.textContent = fmt(obs.experience_pool);
  const mats = obs.materials||{}; DOM.ovMaterials.textContent = Object.entries(mats).map(([k,v])=>`${matLabel(k)}:${fmt(v)}`).join(' ')||'无材料';
  const advs = obs.adventurers||[]; DOM.ovParty.textContent = `${advs.length}/${obs.party_size_limit||'?'}`;
  if (S.replay&&S.replay.score) { DOM.ovScore.textContent = S.replay.score.total_score||S.replay.score.score||'—'; } else { DOM.ovScore.textContent='—'; }
  // Per-turn rank score with fallback to overall
  const curTurn = currentTurn();
  const turnRank = curTurn && curTurn.rank_score != null ? Math.round(curTurn.rank_score) : (S.replay&&S.replay.score&&S.replay.score.rank_score!=null ? Math.round(S.replay.score.rank_score) : null);
  DOM.ovRank.textContent = turnRank != null ? turnRank : '—';
  // Run stats
  const stats = computeReplayStats(S.replay);
  if (stats) {
    const parts = [];
    if (stats.timing && stats.timing.total_duration_ms > 0) parts.push(`⏱ ${formatReplayDuration(stats.timing.total_duration_ms)}`);
    if (stats.token_usage) {
      const tp = [];
      if (stats.token_usage.input_tokens) tp.push(`in ${stats.token_usage.input_tokens.toLocaleString()}`);
      if (stats.token_usage.output_tokens) tp.push(`out ${stats.token_usage.output_tokens.toLocaleString()}`);
      if (tp.length) parts.push(`🔤 ${tp.join('·')}`);
    }
    if (stats.tool_calls && stats.tool_calls.total > 0) parts.push(`🔧 ${stats.tool_calls.total} (✓${stats.tool_calls.successful} ✗${stats.tool_calls.failed})`);
    if (stats.game_actions && stats.game_actions.battles_total > 0) parts.push(`⚔ ${stats.game_actions.battles_won}/${stats.game_actions.battles_total}胜`);
    if (stats.game_actions && stats.game_actions.total_gold_earned > 0) parts.push(`💰 ${stats.game_actions.total_gold_earned.toLocaleString()}`);
    if (stats.game_actions && stats.game_actions.total_experience_earned > 0) parts.push(`⭐ ${stats.game_actions.total_experience_earned.toLocaleString()}`);
    DOM.ovStats.textContent = parts.length ? parts.join(' · ') : '—';
  } else {
    DOM.ovStats.textContent = '—';
  }
}

// ============================================================================
// Game State (uses effective observation)
// ============================================================================
function renderGameState() {
  const obs = getEffectiveObservation();
  if (!obs) { DOM.adventurerCards.innerHTML='<div class="empty-state"><span class="muted">加载存档后显示</span></div>'; DOM.monsterCards.innerHTML=''; DOM.inventoryCards.innerHTML=''; DOM.recipeList.innerHTML=''; DOM.upgradeList.innerHTML=''; return; }
  const refs = refsForObservation(obs);
  renderAdventurers(obs.adventurers||[], refs);
  renderMonsters(obs.monsters||[], refs);
  renderInventory(obs.equipment_inventory||[], refs);
  renderRecipes(obs.crafting_recipes||[], refs);
  renderUpgrades(obs.global_upgrades||[], refs);
}

function refsForObservation(obs) {
  if (!obs) return {};
  const r = {adventurer:{},monster:{},equipment:{},recipe:{},upgrade:{},recruit:{}};
  (obs.adventurers||[]).forEach((a,i)=>{r.adventurer[a.adventurer_id]=i+1;});
  (obs.monsters||[]).forEach((m,i)=>{r.monster[m.monster_id]=i+1;});
  (obs.equipment_inventory||[]).forEach((e,i)=>{r.equipment[e.instance_id]=i+1;});
  (obs.crafting_recipes||[]).forEach((rc,i)=>{r.recipe[rc.recipe_id]=i+1;});
  (obs.global_upgrades||[]).forEach((u,i)=>{r.upgrade[u.upgrade_id]=i+1;});
  (obs.recruit_candidates||[]).forEach((c,i)=>{r.recruit[c.candidate_id]=i+1;});
  return r;
}

// --- Cards ---
function renderAdventurers(advs, refs) { DOM.adventurerCards.innerHTML = advs.length ? advs.map(a=>adventurerCard(a,refs)).join('') : '<div class="empty-state"><span class="muted">无冒险者</span></div>'; }
function adventurerCard(a, refs) {
  const rid = refId(refs,'adventurer',a.adventurer_id); const s=a.effective_stats||{}; const r=a.resources||{};
  const hpPct = s.hp?Math.max(0,Math.min(100,(r.current_hp/s.hp)*100)):0;
  const mpPct = s.mp?Math.max(0,Math.min(100,(r.current_mp/s.mp)*100)):0;
  const hpc = hpPct>50?'hp':hpPct>25?'hp warning':'hp danger';
  const skills = a.skills||[]; const slots = a.equipment_slots||[];
  const rankContribution = rankContributionForAdventurer(a.adventurer_id);
  const rankContributionHtml = rankContribution ? `
    <div class="rank-contrib">
      <span>Rank 贡献</span>
      <strong>${esc(fmtRankScore(rankContribution.score))}</strong>
      ${rankContribution.share != null ? `<em>${esc(fmtPct(rankContribution.share))}</em>` : ''}
    </div>` : '';
  return `<div class="entity-card adventurer-card" data-id="${esc(a.adventurer_id)}">
    <div class="card-header">
      <div class="avatar-slot"><div class="avatar-placeholder">${esc((a.name||'?')[0])}</div></div>
      <div class="card-info"><div class="card-name">${esc(a.name||'?')}</div><div class="card-subtitle">Lv.${fmt(a.level)} · ${esc(a.template_id||'')}${rid?' · ID '+rid:''}</div></div>
    </div>
    <div class="resource-bars">
      <div class="res-bar"><span class="res-bar-label">HP</span><div class="res-bar-track"><div class="res-bar-fill ${hpc}" style="width:${hpPct}%"></div></div><span class="res-bar-value">${fmt(r.current_hp)}/${fmt(s.hp)}</span></div>
      <div class="res-bar"><span class="res-bar-label">MP</span><div class="res-bar-track"><div class="res-bar-fill mp" style="width:${mpPct}%"></div></div><span class="res-bar-value">${fmt(r.current_mp)}/${fmt(s.mp)}</span></div>
    </div>
    <div class="card-stats">${statCell('攻击',s.attack)}${statCell('防御',s.defense)}${statCell('速度',s.speed)}${statCell('恢复',s.recovery)}${statCell('回魔',s.mp_recovery)}</div>
    ${skills.length?`<div class="skill-chips">${skills.map(sk=>`<span class="skill-chip ${sk.kind==='active'?'active':''}" title="${esc(skillTip(sk))}">${esc(sk.name||sk.skill_id)}</span>`).join('')}</div>`:''}
    ${slots.length?`<div class="equipment-slots">${slots.map(sl=>equipSlot(sl,refs)).join('')}</div>`:''}
    ${rankContributionHtml}
    <div class="card-subtitle" style="margin-top:6px;">经验: ${fmt(a.experience)}${a.next_level?' → Lv.'+(a.next_level.preview_level||'?'):''}</div>
  </div>`;
}
function equipSlot(sl, refs) {
  const it=sl.item;
  return it
    ? `<div class="equip-slot filled" title="${esc(it.name||sl.slot)}${refId(refs,'equipment',it.instance_id)||''}"><span class="slot-placeholder">${esc((it.name||sl.slot)[0])}</span></div>`
    : `<div class="equip-slot" title="${esc(sl.slot||'')}"><span class="slot-placeholder">${esc(slotIcon(sl.slot))}</span></div>`;
}

function renderMonsters(ms, refs) { DOM.monsterCards.innerHTML = ms.length ? ms.map(m=>monsterCard(m,refs)).join('') : '<div class="empty-state"><span class="muted">无怪物</span></div>'; }
function monsterCard(m, refs) {
  const rid=refId(refs,'monster',m.monster_id); const s=m.stats||{}; const rw=m.reward||{}; const sk=m.skills||[];
  return `<div class="entity-card" data-id="${esc(m.monster_id)}">
    <div class="card-header"><div class="avatar-slot monster"><div class="avatar-placeholder">${esc((m.name||'?')[0])}</div></div><div class="card-info"><div class="card-name">${esc(m.name||'?')}</div><div class="card-subtitle">Tier ${fmt(m.tier)} · ${esc(m.archetype_id||'')}${rid?' · ID '+rid:''}</div></div></div>
    <div class="card-stats">${statCell('HP',s.hp)}${statCell('MP',s.mp)}${statCell('攻击',s.attack)}${statCell('防御',s.defense)}${statCell('速度',s.speed)}</div>
    ${sk.length?`<div class="skill-chips">${sk.map(sk=>`<span class="skill-chip ${sk.kind==='active'?'active':''}" title="${esc(skillTip(sk))}">${esc(sk.name||sk.skill_id)}</span>`).join('')}</div>`:''}
    <div class="card-subtitle" style="margin-top:4px;">奖励: 💰${fmt(rw.gold)} ⭐${fmt(rw.experience)} ${Object.entries(rw.materials||{}).map(([k,v])=>`${matLabel(k)}:${fmt(v)}`).join(' ')}</div>
  </div>`;
}

function renderInventory(items, refs) { DOM.inventoryCards.innerHTML = items.length ? items.map(e=>inventoryCard(e,refs)).join('') : '<div class="empty-state"><span class="muted">无装备</span></div>'; }
function inventoryCard(eq, refs) {
  const rid=refId(refs,'equipment',eq.instance_id); const s=eq.stats||{}; const sk=eq.skills||[];
  return `<div class="entity-card" data-id="${esc(eq.instance_id)}">
    <div class="card-header"><div class="avatar-slot equipment"><div class="avatar-placeholder">${esc((eq.name||'?')[0])}</div></div><div class="card-info"><div class="card-name">${esc(eq.name||'?')}</div><div class="card-subtitle">${esc(eq.slot||'')} · ${eq.equipped_by?'已装备':'空闲'}${rid?' · ID '+rid:''}</div></div></div>
    ${Object.keys(s).length?`<div class="card-stats">${Object.entries(s).map(([k,v])=>statCell(statLabel(k),v)).join('')}</div>`:''}
    ${sk.length?`<div class="skill-chips">${sk.map(sk=>`<span class="skill-chip">${esc(sk.name||sk.skill_id)}</span>`).join('')}</div>`:''}
  </div>`;
}

function renderRecipes(recipes, refs) {
  DOM.recipeList.innerHTML = recipes.length
    ? recipes.map(r=>{const rid=refId(refs,'recipe',r.recipe_id);return`<div class="list-item" data-id="${esc(r.recipe_id)}" style="opacity:${r.can_craft?1:0.5}"><div class="name">${rid?'['+rid+'] ':''}${esc(r.name)} → ${esc(r.output_name)}</div><div class="detail">槽位:${esc(r.output_slot)} · 属性:${fmtMap(r.output_stats)}</div><div class="cost">💰${fmt(r.gold_cost)} · 材料:${fmtMap(r.material_costs)} · ${r.can_craft?'✅':'❌'}</div></div>`;}).join('')
    : '<div class="muted" style="padding:8px">无配方</div>';
}
function renderUpgrades(upgrades, refs) {
  DOM.upgradeList.innerHTML = upgrades.length
    ? upgrades.map(u=>{const rid=refId(refs,'upgrade',u.upgrade_id);const st=u.unlocked?'✅ 已解锁':u.can_purchase?'💰 可购买':'🔒 不可购买';const prereq=upgradePrereqText(upgrades, u.required_upgrade_ids);return`<div class="list-item" data-id="${esc(u.upgrade_id)}" style="opacity:${u.unlocked?1:u.can_purchase?0.8:0.5}"><div class="name">${rid?'['+rid+'] ':''}${esc(u.name)}</div><div class="detail">属性:${fmtMap(u.stats)} · 上限+${fmt(u.party_size_bonus)}${prereq?`<br>前置：${prereq}`:''}</div><div class="cost">${st} · 💰${fmt(u.gold_cost)}</div></div>`;}).join('')
    : '<div class="muted" style="padding:8px">无升级</div>';
}

// ============================================================================
// LLM Trace
// ============================================================================
function renderLLMTrace() {
  if (!S.replay) { DOM.llmScroll.innerHTML='<div class="llm-empty muted">加载存档后显示</div>'; DOM.stepBadge.textContent='—'; return; }
  const turn = currentTurn(); if (!turn) { DOM.llmScroll.innerHTML='<div class="llm-empty muted">无数据</div>'; DOM.stepBadge.textContent='—'; return; }
  const steps = turn.steps||[];
  DOM.stepBadge.textContent = S.currentStepIdx>=0?`${S.currentStepIdx+1}/${steps.length}`:`0/${steps.length}`;
  DOM.stepBarFill.style.width = steps.length>0&&S.currentStepIdx>=0?`${((S.currentStepIdx+1)/steps.length)*100}%`:'0%';
  if (!steps.length) { DOM.llmScroll.innerHTML='<div class="llm-empty muted">本回合无工具调用</div>'; return; }

  let html = '';
  steps.forEach((step,idx) => {
    if (step.type==='turn_prompt') return;
    const active = idx===S.currentStepIdx;
    const isRead = step.type==='tool_result'&&READ_TOOLS.has(step.name);
    const isWrite = step.type==='tool_result'&&WRITE_TOOLS.has(step.name);
    html += renderLLMStep(step, idx, active, isRead, isWrite);
  });
  DOM.llmScroll.innerHTML = html;
  if (S.currentStepIdx>=0) { const a=DOM.llmScroll.querySelector('.llm-step.active'); if (a) a.scrollIntoView({block:'nearest',behavior:'smooth'}); }
}

function renderLLMStep(step, idx, active, isRead, isWrite) {
  let cls='llm-step'; if (active) cls+=' active'; if (isRead) cls+=' step-read'; if (isWrite) cls+=' step-write';
  let icon='', title='', body='';

  if (step.type==='assistant') {
    icon='<span class="llm-step-icon reasoning">🧠</span>'; title='LLM 响应';
    if (step.reasoning_content) body+=`<div class="llm-step-body reasoning">💭 ${esc(trunc(step.reasoning_content,200))}</div>`;
    if (step.tool_calls&&step.tool_calls.length) {
      body+=`<div class="llm-step-body" style="color:var(--accent);margin-top:4px;">调用 ${step.tool_calls.length} 个工具:</div>`;
      step.tool_calls.forEach(tc=>{const fn=tc.function?tc.function.name:(tc.name||'?');const fa=tc.function?tc.function.arguments:(tc.arguments||{});body+=`<div class="llm-args">🔧 ${esc(fn)}(${esc(JSON.stringify(fa,null,1))})</div>`;});
    }
    if (step.usage) body+=`<div style="font-size:10px;color:var(--muted);margin-top:2px;">Tokens: ${step.usage.input_tokens||'?'} → ${step.usage.output_tokens||'?'}</div>`;
  } else if (step.type==='tool_result') {
    const ok = step.content&&step.content.trimStart().startsWith('OK');
    const fail = step.content&&step.content.trimStart().startsWith('FAIL');
    icon=`<span class="llm-step-icon ${ok?(isWrite?'tool-ok':'tool-read'):'tool-fail'}">${ok?(isWrite?'▶':'📖'):'✗'}</span>`;
    title=`${isRead?'📖 ':isWrite?'⚡ ':''}${esc(step.name)}`;
    if (step.arguments&&Object.keys(step.arguments).length) body+=`<div class="llm-args">参数: ${esc(JSON.stringify(step.arguments,null,1))}</div>`;
    if (step.content) {
      const lines=step.content.split('\n'); body+=`<div class="llm-step-body" style="color:${ok?'var(--ok)':'var(--danger)'}">${esc(lines[0])}</div>`;
      const rest=lines.slice(1).join('\n'); if (rest.trim()) body+=`<div class="llm-step-body">${esc(trunc(rest,250))}</div>`;
    }
  } else if (step.type==='retry_prompt') {
    icon='<span class="llm-step-icon retry">↻</span>'; title='重试'; body=`<div class="llm-step-body">${esc(trunc(step.content,120))}</div>`;
  } else return '';

  return `<div class="${cls}" data-step="${idx}"><div class="llm-step-header">${icon}<span class="llm-step-title">${title}</span>${step.timing?`<span style="font-size:10px;color:var(--muted);margin-left:auto">${step.timing.duration_ms||0}ms</span>`:''}</div>${body}</div>`;
}

function formatTurnMeta(tu) {
  if (!tu) return '';
  const parts = [];
  const ms = Number(tu.duration_ms);
  if (Number.isFinite(ms) && ms > 0) parts.push(ms < 1000 ? `${Math.round(ms)} ms` : `${(ms / 1000).toFixed(1)} s`);
  if (tu.input_tokens) parts.push(`in ${tu.input_tokens}`);
  if (tu.output_tokens) parts.push(`out ${tu.output_tokens}`);
  return parts.length ? `<div class="turn-meta">${parts.join(' · ')}</div>` : '';
}

// ============================================================================
// Controls
// ============================================================================
function updateControls() {
  const h=!!S.replay; const cp=h&&(S.currentTurnIdx>0||S.currentStepIdx>=0); const cn=h&&findNextCompleted(S.currentTurnIdx)>=0;
  DOM.btnFirst.disabled=!cp; DOM.btnPrevTurn.disabled=!cp; DOM.btnPlay.disabled=!h; DOM.btnNextTurn.disabled=!cn; DOM.btnLast.disabled=!cn;
  if (S.playing) { DOM.btnPlay.textContent='⏸'; DOM.btnPlay.classList.add('playing'); }
  else { DOM.btnPlay.textContent='▶'; DOM.btnPlay.classList.remove('playing'); }
}
function switchTab(t) {
  $$('#stateTabs .tab').forEach(tb=>tb.classList.toggle('active',tb.dataset.tab===t));
  $$('.tab-content').forEach(p=>p.classList.toggle('active',p.dataset.panel===t));
}

// ============================================================================
// Helpers
// ============================================================================
function esc(s) { if(!s)return''; return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;'); }
function fmt(v) { if(v==null)return'—'; return typeof v==='number'?v.toLocaleString():String(v); }
function trunc(t,l) { if(!t)return''; const s=String(t); return s.length<=l?s:s.slice(0,l)+'…'; }
function statCell(l,v) { return `<div class="stat-item"><span class="stat-label">${l}</span><span class="stat-value">${fmt(v)}</span></div>`; }
function statLabel(k) { const m={hp:'HP',mp:'MP',attack:'攻击',defense:'防御',speed:'速度',recovery:'恢复',mp_recovery:'回魔'}; return m[k]||k; }
function matLabel(k) { const m={iron:'铁',wood:'木',cloth:'布',leather:'皮',gem:'宝石',herb:'草药',crystal:'水晶',essence:'精华'}; return m[k]||k; }
function upgradePrereqText(upgrades, ids) { if (!ids || !ids.length) return ''; return ids.map((id) => { const u = upgrades.find((g) => g.upgrade_id === id); return u ? u.name : id; }).join('、'); }
function fmtRankScore(v) { return v == null ? '—' : Number(v).toLocaleString(undefined, { maximumFractionDigits: 1 }); }
function fmtPct(v) { return v == null ? '—' : `${(Number(v) * 100).toFixed(1)}%`; }
function slotIcon(s) { const m={main_hand:'⚔️',off_hand:'🛡️',two_hand:'🗡️',helmet:'⛑️',armor:'🛡️',boots:'👢',accessory:'💍'}; return m[s]||'?'; }
function fmtMap(o) { if(!o||!Object.keys(o).length)return'—'; return Object.entries(o).map(([k,v])=>`${statLabel(k)}:${fmt(v)}`).join(' '); }
function skillTip(s) { const p=[]; if(s.kind)p.push(`类型:${s.kind}`); if(s.mp_cost!=null)p.push(`MP:${s.mp_cost}`); if(s.condition)p.push(`条件:${s.condition}`); if(s.effects){p.push('效果:'+(Array.isArray(s.effects)?s.effects:[s.effects]).map(e=>`${e.type||'?'}:${e.value!=null?(e.value>0?'+':'')+e.value:'?'}`).join(', '));} return p.join('\n'); }
function refId(refs,c,id) { return (refs&&refs[c]&&id)?(refs[c][id]??null):null; }
function setStatus(m,e) { DOM.statusText.textContent=m; DOM.statusText.style.color=e?'var(--danger)':'var(--muted)'; }
function keyById(arr,key) { const m={}; for(const it of arr){if(it&&it[key])m[it[key]]=it;} return m; }

function contributionItems(score) {
  if (!score || typeof score !== 'object') return [];
  const values = Array.isArray(score.rank_score_per_adventurer)
    ? score.rank_score_per_adventurer
    : Array.isArray(score.per_adventurer)
      ? score.per_adventurer
      : [];
  return values
    .filter(item => item && typeof item === 'object')
    .map(item => {
      const scoreValue = item.rank_score_contribution ?? item.rank_score;
      return {
        adventurer_id: item.adventurer_id,
        name: item.name,
        score: Number(scoreValue),
        share: item.rank_score_share != null ? Number(item.rank_score_share) : null,
      };
    })
    .filter(item => item.adventurer_id != null && Number.isFinite(item.score));
}

function rankContributionForAdventurer(adventurerId) {
  const items = contributionItems(S.replay?.score);
  return items.find(item => String(item.adventurer_id) === String(adventurerId)) || null;
}

// ============================================================================
// Replay Stats (client-side computation from turns/steps)
// ============================================================================
function formatReplayDuration(ms) {
  if (ms < 1000) return `${Math.round(ms)} ms`;
  return `${(ms / 1000).toFixed(2)} s`;
}

function structuredEndTurnStats(step) {
  const battles = step?.result?.turn_result?.battles;
  if (!Array.isArray(battles)) return null;
  const stats = {
    battlesTotal: 0,
    battlesWon: 0,
    battlesLost: 0,
    goldEarned: 0,
    expEarned: 0,
  };
  for (const battle of battles) {
    if (!battle || typeof battle !== "object") continue;
    stats.battlesTotal++;
    const won = battleWon(battle);
    if (won === true) stats.battlesWon++;
    else if (won === false) stats.battlesLost++;
    const reward = battle.reward && typeof battle.reward === "object" ? battle.reward : {};
    const gold = Number(reward.gold);
    const exp = Number(reward.experience);
    if (Number.isFinite(gold)) stats.goldEarned += gold;
    if (Number.isFinite(exp)) stats.expEarned += exp;
  }
  return stats;
}

function battleWon(battle) {
  if (!battle || typeof battle !== "object") return null;
  if (battle.won === true) return true;
  if (battle.won === false) return false;
  const outcome = battle.outcome ?? battle.result ?? battle.winner ?? battle.status;
  if (typeof outcome !== "string") return null;
  const value = outcome.toLowerCase();
  if (["left_win", "adventurer_win", "player_win", "win", "won", "victory"].includes(value)) return true;
  if (["right_win", "monster_win", "enemy_win", "loss", "lost", "defeat"].includes(value)) return false;
  return null;
}

function toolStepSucceeded(step, content) {
  if (step?.ok === true || step?.result?.ok === true) return true;
  if (step?.ok === false || step?.result?.ok === false || step?.error) return false;
  const stripped = String(content || "").trimStart();
  if (stripped.startsWith("OK") || stripped.startsWith("成功")) return true;
  if (stripped.startsWith("FAIL") || stripped.startsWith("失败") || stripped.startsWith("ERROR") || stripped.startsWith("错误")) return false;
  try {
    const data = JSON.parse(stripped);
    if (typeof data?.ok === "boolean") return data.ok;
  } catch {}
  return false;
}

function textRewardStats(content) {
  const rewardLines = String(content || "")
    .split(/\r?\n/)
    .filter(line => /奖励|reward/i.test(line));
  const battleRewardLines = rewardLines.filter(line => /^\s*-\s+/.test(line));
  const lines = battleRewardLines.length ? battleRewardLines : rewardLines;
  let goldEarned = 0;
  let expEarned = 0;
  for (const line of lines) {
    const gold = line.match(/(?:金币|gold)\s*[:=＝]\s*(\d+)/i);
    const exp = line.match(/(?:经验|experience|exp)\s*[:=＝]\s*(\d+)/i);
    if (gold) goldEarned += parseInt(gold[1], 10);
    if (exp) expEarned += parseInt(exp[1], 10);
  }
  return { goldEarned, expEarned };
}

function computeReplayStats(replay) {
  if (!replay || !Array.isArray(replay.turns)) return null;
  let totalMs = 0, inputTokens = 0, outputTokens = 0, cacheRead = 0, cacheWrite = 0;
  let totalCalls = 0, successfulCalls = 0, failedCalls = 0;
  let battlesTotal = 0, battlesWon = 0, battlesLost = 0;
  let goldEarned = 0, expEarned = 0;
  let crafted = 0, upgrades = 0, allocated = 0, recruited = 0, dismissed = 0, equipped = 0, unequipped = 0;
  let modelSteps = 0, turnsCompleted = 0, turnsFailed = 0;

  // Prefer pre-computed stats from replay.json
  const savedGA = replay.stats && replay.stats.game_actions;
  if (savedGA) {
    goldEarned = savedGA.total_gold_earned || 0;
    expEarned = savedGA.total_experience_earned || 0;
  }

  for (const turn of replay.turns) {
    if (!turn || typeof turn !== "object") continue;
    if (turn.status === "completed") turnsCompleted++;
    else if (turn.status === "failed") turnsFailed++;

    if (turn.timing_usage) {
      const tu = turn.timing_usage;
      if (typeof tu.duration_ms === "number") totalMs += tu.duration_ms;
      if (typeof tu.input_tokens === "number") inputTokens += tu.input_tokens;
      if (typeof tu.output_tokens === "number") outputTokens += tu.output_tokens;
    }
    const steps = Array.isArray(turn.steps) ? turn.steps : [];
    for (const step of steps) {
      if (!step || typeof step !== "object") continue;
      if (step.type === "assistant") {
        modelSteps++;
        if (!turn.timing_usage) {
          if (step.timing?.duration_ms != null) totalMs += Number(step.timing.duration_ms);
          const u = step.usage || {};
          const inp = u.input_tokens ?? u.prompt_tokens;
          const out = u.output_tokens ?? u.completion_tokens;
          if (typeof inp === "number") inputTokens += inp;
          if (typeof out === "number") outputTokens += out;
          if (typeof u.cache_read_input_tokens === "number") cacheRead += u.cache_read_input_tokens;
          if (typeof u.cache_creation_input_tokens === "number") cacheWrite += u.cache_creation_input_tokens;
        }
      }
      if (step.type === "tool_result") {
        totalCalls++;
        const name = step.name || "";
        const content = typeof step.content === "string" ? step.content : "";
        const ok = toolStepSucceeded(step, content);
        if (ok) {
          successfulCalls++;
          if (name === "craft_equipment") crafted++;
          else if (name === "purchase_upgrade") upgrades++;
          else if (name === "allocate_experience") allocated++;
          else if (name === "recruit_adventurer") recruited++;
          else if (name === "dismiss_adventurer") dismissed++;
          else if (name === "equip_item") equipped++;
          else if (name === "unequip_item") unequipped++;
          else if (name === "end_turn") {
            const structured = structuredEndTurnStats(step);
            if (structured) {
              battlesTotal += structured.battlesTotal;
              battlesWon += structured.battlesWon;
              battlesLost += structured.battlesLost;
              if (!savedGA) {
                goldEarned += structured.goldEarned;
                expEarned += structured.expEarned;
              }
            } else {
              const bm = content.match(/(\d+)\s*场战斗[,，]\s*(\d+)\s*胜\s*(\d+)\s*负/);
              if (bm) { battlesTotal += parseInt(bm[1], 10); battlesWon += parseInt(bm[2], 10); battlesLost += parseInt(bm[3], 10); }
              if (!savedGA) {
                const rewards = textRewardStats(content);
                goldEarned += rewards.goldEarned;
                expEarned += rewards.expEarned;
              }
            }
          }
        } else {
          failedCalls++;
        }
      }
    }
  }

  const tokenUsage = { input_tokens: inputTokens, output_tokens: outputTokens };
  if (cacheRead > 0) tokenUsage.cache_read_input_tokens = cacheRead;
  if (cacheWrite > 0) tokenUsage.cache_creation_input_tokens = cacheWrite;

  return {
    timing: { total_duration_ms: Math.round(totalMs), total_duration_seconds: Math.round(totalMs) / 1000 },
    tool_calls: { total: totalCalls, successful: successfulCalls, failed: failedCalls },
    token_usage: tokenUsage,
    game_actions: {
      battles_total: battlesTotal, battles_won: battlesWon, battles_lost: battlesLost,
      total_gold_earned: goldEarned, total_experience_earned: expEarned,
      total_equipment_crafted: crafted, total_upgrades_purchased: upgrades,
      total_recruits: recruited, total_dismissals: dismissed,
      total_experience_allocated: allocated, total_equips: equipped, total_unequips: unequipped,
    },
    model_interaction: { total_model_steps: modelSteps, total_turns_completed: turnsCompleted, total_turns_failed: turnsFailed },
  };
}

// ============================================================================
// Boot
// ============================================================================
document.addEventListener('DOMContentLoaded', init);
