/**
 * Guild Manager Bench · Leaderboard
 *
 * Standalone static leaderboard that reads leaderboard_data.json
 * and renders model ranking cards sorted by rank_score.
 * Includes rank_score curve comparison using Chart.js.
 */

// ============================================================================
// DOM Helpers
// ============================================================================
const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => document.querySelectorAll(sel);

// esc() 高频调用 — 复用模块级单例元素，避免每次创建 div
const _escEl = document.createElement('div');
function esc(str) {
  _escEl.textContent = str;
  return _escEl.innerHTML.replaceAll('"', '&quot;');
}

// ============================================================================
// Formatting
// ============================================================================
function fmtRankScore(val) {
  if (val == null) return null;
  return val.toLocaleString('en-US', { maximumFractionDigits: 1 });
}

function fmtScore(val) {
  if (val == null) return null;
  return val.toFixed(2);
}

function fmtPct(val) {
  if (val == null) return null;
  return (val * 100).toFixed(1) + '%';
}

function fmtInt(val) {
  if (val == null) return null;
  return Number(val).toLocaleString('en-US', { maximumFractionDigits: 0 });
}

function fmtTimestamp(ts) {
  if (!ts) return '—';
  const m = ts.match(/^(\d{4})(\d{2})(\d{2})-(\d{2})(\d{2})(\d{2})/);
  if (!m) return ts;
  return `${m[1]}-${m[2]}-${m[3]} ${m[4]}:${m[5]}:${m[6]}`;
}

function shortRunId(value) {
  if (!value) return '';
  const text = String(value);
  if (text.length <= 24) return text;
  return `${text.slice(0, 12)}…${text.slice(-8)}`;
}

function renderModelNote(modelName) {
  const note = _modelNotes[modelName];
  if (!note || !note.trim()) return '';
  return `<span class="model-note-tag" title="${esc(note)}">${esc(note)}</span>`;
}

function fmtDuration(seconds) {
  if (seconds == null) return null;
  const total = Math.round(seconds);
  if (total < 60) return total + 's';
  if (total >= 3600) return Math.floor(total / 3600) + 'h ' + Math.floor(total % 3600 / 60) + 'm';
  return Math.floor(total / 60) + 'm ' + total % 60 + 's';
}

function fmtTokens(n) {
  if (n == null) return null;
  if (n >= 1000000) return (n / 1000000).toFixed(2) + 'M';
  if (n >= 1000) return `${(n / 1000).toFixed(1)}k`;
  return String(n);
}

const MEDALS = ['#1', '#2', '#3'];

// ── 多口径极端值徽标（替代 Top X%）
// 给每个模型在 7 个维度上算「极值」，最多展示 3 个徽标
// 维度：成本类（mint）/ 表现类（accent）/ 战果类（gold）
const BADGE_CATEGORIES = [
  { key: 'min_input',   dir: 'min', label: '最省输入',   tone: 'mint',   pick: (m) => m.efficiency?.input_tokens?.mean },
  { key: 'min_output',  dir: 'min', label: '最省输出',   tone: 'mint',   pick: (m) => m.efficiency?.output_tokens?.mean },
  { key: 'fastest',     dir: 'min', label: '最快',       tone: 'mint',   pick: (m) => m.efficiency?.duration_seconds?.mean },
  { key: 'least_ops',   dir: 'min', label: '最少操作',   tone: 'mint',   pick: (m) => m.efficiency?.tool_calls?.mean },
  { key: 'top_winrate', dir: 'max', label: '最高胜率',   tone: 'accent', pick: (m) => m.game_quality?.battle_win_rate },
  { key: 'most_gold',   dir: 'max', label: '最多金币',   tone: 'gold',   pick: (m) => m.game_quality?.gold_earned?.mean },
  { key: 'most_exp',    dir: 'max', label: '最多经验',   tone: 'gold',   pick: (m) => m.game_quality?.exp_earned?.mean },
];

function isExcludedFromBadges(model) {
  if (model?.is_baseline === true) return true;
  const name = typeof model?.model === 'string' ? model.model : '';
  return name.startsWith('Baseline ·') || name === '✋ 手动操作';
}

// 聚合一个模型所有 run 中击败过的最强怪物
function strongestKillPower(m) {
  if (!Array.isArray(m.run_details)) return null;
  let max = null;
  for (const r of m.run_details) {
    const p = r?.game_actions?.strongest_defeated_enemy?.power;
    if (p != null && (max == null || p > max)) max = p;
  }
  return max;
}

// 计算每个模型的多口径徽标。返回 Map: modelName -> [{label, tone}]
function computeModelBadges(models) {
  const result = new Map();
  for (const m of models) result.set(m.model, []);
  const eligibleModels = models.filter((m) => !isExcludedFromBadges(m));

  // 标准维度
  for (const cat of BADGE_CATEGORIES) {
    const candidates = [];
    for (const m of eligibleModels) {
      const v = cat.pick(m);
      if (v != null && Number.isFinite(v) && v > 0) {
        candidates.push({ model: m.model, val: v });
      }
    }
    if (!candidates.length) continue;
    const target = cat.dir === 'min'
      ? Math.min(...candidates.map((c) => c.val))
      : Math.max(...candidates.map((c) => c.val));
    for (const c of candidates) {
      // 允许并列：所有打到极值的模型都拿徽标
      if (c.val === target) {
        result.get(c.model).push({ label: cat.label, tone: cat.tone });
      }
    }
  }

  // 最强击败（聚合跨 run）
  const killCandidates = [];
  for (const m of eligibleModels) {
    const p = strongestKillPower(m);
    if (p != null) killCandidates.push({ model: m.model, val: p });
  }
  if (killCandidates.length) {
    const maxKill = Math.max(...killCandidates.map((c) => c.val));
    for (const c of killCandidates) {
      if (c.val === maxKill) {
        result.get(c.model).push({ label: '最强击败', tone: 'gold' });
      }
    }
  }

  // 每张卡最多 3 个徽标 — 超过会变视觉噪音
  for (const [k, v] of result) {
    result.set(k, v.slice(0, 3));
  }

  return result;
}

function renderBadges(badges) {
  if (!badges || !badges.length) return '';
  const items = badges
    .map((b) => `<span class="badge-tag tone-${esc(b.tone)}">${esc(b.label)}</span>`)
    .join('');
  return `<div class="model-badges">${items}</div>`;
}

// ============================================================================
// Global State
// ============================================================================
let _leaderboardData = null;
let _modelNotes = {};       // model name -> note string
let _cardModels = [];       // 当前排行榜 models，供详情懒渲染按 index 取用
let _curveChart = null;
let _curveRunSelection = {}; // key: "model::run_id" -> boolean
let _curveMetric = 'rank_score';
let _curveRunsCache = null;  // allCurveRuns 结果按指标缓存，指标切换/数据重载时失效
let _curveRunsCacheMetric = null;
let _chartJsPromise = null;  // Chart.js 懒加载 Promise（失败时重置以允许重试）
let _adventurerTooltipSeq = 0;
let _adventurerTooltipHideTimer = null;
let _leaderboardSearchQuery = '';
let _leaderboardFilter = 'all';
let _leaderboardSort = 'rank';
let _activeTab = 'leaderboard';
let _comparisonModels = new Set();
let _expandedModels = new Set();
let _modelBadges = new Map();
let _adventurerTooltipRaf = null; // scroll/resize 重定位的 rAF 节流句柄
const _adventurerTooltipDetails = new Map();

// ============================================================================
// Chart.js 懒加载 — 首次进入「曲线对比」才动态注入 CDN script
// ============================================================================
const CHART_JS_URL = 'https://cdn.jsdelivr.net/npm/chart.js@4.4.7/dist/chart.umd.min.js';
function ensureChartJs() {
  if (window.Chart) return Promise.resolve();
  if (_chartJsPromise) return _chartJsPromise;
  _chartJsPromise = new Promise((resolve, reject) => {
    const s = document.createElement('script');
    s.src = CHART_JS_URL;
    s.async = true;
    s.onload = () => resolve();
    s.onerror = () => {
      _chartJsPromise = null; // 允许重试
      reject(new Error('Chart.js 加载失败'));
    };
    document.head.appendChild(s);
  });
  return _chartJsPromise;
}

// Palette for curve lines — distinct colors readable on dark background
const CURVE_COLORS = ['#91adf2', '#d7b779', '#86cbb2', '#b8a0da'];
function curveColor(modelName) {
  return CURVE_COLORS[[..._comparisonModels].indexOf(modelName) % CURVE_COLORS.length];
}

