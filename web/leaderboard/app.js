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

// 简洁的位置/分位标识
function percentileForRank(rank, total) {
  if (rank == null || !total) return null;
  // topPct 越小越靠前
  const topPct = (rank / total) * 100;
  if (rank === 1) return 'Top 1';
  if (topPct <= 6) return 'Top 5%';
  if (topPct <= 11) return 'Top 10%';
  if (topPct <= 22) return 'Top 20%';
  if (topPct <= 33) return 'Top 33%';
  if (topPct <= 50) return 'Top 50%';
  return null;
}

// ============================================================================
// Global State
// ============================================================================
let _leaderboardData = null;
let _modelNotes = {};       // model name -> note string
let _curveChart = null;
let _curveRunSelection = {}; // key: "model::run_id" -> boolean
let _curveMetric = 'rank_score';

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
    return `
      <label class="curve-legend-item">
        <input type="checkbox" data-curve-key="${esc(r.key)}" ${checked} />
        <span class="curve-color-dot" style="background:${color}"></span>
        <span class="curve-legend-model">${esc(r.model)}</span>
        <span class="curve-legend-meta">${esc(timeLabel)} · ${esc(metricLabel)}</span>
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
  const main = $('#leaderboardMain');
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
    return;
  }

  // Stats banner metrics
  renderStatsBanner(data);

  // Build cards with staggered animation delay
  const topScore = data.models
    .map((m) => (m.rank_score && m.rank_score.best) || 0)
    .reduce((a, b) => Math.max(a, b), 0);
  const avgScore = data.models
    .map((m) => (m.rank_score && m.rank_score.best) || 0)
    .filter((v) => v > 0);
  const avgVal = avgScore.length
    ? avgScore.reduce((a, b) => a + b, 0) / avgScore.length
    : null;
  const total = data.models.length;

  const cards = data.models.map((m, i) => {
    const html = renderCard(m, { topScore, avgVal, total });
    return html.replace(
      'class="model-card',
      `style="animation-delay:${i * 80}ms" class="model-card`
    );
  }).join('');

  container.innerHTML = `<div class="card-list">${cards}</div>`;

  // Bind expand toggle
  $$('.model-card').forEach((card) => {
    card.addEventListener('click', (e) => {
      // Don't toggle if clicking on a link or button inside
      if (e.target.closest('a, button')) return;
      card.classList.toggle('expanded');
    });
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

  const items = [
    { label: '参赛模型', value: models.length, unit: '个' },
    { label: '累计运行', value: totalRuns, unit: '次' },
    { label: '最高分', value: fmtRankScore(topScore) || '—', unit: 'Rank Score' },
    { label: '平均分', value: fmtRankScore(Math.round(avgScore)) || '—', unit: 'Rank Score' },
    { label: '游戏种子', value: seedLabel, unit: '复现' },
    { label: '评分种子', value: scoreSeedLabel, unit: '复现' },
  ];

  el.innerHTML = items
    .map(
      (it) => `
      <div class="stats-banner-cell">
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
  const total = (ctx && ctx.total) || 0;

  // Primary stat: rank_score
  const rs = m.rank_score;
  const rankScoreVal = rs ? esc(fmtRankScore(rs.best)) : '—';

  // Percentile (subtle, plain text)
  const pctLabel = percentileForRank(rank, total);

  // Efficiency stats
  const eff = m.efficiency || {};
  const hasEff = eff && (eff.input_tokens || eff.output_tokens || eff.duration_seconds || eff.tool_calls);

  // Game quality stats
  const gq = m.game_quality || {};
  const hasGq = gq && (gq.gold_earned || gq.exp_earned || gq.battle_win_rate != null);

  // Detail rows
  const details = [];
  if (rs) {
    details.push(detailRow('Rank Score · 最佳', fmtRankScore(rs.best)));
    details.push(detailRow('Rank Score · 均值', fmtRankScore(rs.mean)));
    details.push(detailRow('Rank Score · 中位', fmtRankScore(rs.median)));
  }
  if (m.last_run) {
    details.push(detailRow('最近运行', fmtTimestamp(m.last_run)));
  }
  if (latestRun.preset) {
    details.push(detailRow('Preset', latestRun.preset));
  }
  if (latestRun.game_seed != null) {
    details.push(detailRow('Game Seed', fmtInt(latestRun.game_seed)));
  }
  if (latestRun.scoring_seed != null) {
    details.push(detailRow('Scoring Seed', fmtInt(latestRun.scoring_seed)));
  }
  if (latestRun.data_hash) {
    details.push(detailRow('Data Hash', latestRun.data_hash, { wide: true }));
  }

  return `
    <div class="model-card${rankCls}" data-rank="${rank}">
      <div class="card-header">
        <div class="rank-badge">${rank}</div>
        <div class="model-info">
          <div class="model-name" title="${esc(m.model)}">${esc(m.model)}${renderModelNote(m.model)}</div>
          <div class="model-meta">
            ${pctLabel ? `<span class="pct-label">${esc(pctLabel)}</span>` : ''}
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

      <div class="card-detail">
        <div class="detail-section">
          <div class="detail-title">聚合指标</div>
          <div class="detail-grid">${details.join('')}</div>
        </div>
        ${renderRunDetails(runDetails)}
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
  if (!cells.length) return '';
  return `
    <div class="metric-group">
      <div class="metric-group-title">Game</div>
      <div class="metric-group-cells">${cells.join('')}</div>
    </div>`;
}

function metricCell(label, value, opts = {}) {
  const cls = opts.cls ? ` ${opts.cls}` : '';
  return `
    <div class="metric-cell${cls}">
      <dt class="metric-cell-label">${esc(label)}</dt>
      <dd class="metric-cell-value">${value}</dd>
    </div>`;
}

function detailRow(label, value, options = {}) {
  const display = value == null || value === '' ? '—' : String(value);
  const cls = options.wide ? 'detail-item wide' : 'detail-item';
  return `
    <div class="${cls}">
      <span class="detail-label">${esc(label)}</span>
      <span class="detail-value" title="${esc(display)}">${esc(display)}</span>
    </div>`;
}

function renderRunDetails(runs) {
  if (!runs.length) return '';
  const items = runs.map((run) => {
    const best = run.best_adventurer || {};
    const bestText = best.name
      ? `${best.name} (${fmtScore(best.average_score) || '—'})`
      : '—';

    // Efficiency
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

    const seeds = [
      run.game_seed != null ? `game ${fmtInt(run.game_seed)}` : null,
      run.scoring_seed != null ? `score ${fmtInt(run.scoring_seed)}` : null,
    ].filter(Boolean).join(' · ');
    const contributors = renderRankContributors(run.rank_score_per_adventurer);
    const toolBreakdown = renderToolBreakdown(tc);

    return `
      <div class="run-item">
        <div class="run-head">
          <span class="run-time">${esc(fmtTimestamp(run.created_at))}</span>
          <span class="run-id">${esc(shortRunId(run.run_id || run.session_id || ''))}</span>
        </div>
        <div class="run-metrics">
          ${runMetric('Rank Score', fmtRankScore(run.rank_score))}
          ${runMetric('Arena Score', fmtScore(run.score))}
          ${runMetric('Arena 胜率', fmtPct(run.win_rate))}
          ${runMetric('队伍', partyText)}
          ${runMetric('回合', `${run.turns ?? '—'}/${run.max_turns ?? '—'}`)}
          ${runMetric('最强', bestText)}
        </div>
        ${(tu.input_tokens || timing.total_seconds || tc.total || defeatedText || ga.total_gold_earned != null || ga.total_experience_earned != null) ? `
        <div class="run-metrics" style="margin-top:6px">
          ${tu.input_tokens ? runMetric('Input Tokens', fmtInt(tu.input_tokens)) : ''}
          ${tu.output_tokens ? runMetric('Output Tokens', fmtInt(tu.output_tokens)) : ''}
          ${timing.total_seconds ? runMetric('耗时', fmtDuration(timing.total_seconds)) : ''}
          ${tc.total ? runMetric('操作数', fmtInt(tc.total)) : ''}
          ${ga.battles_won != null && ga.battles_total ? runMetric('战斗胜率', `${ga.battles_won}/${ga.battles_total}`) : ''}
          ${ga.total_gold_earned != null ? runMetric('金币', fmtInt(ga.total_gold_earned)) : ''}
          ${ga.total_experience_earned != null ? runMetric('经验', fmtInt(ga.total_experience_earned)) : ''}
          ${defeatedText ? runMetric('最强击败', defeatedText) : ''}
        </div>` : ''}
        <div class="run-submeta">
          ${run.preset ? `<span>${esc(run.preset)}</span>` : ''}
          ${seeds ? `<span>${esc(seeds)}</span>` : ''}
          ${run.score_mode ? `<span>${esc(run.score_mode)}</span>` : ''}
          ${run.rank_score_source ? `<span>rank ${esc(run.rank_score_source)}</span>` : ''}
        </div>
        ${toolBreakdown}
        ${contributors}
      </div>`;
  }).join('');

  return `
    <div class="detail-section run-section">
      <div class="detail-title">运行明细</div>
      <div class="run-list">${items}</div>
    </div>`;
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
    <div class="rank-contrib-list tool-breakdown-list">
      ${items.slice(0, 12).map((item) => `
        <div class="rank-contrib-chip" title="${esc(item.name)}">
          <strong>${esc(toolLabel(item.name))}</strong>
          <em>${esc(fmtInt(item.total))}</em>
          ${item.failed ? `<span>失败 ${esc(fmtInt(item.failed))}</span>` : ''}
        </div>`).join('')}
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
      };
    })
    .filter((item) => Number.isFinite(item.score))
    .sort((a, b) => b.score - a.score);
}

function renderRankContributors(values) {
  const items = rankContributorItems(values);
  if (!items.length) return '';
  return `
    <div class="rank-contrib-list">
      ${items.map((item) => {
        const share = item.share != null ? `<span>${esc(fmtPct(item.share))}</span>` : '';
        return `
          <div class="rank-contrib-chip" title="${esc(item.name)}">
            <strong>${esc(item.name)}</strong>
            <em>${esc(fmtRankScore(item.score))}</em>
            ${share}
          </div>`;
      }).join('')}
    </div>`;
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
    btn.textContent = card.classList.contains('collapsed') ? '展开' : '收起';
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
