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

function esc(str) {
  const d = document.createElement('div');
  d.textContent = str;
  return d.innerHTML;
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
  if (seconds < 60) return `${Math.round(seconds)}s`;
  const m = Math.floor(seconds / 60);
  const s = Math.round(seconds % 60);
  return `${m}m${s}s`;
}

function fmtTokens(n) {
  if (n == null) return null;
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
let _curveChart = null;
let _curveRunSelection = {}; // key: "model::run_id" -> boolean
let _curveMetric = 'rank_score';
let _adventurerTooltipSeq = 0;
let _adventurerTooltipHideTimer = null;
let _leaderboardSearchQuery = '';
const _adventurerTooltipDetails = new Map();

// Palette for curve lines — distinct colors readable on dark background
const CURVE_COLORS = [
  '#f59e0b', // amber (accent)
  '#22d3ee', // cyan
  '#a78bfa', // purple
  '#22c55e', // green
  '#f87171', // red
  '#60a5fa', // blue
  '#fb923c', // orange
  '#e879f9', // fuchsia
  '#34d399', // emerald
  '#fbbf24', // yellow
];

function curveColor(index) {
  return CURVE_COLORS[index % CURVE_COLORS.length];
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
function initTabs() {
  const tabs = $$('.tab-btn');
  tabs.forEach((btn) => {
    btn.addEventListener('click', () => {
      tabs.forEach((t) => t.classList.remove('active'));
      btn.classList.add('active');
      const tab = btn.dataset.tab;
      $('#leaderboardMain').style.display = tab === 'leaderboard' ? '' : 'none';
      $('#curvesMain').style.display = tab === 'curves' ? '' : 'none';
      if (tab === 'curves' && _leaderboardData) {
        renderCurvePanel();
      }
    });
  });

  // 支持 ?tab=curves 直接进入曲线对比（便于截图/分享）
  const params = new URLSearchParams(window.location.search);
  if (params.get('tab') === 'curves') {
    const btn = $('#tabCurves');
    if (btn) btn.click();
  }
  // ?expand=N 自动展开第 N 张卡（开发用）
  const expandIdx = parseInt(params.get('expand') || '', 10);
  if (!Number.isNaN(expandIdx) && expandIdx > 0) {
    setTimeout(() => {
      const card = $$('.model-card')[expandIdx - 1];
      if (card) card.classList.add('expanded');
    }, 100);
  }
}

// ============================================================================
// Curve Comparison
// ============================================================================

/**
 * Collect all runs across all models that have the selected metric curve.
 * Returns [{model, run, key, colorIndex}]
 */
function allCurveRuns(data) {
  const runs = [];
  let idx = 0;
  const metric = currentCurveMetric();
  for (const m of data.models) {
    for (const run of (m.run_details || [])) {
      const curve = metric.curve(run).filter((pt) => metric.value(pt) != null);
      if (curve.length > 0) {
        runs.push({
          model: m.model,
          run,
          key: `${m.model}::${run.run_id || run.session_id}`,
          colorIndex: idx++,
          curve,
        });
      }
    }
  }
  return runs;
}

function renderCurvePanel() {
  const data = _leaderboardData;
  if (!data) return;

  const metricSelect = $('#curveMetricSelect');
  if (metricSelect) {
    metricSelect.value = _curveMetric;
    metricSelect.onchange = () => {
      _curveMetric = metricSelect.value;
      renderCurvePanel();
    };
  }

  const runs = allCurveRuns(data);
  const legend = $('#curveLegend');
  const metric = currentCurveMetric();

  if (!runs.length) {
    legend.innerHTML = `<div class="curve-legend-placeholder">${esc(metric.empty)}</div>`;
    if (_curveChart) { _curveChart.destroy(); _curveChart = null; }
    return;
  }

  // Default: select all runs on first render
  if (Object.keys(_curveRunSelection).length === 0) {
    runs.forEach((r) => { _curveRunSelection[r.key] = true; });
  } else {
    runs.forEach((r) => {
      if (!Object.prototype.hasOwnProperty.call(_curveRunSelection, r.key)) {
        _curveRunSelection[r.key] = true;
      }
    });
  }

  // Build legend items
  const items = runs.map((r) => {
    const checked = _curveRunSelection[r.key] ? 'checked' : '';
    const color = curveColor(r.colorIndex);
    const rs = r.run.rank_score;
    const rsLabel = rs != null ? fmtRankScore(rs) : '—';
    const timeLabel = fmtTimestamp(r.run.created_at);
    const finalValue = r.curve.length ? metric.value(r.curve[r.curve.length - 1]) : null;
    const metricLabel = finalValue != null ? fmtInt(finalValue) : rsLabel;
    // 短时间格式 + 短日期，给模型名让位
    const shortTime = (() => {
      const m = timeLabel.match(/^(\d{4})-(\d{2})-(\d{2}) (\d{2}):(\d{2})/);
      return m ? `${m[2]}-${m[3]} ${m[4]}:${m[5]}` : timeLabel;
    })();
    return `
      <label class="curve-legend-item">
        <input type="checkbox" data-curve-key="${esc(r.key)}" ${checked} />
        <span class="curve-color-dot" style="background:${color}"></span>
        <div class="curve-legend-content">
          <span class="curve-legend-model">${esc(r.model)}</span>
          <span class="curve-legend-meta">${esc(shortTime)} · ${esc(metricLabel)}</span>
        </div>
      </label>`;
  }).join('');

  legend.innerHTML = items;

  // Bind checkbox events
  legend.querySelectorAll('input[type=checkbox]').forEach((cb) => {
    cb.addEventListener('change', () => {
      _curveRunSelection[cb.dataset.curveKey] = cb.checked;
      updateCurveChart(data);
    });
  });

  // Bind select all / deselect all
  $('#curveSelectAll').onclick = () => {
    runs.forEach((r) => { _curveRunSelection[r.key] = true; });
    legend.querySelectorAll('input[type=checkbox]').forEach((cb) => { cb.checked = true; });
    updateCurveChart(data);
  };
  $('#curveDeselectAll').onclick = () => {
    runs.forEach((r) => { _curveRunSelection[r.key] = false; });
    legend.querySelectorAll('input[type=checkbox]').forEach((cb) => { cb.checked = false; });
    updateCurveChart(data);
  };

  updateCurveChart(data);
}

function updateCurveChart(data) {
  const runs = allCurveRuns(data);
  const selected = runs.filter((r) => _curveRunSelection[r.key]);

  if (!selected.length) {
    if (_curveChart) {
      _curveChart.data.labels = [];
      _curveChart.data.datasets = [];
      _curveChart.update();
    }
    return;
  }

  const metric = currentCurveMetric();

  // Build datasets — curve is already filtered to points for the selected metric.
  const datasets = selected.map((r) => {
    const color = curveColor(r.colorIndex);
    const points = r.curve.map((pt) => ({ x: pt.turn, y: metric.value(pt) }));
    return {
      label: r.model,
      data: points,
      borderColor: color,
      backgroundColor: color + '18',
      pointBackgroundColor: color,
      pointRadius: 3,
      pointHoverRadius: 5,
      borderWidth: 2,
      tension: 0.15,
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
        showLine: true,
        interaction: {
          mode: 'nearest',
          intersect: false,
        },
        plugins: {
          legend: { display: false },
          tooltip: {
            backgroundColor: 'rgba(13, 17, 32, 0.95)',
            titleColor: '#e8ecf4',
            bodyColor: '#9aa3be',
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
              color: '#5c6585',
              font: { family: 'Inter', size: 12 },
            },
            ticks: { color: '#5c6585', font: { family: 'JetBrains Mono', size: 11 }, stepSize: 5 },
            grid: { color: 'rgba(255,255,255,0.04)' },
          },
          y: {
            title: {
              display: true,
              text: metric.label,
              color: '#5c6585',
              font: { family: 'Inter', size: 12 },
            },
            ticks: {
              color: '#5c6585',
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
  const meta = $('#topMeta');
  const container = $('#cardListContainer');

  // Top bar meta
  const genTime = data.generated_at ? data.generated_at.replace('T', ' ') : '—';
  meta.innerHTML = `<span>${esc(data.total_runs)} 次运行 · ${data.models.length} 个模型 · 更新于 ${esc(genTime)}</span>`;

  if (!data.models.length) {
    container.innerHTML = `
      <div class="empty-state">
        <h2>暂无数据</h2>
        <p>暂无排行榜数据，请稍后再来查看。</p>
      </div>`;
    updateLeaderboardSearchState(0, 0);
    return;
  }

  // Stats banner metrics
  renderStatsBanner(data);

  const searchInput = $('#leaderboardSearchInput');
  if (searchInput) searchInput.disabled = false;

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
  const clear = $('#leaderboardSearchClear');
  const status = $('#leaderboardSearchStatus');
  const hasQuery = Boolean(_leaderboardSearchQuery);

  if (input && input.value !== _leaderboardSearchQuery) {
    input.value = _leaderboardSearchQuery;
  }
  if (clear) clear.hidden = !hasQuery;
  if (status) {
    status.textContent = hasQuery
      ? `找到 ${visibleCount} / ${totalCount} 个模型`
      : `共 ${totalCount} 个模型`;
  }
}

function renderLeaderboardCards(data) {
  const container = $('#cardListContainer');
  const models = data.models || [];
  const normalizedQuery = normalizeSearchText(_leaderboardSearchQuery.trim());
  const visibleModels = models.filter((model) => modelMatchesSearch(model, normalizedQuery));

  _adventurerTooltipDetails.clear();
  _adventurerTooltipSeq = 0;
  updateLeaderboardSearchState(visibleModels.length, models.length);

  if (!visibleModels.length) {
    container.innerHTML = `
      <div class="empty-state search-empty-state">
        <h2>未找到匹配模型</h2>
        <p>请尝试其他模型名称或备注关键词。</p>
        <button class="search-empty-clear" type="button" data-action="clear-leaderboard-search">清除搜索</button>
      </div>`;
    return;
  }

  // 预计算多口径徽标（避免每个 card 内重算）
  const allBadges = computeModelBadges(models);

  // Build cards with staggered animation delay
  const topScore = models
    .map((m) => (m.rank_score && m.rank_score.mean) || 0)
    .reduce((a, b) => Math.max(a, b), 0);
  const avgScore = models
    .map((m) => (m.rank_score && m.rank_score.mean) || 0)
    .filter((v) => v > 0);
  const avgVal = avgScore.length
    ? avgScore.reduce((a, b) => a + b, 0) / avgScore.length
    : null;
  const total = models.length;

  const cards = visibleModels.map((m, i) => {
    const badges = allBadges.get(m.model) || [];
    const html = renderCard(m, { topScore, avgVal, total, badges });
    return html.replace(
      'class="model-card',
      `style="animation-delay:${i * 80}ms" class="model-card`
    );
  }).join('');

  container.innerHTML = `<div class="card-list">${cards}</div>`;
  bindAdventurerTooltips();

  // Bind expand toggle
  $$('.model-card').forEach((card) => {
    card.addEventListener('click', (e) => {
      // Don't toggle if clicking on a link or button inside
      if (e.target.closest('a, button')) return;
      card.classList.toggle('expanded');
    });
  });
}

function clearLeaderboardSearch({ focus = true } = {}) {
  if (!_leaderboardSearchQuery && !$('#leaderboardSearchInput')?.value) return;
  _leaderboardSearchQuery = '';
  const input = $('#leaderboardSearchInput');
  if (input) input.value = '';
  if (_leaderboardData) renderLeaderboardCards(_leaderboardData);
  if (focus) input?.focus();
}

function initLeaderboardSearch() {
  const input = $('#leaderboardSearchInput');
  const clear = $('#leaderboardSearchClear');
  if (!input || !clear) return;

  input.addEventListener('input', () => {
    _leaderboardSearchQuery = input.value.trim();
    if (_leaderboardData) renderLeaderboardCards(_leaderboardData);
  });

  input.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && input.value) {
      event.preventDefault();
      clearLeaderboardSearch();
    }
  });

  clear.addEventListener('click', () => clearLeaderboardSearch());
  $('#cardListContainer')?.addEventListener('click', (event) => {
    if (event.target.closest('[data-action="clear-leaderboard-search"]')) {
      clearLeaderboardSearch();
    }
  });
}

function renderStatsBanner(data) {
  const el = $('#statsBannerMetrics');
  if (!el) return;

  const models = data.models || [];
  const totalRuns = data.total_runs || 0;
  const validScores = models
    .map((m) => (m.rank_score && m.rank_score.best) || 0)
    .filter((v) => v > 0);

  const topScore = validScores.length ? Math.max(...validScores) : 0;
  const avgScore = validScores.length
    ? validScores.reduce((a, b) => a + b, 0) / validScores.length
    : 0;
  const lastUpdated = data.generated_at
    ? data.generated_at.slice(0, 10)  // YYYY-MM-DD
    : '—';

  // 计算 seed 信息
  const seeds = new Set();
  const scoringSeeds = new Set();
  for (const m of models) {
    for (const r of m.run_details || []) {
      if (r.game_seed != null) seeds.add(r.game_seed);
      if (r.scoring_seed != null) scoringSeeds.add(r.scoring_seed);
    }
  }
  const seedList = Array.from(seeds).sort((a, b) => a - b);
  const scoreSeedList = Array.from(scoringSeeds).sort((a, b) => a - b);
  const seedLabel = seedList.length === 1 ? `seed ${seedList[0]}` : '—';
  const scoreSeedLabel = scoreSeedList.length === 1 ? `score ${scoreSeedList[0]}` : '—';

  // 把指标分两档：primary (最高分 / 平均分) 和 secondary
  // primary 显示为大字号,secondary 显示为小字号 — 拉开权重
  const items = [
    { label: '最高分', value: fmtRankScore(topScore) || '—', unit: 'Rank Score', tier: 'primary' },
    { label: '平均分', value: fmtRankScore(Math.round(avgScore)) || '—', unit: 'Rank Score', tier: 'primary' },
    { label: '参赛模型', value: models.length, unit: '个', tier: 'secondary' },
    { label: '累计运行', value: totalRuns, unit: '次', tier: 'secondary' },
    { label: '游戏种子', value: seedLabel, unit: '复现', tier: 'secondary' },
    { label: '评分种子', value: scoreSeedLabel, unit: '复现', tier: 'secondary' },
  ];

  el.innerHTML = items
    .map(
      (it) => `
      <div class="stats-banner-cell tier-${it.tier}">
        <dt class="stats-banner-label">${esc(it.label)}</dt>
        <dd class="stats-banner-value">${esc(String(it.value))}</dd>
        <span class="stats-banner-unit">${esc(it.unit || '')}</span>
      </div>`
    )
    .join('');
}

function renderCard(m, ctx = {}) {
  const rank = m.rank;
  const rankCls = rank <= 3 ? ` rank-${rank}` : '';
  const runDetails = Array.isArray(m.run_details) ? m.run_details : [];
  const latestRun = runDetails[0] || {};
  const badges = ctx.badges || [];

  // Primary stat: rank_score
  const rs = m.rank_score;
  const rankScoreVal = rs ? esc(fmtRankScore(rs.mean)) : '—';

  // Efficiency stats
  const eff = m.efficiency || {};
  const hasEff = eff && (eff.input_tokens || eff.output_tokens || eff.duration_seconds || eff.tool_calls);

  // Game quality stats
  const gq = m.game_quality || {};
  const hasGq = gq && (gq.gold_earned || gq.exp_earned || gq.battle_win_rate != null || gq.dismissals != null);

  // Aggregate dismiss count across all runs
  let dismissTotal = 0;
  for (const run of runDetails) {
    const tc = run.tool_calls || {};
    const bnd = tc.by_name_detail || {};
    if (bnd.dismiss_adventurer) {
      dismissTotal += bnd.dismiss_adventurer.total || 0;
    } else {
      const bn = tc.by_name || {};
      dismissTotal += bn.dismiss_adventurer || 0;
    }
  }
  if (dismissTotal > 0) {
    gq.dismissals = dismissTotal;
  }

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
    <div class="model-card${rankCls}" data-rank="${rank}">
      <div class="card-header">
        <div class="rank-badge">${rank}</div>
        <div class="model-info">
          <div class="model-name" title="${esc(m.model)}">${esc(m.model)}${renderModelNote(m.model)}</div>
          ${renderBadges(badges)}
          <div class="model-meta">
            <span>${m.runs} 次运行</span>
            ${latestRun.preset ? `<span>${esc(latestRun.preset)}</span>` : ''}
            ${m.last_run ? `<span>${esc(fmtTimestamp(m.last_run))}</span>` : ''}
          </div>
        </div>
        <div class="card-stats">
          <div class="stat-block primary-stat">
            <div class="stat-label">Rank Score<button class="rs-help-btn" data-action="show-rs-help" title="Rank Score 释义">?</button></div>
            <div class="stat-value rank-val">${rankScoreVal}</div>
          </div>
        </div>
        <div class="expand-chevron" aria-label="展开详情">
          <svg width="14" height="14" viewBox="0 0 14 14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <path d="M3.5 5.25L7 8.75L10.5 5.25"/>
          </svg>
        </div>
      </div>

      ${(hasEff || hasGq) ? `
      <div class="metrics-row">
        ${hasEff ? renderEfficiencySection(eff) : ''}
        ${hasGq ? renderGameQualitySection(gq) : ''}
      </div>` : ''}

      <div class="card-detail-wrap">
        <div class="card-detail">
          <div class="detail-section">
            <div class="detail-title">
              <span>聚合指标</span>
              <span class="detail-count">${details.length} 项</span>
            </div>
            ${renderAggregateList(details)}
          </div>
          ${renderRunDetails(runDetails)}
        </div>
      </div>
    </div>`;
}

function renderEfficiencySection(eff) {
  const cells = [];
  if (eff.input_tokens != null) {
    const v = typeof eff.input_tokens === 'object' ? eff.input_tokens.mean : eff.input_tokens;
    cells.push(metricCell('Input', fmtInt(v) + ' tok'));
  }
  if (eff.output_tokens != null) {
    const v = typeof eff.output_tokens === 'object' ? eff.output_tokens.mean : eff.output_tokens;
    cells.push(metricCell('Output', fmtInt(v) + ' tok'));
  }
  if (eff.duration_seconds != null) {
    const v = typeof eff.duration_seconds === 'object' ? eff.duration_seconds.mean : eff.duration_seconds;
    cells.push(metricCell('Duration', fmtDuration(v)));
  }
  if (eff.tool_calls != null) {
    const v = typeof eff.tool_calls === 'object' ? eff.tool_calls.mean : eff.tool_calls;
    cells.push(metricCell('Tool Calls', fmtInt(v)));
  }
  if (!cells.length) return '';
  return `
    <div class="metric-group">
      <div class="metric-group-title">Efficiency</div>
      <div class="metric-group-cells">${cells.join('')}</div>
    </div>`;
}

function renderGameQualitySection(gq) {
  const cells = [];
  if (gq.battle_win_rate != null) {
    cells.push(metricCell('Battle Win', (gq.battle_win_rate * 100).toFixed(1) + '%'));
  }
  if (gq.gold_earned != null) {
    const v = typeof gq.gold_earned === 'object' ? gq.gold_earned.mean : gq.gold_earned;
    cells.push(metricCell('Gold', fmtInt(v)));
  }
  if (gq.exp_earned != null) {
    const v = typeof gq.exp_earned === 'object' ? gq.exp_earned.mean : gq.exp_earned;
    cells.push(metricCell('EXP', fmtInt(v)));
  }
  if (gq.dismissals != null) {
    cells.push(metricCell('遣散', fmtInt(gq.dismissals)));
  }
  if (!cells.length) return '';
  return `
    <div class="metric-group">
      <div class="metric-group-title">Game</div>
      <div class="metric-group-cells">${cells.join('')}</div>
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

function bindAdventurerTooltips() {
  const tooltip = ensureAdventurerTooltip();
  $$('[data-adventurer-tooltip]').forEach((anchor) => {
    anchor.addEventListener('mouseenter', () => showAdventurerTooltip(anchor));
    anchor.addEventListener('mouseleave', scheduleAdventurerTooltipHide);
    anchor.addEventListener('focus', () => showAdventurerTooltip(anchor));
    anchor.addEventListener('blur', scheduleAdventurerTooltipHide);
    anchor.addEventListener('keydown', (event) => {
      if (event.key === 'Escape') hideAdventurerTooltip();
    });
  });

  if (tooltip.dataset.bound === 'true') return;
  tooltip.dataset.bound = 'true';
  tooltip.addEventListener('mouseenter', cancelAdventurerTooltipHide);
  tooltip.addEventListener('mouseleave', scheduleAdventurerTooltipHide);
  window.addEventListener('resize', repositionOpenAdventurerTooltip);
  window.addEventListener('scroll', repositionOpenAdventurerTooltip, true);
}

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

function runMetric(label, value) {
  const display = value == null || value === '' ? '—' : String(value);
  return `
    <div class="run-metric">
      <span>${esc(label)}</span>
      <strong>${esc(display)}</strong>
    </div>`;
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
  initLeaderboardSearch();
  try {
    const [res, notesRes] = await Promise.all([
      fetch('leaderboard_data.json'),
      fetch('model_notes.json'),
    ]);
    if (!res.ok) throw new Error(`HTTP ${res.status}: ${res.statusText}`);
    const data = await res.json();
    _leaderboardData = data;
    if (notesRes.ok) {
      try { _modelNotes = await notesRes.json(); } catch (_) { /* ignore malformed notes */ }
    }
    $('#loadingState')?.remove();
    renderLeaderboard(data);
    initTabs();
    initIntroToggle();
    initRankScoreHelp();
  } catch (e) {
    showError(`无法加载 leaderboard_data.json: ${e.message}`);
  }
}

// ============================================================================
// Intro Card Toggle
// ============================================================================
function initIntroToggle() {
  const card = $('#introCard');
  const btn = $('#introToggle');
  if (!card || !btn) return;
  btn.addEventListener('click', () => {
    card.classList.toggle('collapsed');
    const collapsed = card.classList.contains('collapsed');
    btn.textContent = collapsed ? '展开' : '收起';
    btn.setAttribute('aria-expanded', String(!collapsed));
  });
}

// ============================================================================
// Rank Score Help Popover
// ============================================================================
function initRankScoreHelp() {
  const popover = $('#rsPopover');
  const backdrop = $('#rsBackdrop');

  function openPopover(anchor) {
    popover.setAttribute('aria-hidden', 'false');
    backdrop.setAttribute('aria-hidden', 'false');

    // Position above/below the anchor button, centered
    const rect = anchor.getBoundingClientRect();
    const popW = popover.offsetWidth;
    let left = rect.left + rect.width / 2 - popW / 2;
    left = Math.max(8, Math.min(left, window.innerWidth - popW - 8));
    popover.style.left = left + 'px';

    // Place below with a small gap, flip above if too close to bottom
    const gap = 10;
    const popH = popover.offsetHeight;
    if (rect.bottom + gap + popH > window.innerHeight - 16) {
      popover.style.top = (rect.top - popH - gap) + 'px';
      popover.style.bottom = 'auto';
    } else {
      popover.style.top = (rect.bottom + gap) + 'px';
      popover.style.bottom = 'auto';
    }
  }

  function closePopover() {
    popover.setAttribute('aria-hidden', 'true');
    backdrop.setAttribute('aria-hidden', 'true');
  }

  // Event delegation: open on help button click
  document.addEventListener('click', (e) => {
    const btn = e.target.closest('[data-action="show-rs-help"]');
    if (btn) {
      e.stopPropagation();
      if (popover.getAttribute('aria-hidden') === 'false') {
        closePopover();
      } else {
        openPopover(btn);
      }
      return;
    }
    // Close button inside popover
    if (e.target.closest('.rs-popover-close')) {
      closePopover();
      return;
    }
    // Click outside popover closes it
    if (!e.target.closest('.rs-popover') && !e.target.closest('[data-action="show-rs-help"]')) {
      closePopover();
    }
  });

  // Backdrop click closes
  backdrop.addEventListener('click', closePopover);

  // Escape key closes
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && popover.getAttribute('aria-hidden') === 'false') {
      closePopover();
    }
  });
}

document.addEventListener('DOMContentLoaded', init);