const CURVE_METRICS = {
  rank_score: {
    label: '段位积分',
    empty: '没有可用的段位积分曲线数据',
    value: (pt) => pt?.rank_score,
    curve: (run) => Array.isArray(run.rank_score_curve) ? run.rank_score_curve : [],
  },
  gold: {
    label: '累计金币收入',
    empty: '没有可用的累计金币收入曲线数据',
    value: (pt) => pt?.cumulative_gold_earned,
    curve: (run) => Array.isArray(run.game_actions?.economy_curve) ? run.game_actions.economy_curve : [],
  },
  experience: {
    label: '累计经验收入',
    empty: '没有可用的累计经验收入曲线数据',
    value: (pt) => pt?.cumulative_experience_earned,
    curve: (run) => Array.isArray(run.game_actions?.economy_curve) ? run.game_actions.economy_curve : [],
  },
};

function currentCurveMetric() {
  return CURVE_METRICS[_curveMetric] || CURVE_METRICS.rank_score;
}

function toolLabel(name) {
  const labels = {
    get_party: '查看队伍',
    get_monsters: '查看怪物',
    get_crafting: '查看制作',
    get_inventory: '查看背包',
    get_upgrades: '查看升级',
    get_recruitment: '查看招募',
    get_events: '查看事件',
    preview_battle: '预览战斗',
    craft_equipment: '制作装备',
    purchase_upgrade: '购买升级',
    allocate_experience: '分配经验',
    recruit_adventurer: '招募冒险者',
    dismiss_adventurer: '遣散冒险者',
    write_memo: '写备忘录',
    preview_team_power: '预览战力',
    equip_item: '装备物品',
    unequip_item: '卸下装备',
    end_turn: '结束回合',
  };
  return labels[name] || name || '未知工具';
}

// ============================================================================
// Tab Navigation
// ============================================================================
// 两个 <main> 的显隐统一走 class + hidden 语义，不再写内联 style
function setMainVisible(el, visible) {
  if (!el) return;
  el.classList.toggle('main-hidden', !visible);
  el.hidden = !visible;
}

function syncViewUrl() {
  const url = new URL(window.location.href);
  const values = { q: _leaderboardSearchQuery, type: _leaderboardFilter === 'all' ? '' : _leaderboardFilter,
    sort: _leaderboardSort === 'rank' ? '' : _leaderboardSort, tab: _activeTab === 'curves' ? 'curves' : '' };
  for (const [key, value] of Object.entries(values)) {
    if (value) url.searchParams.set(key, value);
    else url.searchParams.delete(key);
  }
  url.searchParams.delete('compare');
  for (const name of _comparisonModels) url.searchParams.append('compare', name);
  window.history.replaceState(null, '', url);
}

function restoreView() {
  const params = new URLSearchParams(window.location.search);
  _leaderboardSearchQuery = params.get('q') || '';
  _leaderboardFilter = ['all', 'models', 'reference'].includes(params.get('type')) ? params.get('type') : 'all';
  _leaderboardSort = ['rank', 'output', 'duration', 'runs'].includes(params.get('sort')) ? params.get('sort') : 'rank';
  const names = new Set(_leaderboardData.models.map(m => m.model));
  _comparisonModels = new Set(params.getAll('compare').filter(name => names.has(name)).slice(0, 4));
  $('#leaderboardSort').value = _leaderboardSort;
  updateFilterButtons();
}

function setActiveTab(name, { scroll = true } = {}) {
  _activeTab = name;
  $('.skip-link').href = name === 'curves' ? '#curvesMain' : '#leaderboardMain';
  $$('.tab-btn').forEach(btn => {
    const active = btn.dataset.tab === name;
    btn.classList.toggle('active', active);
    if (active) btn.setAttribute('aria-current', 'page');
    else btn.removeAttribute('aria-current');
  });
  setMainVisible($('#leaderboardMain'), name === 'leaderboard');
  setMainVisible($('#curvesMain'), name === 'curves');
  if (name === 'curves') {
    if (!_comparisonModels.size) {
      _leaderboardData.models.filter(m => !isExcludedFromBadges(m)).slice(0, 3)
        .forEach(m => _comparisonModels.add(m.model));
    }
    renderCurvePanel();
  }
  updateComparisonTray();
  syncViewUrl();
  if (scroll) window.scrollTo({ top: 0 });
}

function initTabs() {
  $$('.tab-btn').forEach(btn => btn.addEventListener('click', () => setActiveTab(btn.dataset.tab)));
  $('#backToLeaderboard').addEventListener('click', () => {
    setActiveTab('leaderboard');
    $('#leaderboardSearchInput').focus({ preventScroll: true });
  });
  const params = new URLSearchParams(window.location.search);
  setActiveTab(params.get('tab') === 'curves' ? 'curves' : 'leaderboard', { scroll: false });
  const expandIdx = Number(params.get('expand'));
  if (Number.isInteger(expandIdx) && expandIdx > 0) {
    const card = $$('.model-card')[expandIdx - 1];
    if (card) toggleCardExpanded(card, true);
  }
}

// ============================================================================
// Curve Comparison
// ============================================================================

/**
 * Collect all runs across all models that have the selected metric curve.
 * Returns [{model, run, key, curve, runNumber}].
 */
function allCurveRuns(data) {
  const runs = [];
  const metric = currentCurveMetric();
  for (const m of data.models) {
    (m.run_details || []).forEach((run, index) => {
      const curve = metric.curve(run).filter(pt => metric.value(pt) != null);
      if (curve.length) runs.push({ model: m.model, run, key: m.model + '::' + (run.run_id || run.session_id), curve, runNumber: index + 1 });
    });
  }
  return runs;
}

function comparisonCurveRuns(data) {
  return getCurveRuns(data).filter(r => _comparisonModels.has(r.model));
}

function renderComparisonSummary() {
  const models = [..._comparisonModels].map(name => _leaderboardData.models.find(m => m.model === name));
  if (!models.length) {
    $('#comparisonSummary').innerHTML = '<div class="empty-state"><h2>还没有可对比的模型</h2><p>返回排行榜选择模型后，再查看指标与成长曲线。</p></div>';
    return;
  }
  const metrics = [
    { label: '平均段位积分', value: m => m.rank_score?.mean, format: fmtRankScore, direction: 'max' },
    { label: '运行次数', value: m => m.runs, format: fmtInt },
    { label: '输入 Tokens', value: m => m.efficiency?.input_tokens?.mean, format: fmtTokens },
    { label: '输出 Tokens', value: m => m.efficiency?.output_tokens?.mean, format: fmtTokens },
    { label: '平均耗时', value: m => m.efficiency?.duration_seconds?.mean, format: fmtDuration },
    { label: '平均工具调用', value: m => m.efficiency?.tool_calls?.mean, format: fmtInt },
    { label: '战斗胜率', value: m => m.game_quality?.battle_win_rate, format: fmtPct },
  ];
  $('#comparisonSummary').innerHTML = '<div class="comparison-table-wrap"><table class="comparison-table"><caption class="sr-only">所选模型的聚合指标对比</caption><thead><tr><th scope="col">表现概览<span class="comparison-unit">每次运行的聚合结果</span></th>' +
    models.map(m => '<th scope="col"><span class="comparison-model"><span class="curve-color-dot" style="background:' + curveColor(m.model) + '"></span>' + esc(m.model) +
    '</span><span class="comparison-unit">全榜 #' + m.rank + ' · ' + (m.runs === 1 ? '单次结果' : m.runs + ' 次运行') + '</span></th>').join('') + '</tr></thead><tbody>' +
    metrics.map(metric => {
      const values = models.map(metric.value).filter(v => v != null);
      const best = metric.direction === 'max' && values.length ? Math.max(...values) : null;
      return '<tr><th scope="row">' + metric.label + '</th>' +
        models.map(m => {
          const value = metric.value(m);
          return '<td' + (best != null && value === best ? ' class="comparison-best"' : '') + '>' + (metric.format(value) ?? '—') + '</td>';
        }).join('') + '</tr>';
    }).join('') + '</tbody></table></div>';
}

function filterCurveLegend() {
  const query = normalizeSearchText($('#curveSearch').value.trim());
  $$('.curve-legend-item').forEach(item => { item.hidden = !normalizeSearchText(item.textContent).includes(query); });
  const items = $$('.curve-legend-item');
  $('#curveLegendEmpty').hidden = !items.length || [...items].some(item => !item.hidden);
}

// 按指标缓存 allCurveRuns 结果 — checkbox/全选/清除反复走缓存，
// 仅指标切换或数据重载（renderLeaderboard）时才重新计算
function getCurveRuns(data) {
  if (_curveRunsCache && _curveRunsCacheMetric === _curveMetric) {
    return _curveRunsCache;
  }
  _curveRunsCache = allCurveRuns(data);
  _curveRunsCacheMetric = _curveMetric;
  return _curveRunsCache;
}

// Chart.js 加载占位 / 错误提示（覆盖在 canvas 区域上）
function setCurveChartStatus(state) {
  const wrap = $('.curve-chart-wrap');
  if (!wrap) return;
  let el = wrap.querySelector('.curve-chart-status');
  if (!state) {
    el?.remove();
    return;
  }
  if (!el) {
    el = document.createElement('div');
    el.className = 'curve-chart-status';
    wrap.appendChild(el);
  }
  if (state === 'loading') {
    el.innerHTML = `
      <div class="spinner-wrap">
        <div class="spinner"></div>
        <span>正在加载图表组件…</span>
      </div>`;
  } else if (state === 'error') {
    el.innerHTML = `
      <span>图表组件加载失败，请检查网络后重试</span>
      <button class="curve-btn" type="button" id="curveRetry">重试</button>`;
    el.querySelector('#curveRetry')?.addEventListener('click', () => renderCurvePanel());
  }
}

async function renderCurvePanel() {
  const data = _leaderboardData;
  renderComparisonSummary();
  const metricSelect = $('#curveMetricSelect');
  metricSelect.value = _curveMetric;
  metricSelect.onchange = () => { _curveMetric = metricSelect.value; renderCurvePanel(); };
  const runs = comparisonCurveRuns(data);
  const metric = currentCurveMetric();
  runs.forEach(r => {
    if (!Object.prototype.hasOwnProperty.call(_curveRunSelection, r.key)) _curveRunSelection[r.key] = true;
  });
  $('#curveLegend').innerHTML = runs.map(r => {
    const checked = _curveRunSelection[r.key] ? ' checked' : '';
    return '<label class="curve-legend-item"><input type="checkbox" data-curve-key="' + esc(r.key) + '"' + checked + '>' +
      '<span class="curve-color-dot" style="background:' + curveColor(r.model) + '"></span><span class="curve-legend-content"><span class="curve-legend-model">' +
      esc(r.model) + '</span><span class="curve-legend-meta">Run ' + r.runNumber + ' · ' + esc(fmtTimestamp(r.run.created_at).slice(0, 10)) + '</span></span></label>';
  }).join('') + (runs.length ? '' : '<p class="curve-legend-placeholder">' + metric.empty + '</p>') +
    '<p id="curveLegendEmpty" class="curve-legend-placeholder" hidden>没有匹配的运行</p>';
  filterCurveLegend();
  $('#curveLegend').querySelectorAll('input').forEach(cb => cb.addEventListener('change', () => {
    _curveRunSelection[cb.dataset.curveKey] = cb.checked;
    if (window.Chart) updateCurveChart(data);
  }));
  const selectRuns = checked => {
    runs.forEach(r => { _curveRunSelection[r.key] = checked; });
    $('#curveLegend').querySelectorAll('input').forEach(cb => { cb.checked = checked; });
    if (window.Chart) updateCurveChart(data);
  };
  $('#curveSelectAll').onclick = () => selectRuns(true);
  $('#curveDeselectAll').onclick = () => selectRuns(false);
  if (!window.Chart) {
    setCurveChartStatus('loading');
    try { await ensureChartJs(); }
    catch (e) { setCurveChartStatus('error'); return; }
  }
  updateCurveChart(data);
}

function updateCurveChart(data) {
  const runs = comparisonCurveRuns(data);
  const selected = runs.filter((r) => _curveRunSelection[r.key]);

  $('#curveSelectionStatus').textContent = '已显示 ' + selected.length + ' / ' + runs.length + ' 条运行曲线';
  if (!selected.length) {
    if (_curveChart) {
      _curveChart.destroy();
      _curveChart = null;
    }
    setCurveChartStatus('empty');
    $('.curve-chart-status').textContent = runs.length ? '选择运行，查看成长曲线。' : currentCurveMetric().empty;
    return;
  }
  setCurveChartStatus(null);

  const metric = currentCurveMetric();

  // Build datasets — curve is already filtered to points for the selected metric.
  const datasets = selected.map((r) => {
    const color = curveColor(r.model);
    const points = r.curve.map((pt) => ({ x: pt.turn, y: metric.value(pt) }));
    return {
      label: r.model + ' · Run ' + r.runNumber,
      data: points,
      borderColor: color,
      backgroundColor: color,
      pointBackgroundColor: color,
      pointRadius: 0,
      pointHoverRadius: 4,
      borderWidth: 2,
      borderDash: r.runNumber === 1 ? [] : r.runNumber === 2 ? [6, 4] : [2, 4],
      tension: 0.1,
      fill: false,
    };
  });

  if (_curveChart) {
    _curveChart.data.datasets = datasets;
    if (_curveChart.options?.scales?.y?.title) {
      _curveChart.options.scales.y.title.text = metric.label;
    }
    _curveChart.update();
  } else {
    const ctx = document.getElementById('curveChart');
    if (!ctx) return;
    _curveChart = new Chart(ctx, {
      type: 'scatter',
      data: { datasets },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        animation: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? false : { duration: 180 },
        showLine: true,
        interaction: {
          mode: 'nearest',
          axis: 'x',
          intersect: false,
        },
        plugins: {
          legend: { display: false },
          tooltip: {
            backgroundColor: '#1e2025',
            titleColor: '#e8ecf4',
            bodyColor: '#bdc0c9',
            borderColor: 'rgba(255,255,255,0.1)',
            borderWidth: 1,
            padding: 12,
            cornerRadius: 8,
            titleFont: { family: 'Inter', weight: '600' },
            bodyFont: { family: 'JetBrains Mono', size: 12 },
            callbacks: {
              title: (items) => {
                const pt = items[0]?.parsed;
                return pt ? `回合 ${pt.x}` : '';
              },
              label: (item) => ` ${item.dataset.label}: ${item.parsed.y.toLocaleString('en-US', { maximumFractionDigits: 1 })}`,
            },
          },
        },
        scales: {
          x: {
            type: 'linear',
            min: 1,
            title: {
              display: true,
              text: '回合',
              color: '#93959d',
              font: { family: 'Inter', size: 12 },
            },
            ticks: { color: '#93959d', font: { family: 'JetBrains Mono', size: 11 }, stepSize: 5 },
            grid: { color: 'rgba(255,255,255,0.05)' },
          },
          y: {
            title: {
              display: true,
              text: metric.label,
              color: '#93959d',
              font: { family: 'Inter', size: 12 },
            },
            ticks: {
              color: '#93959d',
              font: { family: 'JetBrains Mono', size: 11 },
              callback: (val) => val.toLocaleString('en-US', { maximumFractionDigits: 0 }),
            },
            grid: { color: 'rgba(255,255,255,0.04)' },
            beginAtZero: true,
          },
        },
      },
    });
  }
}

// ============================================================================
// Rendering — Leaderboard Cards
// ============================================================================
function renderLeaderboard(data) {
  _curveRunsCache = null;
  _curveRunsCacheMetric = null;
  _modelBadges = computeModelBadges(data.models);
  const generated = data.generated_at ? data.generated_at.slice(0, 10) : '—';
  $('#topMeta').textContent = '数据更新于 ' + generated;
  $('#modelCount').textContent = data.models.length;
  renderStatsBanner(data);
  $('#leaderboardSearchInput').disabled = false;
  renderLeaderboardCards(data);
}

function normalizeSearchText(value) {
  return String(value || '').normalize('NFKC').toLocaleLowerCase();
}

function modelMatchesSearch(model, query) {
  if (!query) return true;
  const searchableText = [model.model, _modelNotes[model.model]]
    .filter(Boolean)
    .map(normalizeSearchText)
    .join('\n');
  return searchableText.includes(query);
}

function updateLeaderboardSearchState(visibleCount, totalCount) {
  const input = $('#leaderboardSearchInput');
  if (input.value !== _leaderboardSearchQuery) input.value = _leaderboardSearchQuery;
  $('#leaderboardSearchClear').hidden = !_leaderboardSearchQuery;
  $('#leaderboardSearchStatus').textContent = '显示 ' + visibleCount + ' / ' + totalCount + ' 个参赛条目';
}

function renderLeaderboardCards(data) {
  const query = normalizeSearchText(_leaderboardSearchQuery.trim());
  const visible = data.models.filter(m => modelMatchesSearch(m, query) &&
    (_leaderboardFilter === 'all' || (_leaderboardFilter === 'reference') === isExcludedFromBadges(m)));
  const value = m => {
    if (_leaderboardSort === 'output') return m.efficiency?.output_tokens?.mean;
    if (_leaderboardSort === 'duration') return m.efficiency?.duration_seconds?.mean;
    if (_leaderboardSort === 'runs') return -m.runs;
    return m.rank;
  };
  visible.sort((a, b) => {
    const av = value(a), bv = value(b);
    if (av == null && bv == null) return a.rank - b.rank;
    if (av == null) return 1;
    if (bv == null) return -1;
    return av - bv || a.rank - b.rank;
  });
  _cardModels = visible;
  hideAdventurerTooltip();
  _adventurerTooltipDetails.clear();
  _adventurerTooltipSeq = 0;
  updateLeaderboardSearchState(visible.length, data.models.length);
  const container = $('#cardListContainer');
  if (!visible.length) {
    container.innerHTML = '<div class="empty-state"><span class="empty-symbol" aria-hidden="true">⌕</span><h3>' +
      (data.models.length ? '没有找到匹配的模型' : '还没有评测结果') +
      '</h3><p>试试其他名称，或重置筛选条件。</p><button class="button" data-action="reset-filters" type="button">重置筛选</button></div>';
    return;
  }
  const topScore = Math.max(...data.models.map(m => m.rank_score?.mean ?? 0));
  container.innerHTML = '<table class="ranking-table"><caption class="sr-only">模型表现排行榜，默认按平均段位积分排序</caption>' +
    '<colgroup><col class="col-select"><col class="col-rank"><col class="col-model"><col class="col-score"><col class="col-runs"><col class="col-output"><col class="col-duration"><col class="col-expand"></colgroup>' +
    '<thead><tr><th scope="col"><span class="sr-only">选择对比</span></th><th scope="col">排名</th><th scope="col">模型 / 备注</th>' +
    '<th scope="col" class="numeric">平均段位积分</th><th scope="col" class="numeric">运行</th><th scope="col" class="numeric">输出 Tokens</th><th scope="col" class="numeric">耗时</th><th scope="col"><span class="sr-only">详情</span></th></tr></thead>' +
    '<tbody>' + visible.map((m, index) => renderCard(m, { topScore, index })).join('') + '</tbody></table>';
  $$('.model-card').forEach(card => {
    if (_expandedModels.has(_cardModels[Number(card.dataset.cardIdx)].model)) toggleCardExpanded(card, true);
  });
  updateComparisonTray();
}

// ============================================================================
// Card Interactions — 容器级事件委托 + 详情懒渲染
// ============================================================================

// 首次展开某卡时才生成详情 HTML 注入（最大的 DOM 减重项）；
// 冒险者 tooltip 数据（_adventurerTooltipDetails）也在此时注册
function ensureCardDetailRendered(card) {
  if (card.dataset.detailLoaded === 'true') return;
  const m = _cardModels[Number(card.dataset.cardIdx)];
  card.nextElementSibling.querySelector('.card-detail').innerHTML = renderCardDetail(m);
  card.dataset.detailLoaded = 'true';
}

function toggleCardExpanded(card, force) {
  const expand = force != null ? force : !card.classList.contains('expanded');
  const m = _cardModels[Number(card.dataset.cardIdx)];
  if (expand) { ensureCardDetailRendered(card); _expandedModels.add(m.model); }
  else { _expandedModels.delete(m.model); hideAdventurerTooltip(); }
  card.classList.toggle('expanded', expand);
  card.nextElementSibling.hidden = !expand;
  card.querySelectorAll('[data-action="toggle-detail"]').forEach(btn => btn.setAttribute('aria-expanded', String(expand)));
  card.querySelector('.expand-button').setAttribute('aria-label', (expand ? '收起 ' : '展开 ') + m.model + ' 详情');
}

let _cardInteractionsBound = false;
function initCardInteractions() {
  if (_cardInteractionsBound) return;
  _cardInteractionsBound = true;
  const container = $('#cardListContainer');
  if (!container) return;

  container.addEventListener('click', e => {
    const adventurer = e.target.closest('[data-adventurer-tooltip]');
    if (adventurer) { showAdventurerTooltip(adventurer); return; }
    if (e.target.closest('[data-action="reset-filters"]')) {
      _leaderboardSearchQuery = '';
      _leaderboardFilter = 'all';
      _leaderboardSort = 'rank';
      $('#leaderboardSort').value = 'rank';
      updateFilterButtons();
      renderLeaderboardCards(_leaderboardData);
      syncViewUrl();
      $('#leaderboardSearchInput').focus();
      return;
    }
    const toggle = e.target.closest('[data-action="toggle-detail"]');
    if (toggle) toggleCardExpanded(toggle.closest('.model-card'));
  });
  container.addEventListener('change', e => {
    const checkbox = e.target.closest('[data-compare-model]');
    if (!checkbox) return;
    const name = checkbox.dataset.compareModel;
    if (checkbox.checked) _comparisonModels.add(name);
    else _comparisonModels.delete(name);
    updateComparisonTray();
    syncViewUrl();
  });
  container.addEventListener('keydown', e => {
    if (e.key === 'Escape') hideAdventurerTooltip();
  });
  document.addEventListener('pointerdown', e => {
    if (!e.target.closest('[data-adventurer-tooltip], .adventurer-tooltip')) hideAdventurerTooltip();
  });

  // 冒险者 tooltip — 委托 mouseover/mouseout + focusin/focusout
  container.addEventListener('mouseover', (e) => {
    const anchor = e.target.closest('[data-adventurer-tooltip]');
    if (!anchor) return;
    const tooltip = $('#adventurerTooltip');
    if (tooltip && !tooltip.hidden && tooltip.dataset.anchor === anchor.dataset.adventurerTooltip) {
      // 在同一 anchor 内移动：仅取消隐藏计时
      cancelAdventurerTooltipHide();
      return;
    }
    showAdventurerTooltip(anchor);
  });
  container.addEventListener('mouseout', (e) => {
    if (e.target.closest('[data-adventurer-tooltip]')) scheduleAdventurerTooltipHide();
  });
  container.addEventListener('focusin', (e) => {
    const anchor = e.target.closest('[data-adventurer-tooltip]');
    if (anchor) showAdventurerTooltip(anchor);
  });
  container.addEventListener('focusout', (e) => {
    if (e.target.closest('[data-adventurer-tooltip]')) scheduleAdventurerTooltipHide();
  });

  // tooltip 本体 hover 保持 + window scroll/resize 重定位（rAF 节流）
  const tooltip = ensureAdventurerTooltip();
  tooltip.addEventListener('mouseenter', cancelAdventurerTooltipHide);
  tooltip.addEventListener('mouseleave', scheduleAdventurerTooltipHide);
  window.addEventListener('resize', scheduleAdventurerTooltipReposition);
  window.addEventListener('scroll', scheduleAdventurerTooltipReposition, true);
}

// scroll/resize 高频触发 — 用 rAF 合并到每帧最多一次重定位
function scheduleAdventurerTooltipReposition() {
  if (_adventurerTooltipRaf != null) return;
  _adventurerTooltipRaf = requestAnimationFrame(() => {
    _adventurerTooltipRaf = null;
    repositionOpenAdventurerTooltip();
  });
}

function updateFilterButtons() {
  $$('[data-filter]').forEach(btn => {
    const active = btn.dataset.filter === _leaderboardFilter;
    btn.classList.toggle('active', active);
    btn.setAttribute('aria-pressed', String(active));
  });
}

function clearLeaderboardSearch() {
  _leaderboardSearchQuery = '';
  renderLeaderboardCards(_leaderboardData);
  syncViewUrl();
  $('#leaderboardSearchInput').focus();
}

function initLeaderboardSearch() {
  const input = $('#leaderboardSearchInput');
  input.addEventListener('input', () => {
    _leaderboardSearchQuery = input.value;
    renderLeaderboardCards(_leaderboardData);
    syncViewUrl();
  });
  input.addEventListener('keydown', e => {
    if (e.key === 'Escape' && input.value) clearLeaderboardSearch();
  });
  $('#leaderboardSearchClear').addEventListener('click', clearLeaderboardSearch);
  $$('[data-filter]').forEach(btn => btn.addEventListener('click', () => {
    _leaderboardFilter = btn.dataset.filter;
    updateFilterButtons();
    renderLeaderboardCards(_leaderboardData);
    syncViewUrl();
  }));
  $('#leaderboardSort').addEventListener('change', e => {
    _leaderboardSort = e.target.value;
    renderLeaderboardCards(_leaderboardData);
    syncViewUrl();
  });
  $('#clearComparison').addEventListener('click', () => {
    _comparisonModels.clear();
    updateComparisonTray();
    syncViewUrl();
    $('#leaderboardSearchInput').focus({ preventScroll: true });
  });
  $('#startComparison').addEventListener('click', () => {
    setActiveTab('curves');
    $('#backToLeaderboard').focus({ preventScroll: true });
  });
  $('#curveSearch').addEventListener('input', filterCurveLegend);
}

function updateComparisonTray() {
  const count = _comparisonModels.size;
  $('#compareTray').hidden = count === 0 || _activeTab !== 'leaderboard';
  document.body.classList.toggle('has-comparison', count > 0 && _activeTab === 'leaderboard');
  $('#compareCount').textContent = '已选 ' + count + ' / 4';
  $('#compareNames').textContent = [..._comparisonModels].join(' · ');
  $('#startComparison').disabled = count < 2;
  $('#selectionMessage').textContent = count === 4 ? '已选择 4 项。取消一项后可选择其他模型。' : '';
  $$('[data-compare-model]').forEach(checkbox => {
    checkbox.checked = _comparisonModels.has(checkbox.dataset.compareModel);
    checkbox.disabled = count >= 4 && !checkbox.checked;
    checkbox.closest('.model-card').classList.toggle('selected', checkbox.checked);
  });
}

function renderStatsBanner(data) {
  const models = data.models;
  const scores = models.map(m => m.rank_score?.mean).filter(v => v != null);
  const best = models.map(m => m.rank_score?.best).filter(v => v != null);
  const items = [
    ['参赛条目', String(models.length), '模型与参考基线'],
    ['完成运行', String(data.total_runs), '已归档的评测结果'],
    ['单次最高分', best.length ? fmtRankScore(Math.max(...best)) : '—', 'Rank Score'],
    ['全榜平均分', scores.length ? fmtRankScore(scores.reduce((a,b) => a+b, 0) / scores.length) : '—', '各条目均分的平均值']
  ];
  $('#statsBannerMetrics').innerHTML = items.map((it, i) => '<div class="overview-stat"><dt>' + it[0] +
    '</dt><dd' + (i === 2 ? ' class="gold-value"' : '') + '>' + it[1] + '</dd><span>' + it[2] + '</span></div>').join('');
  const runs = models.flatMap(m => m.run_details || []);
  const seeds = [...new Set(runs.map(r => r.game_seed).filter(v => v != null))].join(', ');
  const scoring = [...new Set(runs.map(r => r.scoring_seed).filter(v => v != null))].join(', ');
  $('#seedSummary').textContent = 'Game seed: ' + (seeds || '—') + ' / Scoring seed: ' + (scoring || '—');
}

function renderCard(m, ctx = {}) {
  const score = m.rank_score?.mean;
  const note = _modelNotes[m.model] || '';
  const kind = m.model === '✋ 手动操作' ? '人工参考' : isExcludedFromBadges(m) ? '自动基线' : '';
  const detailId = 'model-detail-' + ctx.index;
  const out = m.efficiency?.output_tokens?.mean;
  const duration = m.efficiency?.duration_seconds?.mean;
  const percent = ctx.topScore > 0 && score != null ? Math.max(0, score / ctx.topScore * 100) : 0;
  const kindMarkup = kind ? '<span class="reference-badge">' + kind + '</span>' : '';
  return '<tr class="model-card' + (m.rank <= 3 ? ' rank-' + m.rank : '') + '" data-rank="' + m.rank + '" data-card-idx="' + ctx.index + '">' +
    '<td class="select-cell"><label class="compare-check"><input type="checkbox" data-compare-model="' + esc(m.model) + '" aria-label="对比 ' + esc(m.model) + '"></label></td>' +
    '<td class="rank-cell"><span class="rank-badge">' + String(m.rank).padStart(2, '0') + '</span></td>' +
    '<th scope="row" class="model-cell"><button class="model-name" data-action="toggle-detail" aria-expanded="false" aria-controls="' + detailId + '" type="button">' + esc(m.model) + '</button>' +
    '<div class="model-subline">' + kindMarkup + '<span class="model-note">' + esc(note || (kind ? '参考表现' : 'LLM Agent')) + '</span></div></th>' +
    '<td class="score-cell numeric"><span class="mobile-label">平均段位积分</span><strong>' + (fmtRankScore(score) ?? '—') + '</strong>' +
    '<span class="score-track" aria-hidden="true"><span style="width:' + percent.toFixed(2) + '%"></span></span></td>' +
    '<td class="runs-cell numeric"><span class="run-count">' + m.runs + '<span class="mobile-label"> 次运行</span></span><span class="sample-note">' + (m.runs === 1 ? '单次结果' : '多次均值') + '</span></td>' +
    '<td class="output-cell numeric"><span class="mobile-label">输出</span>' + (fmtTokens(out) ?? '—') + '</td>' +
    '<td class="duration-cell numeric"><span class="mobile-label">耗时</span>' + (fmtDuration(duration) ?? '—') + '</td>' +
    '<td class="expand-cell"><button class="expand-button" data-action="toggle-detail" aria-expanded="false" aria-controls="' + detailId + '" aria-label="展开 ' + esc(m.model) + ' 详情" type="button"><span aria-hidden="true">⌄</span></button></td></tr>' +
    '<tr class="model-detail-row" id="' + detailId + '" hidden><td colspan="8"><div class="card-detail"></div></td></tr>';
}

// 卡片详情（懒渲染）：聚合指标 + 运行明细，首次展开时才调用
function renderCardDetail(m) {
  const runDetails = Array.isArray(m.run_details) ? m.run_details : [];
  const latestRun = runDetails[0] || {};
  const rs = m.rank_score;
  const dismissals = runDetails.reduce((sum, run) => sum + (run.tool_calls?.by_name_detail?.dismiss_adventurer?.total ?? run.tool_calls?.by_name?.dismiss_adventurer ?? 0), 0);
  const quality = dismissals > 0 ? { ...m.game_quality, dismissals } : m.game_quality || {};

  // Detail rows（结构化数据，传给 renderAggregateList 分组渲染）
  const details = [];
  if (rs) {
    details.push({ label: 'Rank Score · 最佳', value: fmtRankScore(rs.best) || '—' });
    details.push({ label: 'Rank Score · 均值', value: fmtRankScore(rs.mean) || '—' });
    details.push({ label: 'Rank Score · 中位', value: fmtRankScore(rs.median) || '—' });
  }
  if (m.last_run) {
    details.push({ label: '最近运行', value: fmtTimestamp(m.last_run) });
  }
  if (latestRun.preset) {
    details.push({ label: 'Preset', value: latestRun.preset });
  }
  if (latestRun.game_seed != null) {
    details.push({ label: 'Game Seed', value: fmtInt(latestRun.game_seed) });
  }
  if (latestRun.scoring_seed != null) {
    details.push({ label: 'Scoring Seed', value: fmtInt(latestRun.scoring_seed) });
  }
  if (latestRun.data_hash) {
    details.push({ label: 'Data Hash', value: latestRun.data_hash });
  }

  return `
    <div class="detail-model-heading"><h3>${esc(m.model)}</h3>${renderBadges(_modelBadges.get(m.model))}</div>
    ${_modelNotes[m.model] ? `<p class="detail-note">${esc(_modelNotes[m.model])}</p>` : ''}
    <div class="metrics-row">${renderEfficiencySection(m.efficiency || {})}${renderGameQualitySection(quality)}</div>
    <div class="detail-section">
      <div class="detail-title">
        <span>聚合指标</span>
        <span class="detail-count">${details.length} 项</span>
      </div>
      ${renderAggregateList(details)}
    </div>
    ${renderRunDetails(runDetails)}`;
}

function renderEfficiencySection(eff) {
  const cells = [];
  if (eff.input_tokens != null) {
    const v = typeof eff.input_tokens === 'object' ? eff.input_tokens.mean : eff.input_tokens;
    cells.push(metricCell('输入 Tokens', fmtInt(v) + ' tok'));
  }
  if (eff.output_tokens != null) {
    const v = typeof eff.output_tokens === 'object' ? eff.output_tokens.mean : eff.output_tokens;
    cells.push(metricCell('输出 Tokens', fmtInt(v) + ' tok'));
  }
  if (eff.duration_seconds != null) {
    const v = typeof eff.duration_seconds === 'object' ? eff.duration_seconds.mean : eff.duration_seconds;
    cells.push(metricCell('耗时', fmtDuration(v)));
  }
  if (eff.tool_calls != null) {
    const v = typeof eff.tool_calls === 'object' ? eff.tool_calls.mean : eff.tool_calls;
    cells.push(metricCell('工具调用', fmtInt(v)));
  }
  if (!cells.length) return '';
  return `
    <div class="metric-group">
      <div class="metric-group-title">运行效率</div>
      <dl class="metric-group-cells">${cells.join('')}</dl>
    </div>`;
}

function renderGameQualitySection(gq) {
  const cells = [];
  if (gq.battle_win_rate != null) {
    cells.push(metricCell('战斗胜率', (gq.battle_win_rate * 100).toFixed(1) + '%'));
  }
  if (gq.gold_earned != null) {
    const v = typeof gq.gold_earned === 'object' ? gq.gold_earned.mean : gq.gold_earned;
    cells.push(metricCell('累计金币', fmtInt(v)));
  }
  if (gq.exp_earned != null) {
    const v = typeof gq.exp_earned === 'object' ? gq.exp_earned.mean : gq.exp_earned;
    cells.push(metricCell('累计经验', fmtInt(v)));
  }
  if (gq.dismissals != null) {
    cells.push(metricCell('遣散', fmtInt(gq.dismissals)));
  }
  if (!cells.length) return '';
  return `
    <div class="metric-group">
      <div class="metric-group-title">经营表现</div>
      <dl class="metric-group-cells">${cells.join('')}</dl>
    </div>`;
}

// 聚合指标 → 按类别分组：战力 / 时间 / 配置
function renderAggregateList(details) {
  const groups = { combat: [], timing: [], config: [] };
  for (const d of details) {
    if (d.label.startsWith('Rank Score')) groups.combat.push(d);
    else if (d.label === '最近运行') groups.timing.push(d);
    else groups.config.push(d);
  }
  return `
    <div class="aggregate-list">
      ${renderAggregateGroup('combat', '战力', groups.combat, d => d.label === 'Rank Score · 最佳')}
      ${renderAggregateGroup('timing', '时间', groups.timing)}
      ${renderAggregateGroup('config', '配置', groups.config)}
    </div>
  `;
}

function renderAggregateGroup(tone, name, rows, isPrimary) {
  if (!rows.length) return '';
  const rowsHtml = rows.map((r) => {
    const cls = isPrimary && isPrimary(r) ? ' aggregate-row primary' : ' aggregate-row';
    return `
      <div class="${cls}">
        <span class="aggregate-label">${esc(r.label)}</span>
        <span class="aggregate-value" title="${esc(r.value)}">${esc(r.value)}</span>
      </div>
    `;
  }).join('');
  return `
    <div class="aggregate-group tone-${tone}">
      <div class="aggregate-group-head">
        <span class="aggregate-group-name">${esc(name)}</span>
        <span class="aggregate-group-count">${rows.length}</span>
      </div>
      <div class="aggregate-rows">${rowsHtml}</div>
    </div>
  `;
}

function metricCell(label, value, opts = {}) {
  const cls = opts.cls ? ` ${opts.cls}` : '';
  return `
    <div class="metric-cell${cls}">
      <dt class="metric-cell-label">${esc(label)}</dt>
      <dd class="metric-cell-value">${value}</dd>
    </div>`;
}

function renderRunDetails(runs) {
  if (!runs.length) return '';
  const cards = runs.map((run, idx) => renderRunCard(run, idx + 1, runs.length)).join('');
  return `
    <div class="detail-section run-section">
      <div class="detail-title">
        <span>运行明细</span>
        <span class="detail-count">${runs.length} 次</span>
      </div>
      <div class="run-list">${cards}</div>
    </div>`;
}

function renderRunCard(run, num, total) {
  const tu = run.token_usage || {};
  const timing = run.timing || {};
  const tc = run.tool_calls || {};
  const ga = run.game_actions || {};
  const strongestEnemy = ga.strongest_defeated_enemy || {};
  const defeatedText = strongestEnemy.name
    ? `${strongestEnemy.name}（强度 ${fmtInt(strongestEnemy.power) || '—'}）`
    : null;
  const partyText = run.party_size != null
    ? `${run.party_size}/${run.party_size_limit ?? '—'}`
    : '—';

  // 战力（只剩 Rank Score,主指标独占）
  const combatStats = [
    runStat('Rank Score', fmtRankScore(run.rank_score), true),
  ];

  // 效率（按可用性展示）
  const effStats = [];
  if (tu.input_tokens) effStats.push(runStat('Input', fmtInt(tu.input_tokens)));
  if (tu.output_tokens) effStats.push(runStat('Output', fmtInt(tu.output_tokens)));
  if (timing.total_seconds) effStats.push(runStat('耗时', fmtDuration(timing.total_seconds)));
  if (tc.total) effStats.push(runStat('操作数', fmtInt(tc.total)));

  // 战果
  const resultStats = [
    runStat('队伍', partyText),
    runStat('回合', `${run.turns ?? '—'}/${run.max_turns ?? '—'}`),
  ];
  if (ga.battles_won != null && ga.battles_total) {
    resultStats.push(runStat('战斗', `${ga.battles_won}/${ga.battles_total}`));
  }
  if (ga.total_gold_earned != null) resultStats.push(runStat('金币', fmtInt(ga.total_gold_earned)));
  if (ga.total_experience_earned != null) resultStats.push(runStat('EXP', fmtInt(ga.total_experience_earned)));
  if (defeatedText) resultStats.push(runStat('击败 Boss', defeatedText));
  const upgrades = Array.isArray(run.upgrades) ? run.upgrades : [];
  if (upgrades.length) resultStats.push(runStat('升级', `${upgrades.length} 项`));

  // Footer tags
  const tags = [];
  if (run.preset) tags.push(run.preset);
  if (run.game_seed != null) tags.push(`game ${fmtInt(run.game_seed)}`);
  if (run.scoring_seed != null) tags.push(`score ${fmtInt(run.scoring_seed)}`);
  if (run.score_mode) tags.push(run.score_mode);
  if (run.rank_score_source) tags.push(`rank ${run.rank_score_source}`);

  const toolBreakdown = renderToolBreakdown(tc);
  const contributors = renderRankContributors(run.rank_score_per_adventurer);
  const adventurerResults = renderAdventurerResults(ga.adventurer_stats);
  const upgradeList = renderRunUpgrades(upgrades);

  const numLabel = total > 1 ? `Run ${num}/${total}` : 'Run';

  return `
    <div class="run-card">
      <div class="run-card-header">
        <span class="run-num">${esc(numLabel)}</span>
        <span class="run-time">${esc(fmtTimestamp(run.created_at))}</span>
        <span class="run-id" title="${esc(run.run_id || run.session_id || '')}">${esc(shortRunId(run.run_id || run.session_id || ''))}</span>
      </div>
      <div class="run-card-body">
        <div class="run-group tone-combat">
          <div class="run-group-name">战力</div>
          <div class="run-stats">${combatStats.join('')}</div>
        </div>
        ${effStats.length ? `
        <div class="run-group tone-eff">
          <div class="run-group-name">效率</div>
          <div class="run-stats">${effStats.join('')}</div>
        </div>` : ''}
        ${resultStats.length ? `
        <div class="run-group tone-result">
          <div class="run-group-name">战果</div>
          <div class="run-stats">${resultStats.join('')}</div>
        </div>` : ''}
      </div>
      ${upgradeList ? `
      <div class="run-card-section">
        <div class="run-section-title">已购买升级</div>
        ${upgradeList}
      </div>` : ''}
      ${toolBreakdown ? `
      <div class="run-card-section">
        <div class="run-section-title">工具调用</div>
        ${toolBreakdown}
      </div>` : ''}
      ${contributors ? `
      <div class="run-card-section">
        <div class="run-section-title">Rank 贡献者</div>
        ${contributors}
      </div>` : ''}
      ${adventurerResults ? `
      <div class="run-card-section">
        <div class="run-section-title">冒险者累计战绩</div>
        ${adventurerResults}
      </div>` : ''}
      ${tags.length ? `
      <div class="run-card-footer">
        ${tags.map((t) => `<span class="run-tag">${esc(t)}</span>`).join('')}
      </div>` : ''}
    </div>
  `;
}

// runStat: label + value, optional primary highlight
function runStat(label, value, primary = false) {
  const display = value == null || value === '' ? '—' : String(value);
  const cls = primary ? ' run-stat primary' : ' run-stat';
  return `
    <div class="${cls}">
      <div class="run-stat-label">${esc(label)}</div>
      <div class="run-stat-value" title="${esc(display)}">${esc(display)}</div>
    </div>
  `;
}

function renderRunUpgrades(upgrades) {
  if (!Array.isArray(upgrades) || !upgrades.length) return '';
  return `
    <div class="upgrade-list">
      ${upgrades.map((upgrade) => {
        const name = upgrade.name || upgrade.upgrade_id || '未知升级';
        const description = upgrade.description || '';
        const cost = upgrade.gold_cost != null ? `${fmtInt(upgrade.gold_cost)} 金币` : '';
        return `
          <div class="upgrade-item" title="${esc(description)}">
            <div class="upgrade-item-head">
              <span class="upgrade-item-name">${esc(name)}</span>
              ${cost ? `<span class="upgrade-item-cost">${esc(cost)}</span>` : ''}
            </div>
            <div class="upgrade-item-effect">${esc(upgradeEffectText(upgrade))}</div>
          </div>`;
      }).join('')}
    </div>`;
}

function upgradeEffectText(upgrade) {
  const parts = [];
  const stats = statModifierText(upgrade.stats);
  if (stats !== '无属性加成') parts.push(stats);
  if (Number(upgrade.party_size_bonus) > 0) {
    parts.push(`队伍上限 +${upgrade.party_size_bonus}`);
  }
  const skills = Array.isArray(upgrade.skills) ? upgrade.skills : [];
  const skillNames = skills
    .map((skill) => skill?.name || skill?.skill_id)
    .filter(Boolean);
  if (skillNames.length) parts.push(`技能：${skillNames.join('、')}`);
  return parts.join(' · ') || upgrade.description || '已解锁';
}

function toolBreakdownItems(toolCalls) {
  const detail = toolCalls?.by_name_detail && Object.keys(toolCalls.by_name_detail).length
    ? toolCalls.by_name_detail
    : null;
  const items = detail
    ? Object.entries(detail).map(([name, counts]) => ({
        name,
        total: counts.total || 0,
        failed: counts.failed || 0,
      }))
    : Object.entries(toolCalls?.by_name || {}).map(([name, total]) => ({
        name,
        total: Number(total) || 0,
        failed: 0,
      }));
  return items
    .filter((item) => item.total > 0)
    .sort((a, b) => b.total - a.total || a.name.localeCompare(b.name));
}

function renderToolBreakdown(toolCalls) {
  const items = toolBreakdownItems(toolCalls);
  if (!items.length) return '';
  return `
    <div class="tool-chips">
      ${items.slice(0, 12).map((item) => `
        <span class="tool-chip" title="${esc(item.name)}">
          <span class="tool-chip-name">${esc(toolLabel(item.name))}</span>
          <span class="tool-chip-count">${esc(fmtInt(item.total))}</span>
          ${item.failed ? `<span class="tool-chip-fail">失败 ${esc(fmtInt(item.failed))}</span>` : ''}
        </span>`).join('')}
    </div>`;
}

function rankContributorItems(values) {
  if (!Array.isArray(values)) return [];
  return values
    .filter((item) => item && typeof item === 'object')
    .map((item) => {
      const score = item.rank_score_contribution ?? item.rank_score;
      return {
        name: item.name || item.adventurer_id || '?',
        score: Number(score),
        share: item.rank_score_share != null ? Number(item.rank_score_share) : null,
        adventurer: item.adventurer && typeof item.adventurer === 'object'
          ? item.adventurer
          : null,
      };
    })
    .filter((item) => Number.isFinite(item.score))
    .sort((a, b) => b.score - a.score);
}

function renderRankContributors(values) {
  const items = rankContributorItems(values);
  if (!items.length) return '';
  return `
    <div class="contrib-chips">
      ${items.map((item) => {
        const share = item.share != null ? `<span class="contrib-chip-share">${esc(fmtPct(item.share))}</span>` : '';
        const chipTitle = item.adventurer ? '' : ` title="${esc(item.name)} · ${esc(fmtRankScore(item.score))}"`;
        let power = `<span class="contrib-chip-score">${esc(fmtRankScore(item.score))}</span>`;
        if (item.adventurer) {
          const tooltipKey = `adventurer-${++_adventurerTooltipSeq}`;
          _adventurerTooltipDetails.set(tooltipKey, item.adventurer);
          power = `
            <button
              type="button"
              class="contrib-chip-power"
              data-adventurer-tooltip="${tooltipKey}"
              aria-label="查看 ${esc(item.name)} 的详细属性"
            >${esc(fmtRankScore(item.score))}</button>`;
        }
        return `
          <span class="contrib-chip"${chipTitle}>
            <span class="contrib-chip-name">${esc(item.name)}</span>
            ${power}
            ${share}
          </span>`;
      }).join('')}
    </div>`;
}

function renderAdventurerResults(values) {
  if (!Array.isArray(values) || !values.length) return '';
  const items = values
    .filter((item) => item && typeof item === 'object')
    .sort((a, b) => Number(b.cumulative_battles_total || 0) - Number(a.cumulative_battles_total || 0));
  if (!items.length) return '';
  return `
    <div class="adventurer-result-grid">
      ${items.map((item) => `
        <div class="adventurer-result">
          <strong>${esc(item.adventurer_name || item.adventurer_id || '?')}</strong>
          <span>${esc(fmtInt(item.cumulative_battles_won || 0))} 胜 / ${esc(fmtInt(item.cumulative_battles_lost || 0))} 负</span>
          <em>金币 ${esc(fmtInt(item.cumulative_gold_earned || 0))} · EXP ${esc(fmtInt(item.cumulative_experience_earned || 0))}</em>
        </div>
      `).join('')}
    </div>`;
}

// 冒险者 tooltip 的事件绑定已上移为 #cardListContainer 容器级委托（initCardInteractions）

function ensureAdventurerTooltip() {
  let tooltip = $('#adventurerTooltip');
  if (tooltip) return tooltip;
  tooltip = document.createElement('div');
  tooltip.id = 'adventurerTooltip';
  tooltip.className = 'adventurer-tooltip';
  tooltip.setAttribute('role', 'tooltip');
  tooltip.hidden = true;
  document.body.appendChild(tooltip);
  return tooltip;
}

function showAdventurerTooltip(anchor) {
  cancelAdventurerTooltipHide();
  const detail = _adventurerTooltipDetails.get(anchor.dataset.adventurerTooltip);
  if (!detail) return;
  const tooltip = ensureAdventurerTooltip();
  tooltip.innerHTML = renderAdventurerTooltip(detail);
  tooltip.dataset.anchor = anchor.dataset.adventurerTooltip;
  tooltip.hidden = false;
  anchor.setAttribute('aria-describedby', tooltip.id);
  positionAdventurerTooltip(anchor, tooltip);
}

function scheduleAdventurerTooltipHide() {
  cancelAdventurerTooltipHide();
  _adventurerTooltipHideTimer = window.setTimeout(hideAdventurerTooltip, 120);
}

function cancelAdventurerTooltipHide() {
  if (_adventurerTooltipHideTimer != null) {
    window.clearTimeout(_adventurerTooltipHideTimer);
    _adventurerTooltipHideTimer = null;
  }
}

function hideAdventurerTooltip() {
  cancelAdventurerTooltipHide();
  const tooltip = $('#adventurerTooltip');
  if (!tooltip || tooltip.hidden) return;
  const anchorKey = tooltip.dataset.anchor;
  if (anchorKey) {
    document.querySelector(`[data-adventurer-tooltip="${anchorKey}"]`)
      ?.removeAttribute('aria-describedby');
  }
  tooltip.hidden = true;
  tooltip.dataset.anchor = '';
}

function repositionOpenAdventurerTooltip() {
  const tooltip = $('#adventurerTooltip');
  if (!tooltip || tooltip.hidden || !tooltip.dataset.anchor) return;
  const anchor = document.querySelector(
    `[data-adventurer-tooltip="${tooltip.dataset.anchor}"]`
  );
  if (anchor) positionAdventurerTooltip(anchor, tooltip);
}

function positionAdventurerTooltip(anchor, tooltip) {
  const rect = anchor.getBoundingClientRect();
  const gap = 10;
  const margin = 12;
  if (rect.bottom < 0 || rect.top > window.innerHeight) {
    hideAdventurerTooltip();
    return;
  }
  tooltip.style.visibility = 'hidden';
  tooltip.style.left = `${margin}px`;
  tooltip.style.top = `${margin}px`;

  const width = tooltip.offsetWidth;
  const height = tooltip.offsetHeight;
  let left = rect.left + rect.width / 2 - width / 2;
  left = Math.max(margin, Math.min(left, window.innerWidth - width - margin));

  let top = rect.bottom + gap;
  if (top + height > window.innerHeight - margin) {
    top = Math.max(margin, rect.top - height - gap);
  }
  top = Math.max(margin, Math.min(top, window.innerHeight - height - margin));

  tooltip.style.left = `${left}px`;
  tooltip.style.top = `${top}px`;
  tooltip.style.visibility = 'visible';
}

function renderAdventurerTooltip(adventurer) {
  const stats = adventurer.effective_stats || {};
  const baseStats = adventurer.base_stats || {};
  const resources = adventurer.resources || {};
  const equipment = (adventurer.equipment_slots || []).filter((slot) => slot?.item);
  const skills = Array.isArray(adventurer.skills) ? adventurer.skills : [];
  const hpText = `${fmtInt(resources.current_hp) || '—'} / ${fmtInt(stats.hp) || '—'}`;
  const mpText = `${fmtInt(resources.current_mp) || '—'} / ${fmtInt(stats.mp) || '—'}`;

  return `
    <div class="adventurer-tooltip-head">
      <div>
        <strong>${esc(adventurer.name || adventurer.adventurer_id || '未知冒险者')}</strong>
        <span>Lv.${esc(String(adventurer.level ?? '—'))} · ${esc(adventurer.template_id || '未知职业')}</span>
      </div>
      <div class="adventurer-tooltip-resources">
        <span>HP ${esc(hpText)}</span>
        <span>MP ${esc(mpText)}</span>
      </div>
    </div>
    <section class="adventurer-tooltip-section">
      <h4>最终属性 <span>括号内为基础值</span></h4>
      <div class="adventurer-tooltip-stats">
        ${['hp', 'mp', 'attack', 'defense', 'speed', 'recovery', 'mp_recovery']
          .map((key) => renderAdventurerStat(key, stats[key], baseStats[key]))
          .join('')}
      </div>
    </section>
    <section class="adventurer-tooltip-section">
      <h4>装备 <span>${equipment.length} 件</span></h4>
      <div class="adventurer-tooltip-list">
        ${equipment.length
          ? equipment.map((slot) => renderAdventurerEquipment(slot)).join('')
          : '<div class="adventurer-tooltip-empty">无装备</div>'}
      </div>
    </section>
    <section class="adventurer-tooltip-section">
      <h4>技能 <span>${skills.length} 个</span></h4>
      <div class="adventurer-tooltip-list">
        ${skills.length
          ? skills.map((skill) => renderAdventurerSkill(skill)).join('')
          : '<div class="adventurer-tooltip-empty">无技能</div>'}
      </div>
    </section>`;
}

function renderAdventurerStat(key, effective, base) {
  const effectiveText = effective == null ? '—' : fmtInt(effective);
  const baseText = base == null ? '—' : fmtInt(base);
  return `
    <div class="adventurer-tooltip-stat">
      <span>${esc(statLabel(key))}</span>
      <strong>${esc(effectiveText)}</strong>
      <em>(${esc(baseText)})</em>
    </div>`;
}

function renderAdventurerEquipment(slot) {
  const item = slot.item || {};
  const skills = Array.isArray(item.skills) ? item.skills : [];
  return `
    <div class="adventurer-tooltip-item">
      <div class="adventurer-tooltip-item-head">
        <span>${esc(slotLabel(slot.slot))}</span>
        <strong>${esc(item.name || item.template_id || '未知装备')}</strong>
      </div>
      <p>${esc(statModifierText(item.stats))}</p>
      ${skills.map((skill) => `
        <div class="adventurer-tooltip-item-skill">
          <strong>装备技能 · ${esc(skill.name || skill.skill_id || '未知技能')}</strong>
          <span>${esc(skillDetailText(skill))}</span>
        </div>`).join('')}
    </div>`;
}

function renderAdventurerSkill(skill) {
  return `
    <div class="adventurer-tooltip-skill">
      <div>
        <strong>${esc(skill.name || skill.skill_id || '未知技能')}</strong>
        <span>${skill.kind === 'active' ? '主动' : '被动'}</span>
      </div>
      <p>${esc(skillDetailText(skill))}</p>
    </div>`;
}

function skillDetailText(skill) {
  const parts = [];
  if (skill.mp_cost > 0) parts.push(`消耗 ${skill.mp_cost} MP`);
  if (skill.free) parts.push('即时（附赠普攻）');
  if (skill.once_per_battle) parts.push('每场限一次');
  const condition = skillConditionText(skill.condition);
  if (condition) parts.push(`条件：${condition}`);
  const effects = Array.isArray(skill.effects) ? skill.effects : [];
  effects.map(skillEffectText).filter(Boolean).forEach((text) => parts.push(text));
  return parts.join('，') || '无额外说明';
}

function skillConditionText(condition) {
  if (!condition || condition.type === 'always') return '';
  const pct = condition.value == null ? null : `${Math.round(condition.value * 100)}%`;
  const labels = {
    self_hp_pct_lte: `自身HP ≤ ${pct}`,
    self_hp_pct_gte: `自身HP ≥ ${pct}`,
    target_hp_pct_lte: `目标HP ≤ ${pct}`,
    target_hp_pct_gte: `目标HP ≥ ${pct}`,
    self_mp_pct_lte: `自身MP ≤ ${pct}`,
    self_mp_pct_gte: `自身MP ≥ ${pct}`,
    target_mp_pct_lte: `目标MP ≤ ${pct}`,
    target_mp_pct_gte: `目标MP ≥ ${pct}`,
    action_index_lte: `行动序号 ≤ ${condition.value}`,
    action_index_gte: `行动序号 ≥ ${condition.value}`,
  };
  if (labels[condition.type]) return labels[condition.type];
  const nested = Array.isArray(condition.conditions) ? condition.conditions : [];
  if (condition.type === 'all') return nested.map(skillConditionText).filter(Boolean).join(' 且 ');
  if (condition.type === 'any') return nested.map(skillConditionText).filter(Boolean).join(' 或 ');
  return condition.type || '';
}

function skillEffectText(effect) {
  if (!effect || typeof effect !== 'object') return '';
  if (effect.type === 'damage_multiplier') return `伤害 ×${effect.value}`;
  if (effect.type === 'damage_bonus') return `伤害 +${effect.value}`;
  if (effect.type === 'true_damage') return `真实伤害 ${effect.value}`;
  if (effect.type === 'self_damage') return `自身受伤 ${effect.value}`;
  if (effect.type === 'heal') return `治疗 ${effect.value} HP`;
  if (effect.type === 'heal_percent') return `治疗 ${Math.round(effect.value * 100)}% 最大HP`;
  if (effect.type === 'mp_restore') return `${effect.target === 'self' ? '自身' : '目标'}恢复 ${effect.value} MP`;
  if (effect.type === 'stat_bonus') return `${statLabel(effect.stat)} ${signedNumber(effect.value)}`;
  if (effect.type === 'stat_multiplier') return `${statLabel(effect.stat)} ×${effect.value}`;
  if (effect.type === 'apply_status' && effect.status) {
    const status = effect.status;
    const duration = status.duration ? ` ${status.duration}回合` : '';
    const effects = (status.effects || []).map(skillEffectText).filter(Boolean).join('，');
    return `施加状态 ${status.name || ''}${duration}${effects ? `（${effects}）` : ''}`;
  }
  return `${effect.type || '效果'}${effect.value != null ? ` ${effect.value}` : ''}`;
}

function statModifierText(stats) {
  const values = Object.entries(stats || {}).filter(([, value]) => Number(value) !== 0);
  return values.length
    ? values.map(([key, value]) => `${statLabel(key)} ${signedNumber(value)}`).join(' · ')
    : '无属性加成';
}

function signedNumber(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return String(value);
  return number > 0 ? `+${number}` : String(number);
}

function statLabel(key) {
  return {
    hp: 'HP',
    mp: 'MP',
    attack: '攻击',
    defense: '防御',
    speed: '速度',
    recovery: '回血',
    mp_recovery: '回魔',
  }[key] || key || '属性';
}

function slotLabel(slot) {
  return {
    main_hand: '右手',
    off_hand: '左手',
    two_hand: '双手',
    hand: '单手',
    boots: '鞋子',
    helmet: '头盔',
    armor: '护甲',
    accessory: '饰品',
  }[slot] || slot || '装备';
}

// ============================================================================
// Error Display
// ============================================================================
function showError(msg) {
  $('#loadingState')?.remove();
  $('#leaderboardMain').innerHTML = `
    <div class="error-state">
      <p>${esc(msg)}</p>
    </div>`;
}

// ============================================================================
// Init
// ============================================================================
async function init() {
  try {
    const [res, notesRes] = await Promise.all([
      fetch('leaderboard_data.json', { cache: 'no-cache' }),
      fetch('model_notes.json', { cache: 'no-cache' }),
    ]);
    if (!res.ok) throw new Error(`HTTP ${res.status}: ${res.statusText}`);
    const data = await res.json();
    _leaderboardData = data;
    if (notesRes.ok) {
      try { _modelNotes = await notesRes.json(); } catch (_) { /* ignore malformed notes */ }
    }
    $('#loadingState')?.remove();
    restoreView();
    initLeaderboardSearch();
    initCardInteractions();
    renderLeaderboard(data);
    initTabs();
    initRankScoreHelp();
  } catch (e) {
    showError(`无法加载 leaderboard_data.json: ${e.message}`);
  }
}

function initRankScoreHelp() {
  const dialog = $('#rsPopover');
  document.addEventListener('click', e => {
    if (e.target.closest('[data-action="show-rs-help"]')) dialog.showModal();
  });
  dialog.querySelector('.rs-popover-close').addEventListener('click', () => dialog.close());
  dialog.addEventListener('click', e => {
    const r = dialog.getBoundingClientRect();
    if (e.target === dialog && (e.clientX < r.left || e.clientX > r.right || e.clientY < r.top || e.clientY > r.bottom)) dialog.close();
  });
}

document.addEventListener('DOMContentLoaded', init);
