/**
 * Guild Manager Bench · Leaderboard
 *
 * Standalone static leaderboard that reads leaderboard_data.json
 * and renders model ranking cards sorted by rank_score.
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

const MEDALS = ['🥇', '🥈', '🥉'];

// ============================================================================
// Rendering
// ============================================================================
function renderLeaderboard(data) {
  const main = $('#leaderboardMain');
  const meta = $('#topMeta');

  // Top bar meta
  const genTime = data.generated_at ? data.generated_at.replace('T', ' ') : '—';
  meta.innerHTML = `<span>${esc(data.total_runs)} 次运行 · ${data.models.length} 个模型 · 更新于 ${esc(genTime)}</span>`;

  if (!data.models.length) {
    main.innerHTML = `
      <div class="empty-state">
        <h2>暂无数据</h2>
        <p>将 replay JSON 放入 <code>web/leaderboard/data/</code>，然后运行 <code>uv run guild-manager build-leaderboard</code></p>
      </div>`;
    return;
  }

  // Build cards with staggered animation delay
  const cards = data.models.map((m, i) => {
    const html = renderCard(m);
    return html.replace(
      'class="model-card',
      `style="animation-delay:${i * 80}ms" class="model-card`
    );
  }).join('');

  main.innerHTML = `<div class="card-list">${cards}</div>`;

  // Bind expand toggle
  $$('.model-card').forEach((card) => {
    card.addEventListener('click', (e) => {
      // Don't toggle if clicking on a link or button inside
      if (e.target.closest('a, button')) return;
      card.classList.toggle('expanded');
    });
  });
}

function renderCard(m) {
  const rank = m.rank;
  const rankCls = rank <= 3 ? ` rank-${rank}` : '';
  const medal = rank <= 3 ? `<span class="medal">${MEDALS[rank - 1]}</span> ` : '';
  const runDetails = Array.isArray(m.run_details) ? m.run_details : [];
  const latestRun = runDetails[0] || {};

  // Primary stat: rank_score
  const rs = m.rank_score;
  const rankScoreVal = rs ? esc(fmtRankScore(rs.best)) : '—';

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
        <div class="rank-badge">${medal}${rank}</div>
        <div class="model-info">
          <div class="model-name" title="${esc(m.model)}">${esc(m.model)}</div>
          <div class="model-meta">
            <span>${m.runs} 次运行</span>
            ${latestRun.preset ? `<span>${esc(latestRun.preset)}</span>` : ''}
            ${m.last_run ? `<span>${esc(fmtTimestamp(m.last_run))}</span>` : ''}
          </div>
        </div>
        <div class="card-stats">
          <div class="stat-block primary-stat">
            <div class="stat-label">Rank Score</div>
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
  const items = [];

  if (eff.input_tokens) {
    const inTok = eff.input_tokens;
    items.push(metricChip('📥 Input', fmtTokens(inTok.mean), inTok.total !== inTok.mean ? `总计 ${fmtInt(inTok.total)}` : null));
  }
  if (eff.output_tokens) {
    const outTok = eff.output_tokens;
    items.push(metricChip('📤 Output', fmtTokens(outTok.mean), outTok.total !== outTok.mean ? `总计 ${fmtInt(outTok.total)}` : null));
  }
  if (eff.duration_seconds) {
    const dur = eff.duration_seconds;
    items.push(metricChip('⏱ 耗时', fmtDuration(dur.mean), dur.total !== dur.mean ? `总计 ${fmtDuration(dur.total)}` : null));
  }
  if (eff.tool_calls) {
    const tc = eff.tool_calls;
    items.push(metricChip('🔧 操作', fmtInt(tc.mean), tc.total !== tc.mean ? `总计 ${fmtInt(tc.total)}` : null));
  }

  if (!items.length) return '';
  return `
    <div class="metrics-group">
      <div class="metrics-group-title">效率</div>
      <div class="metrics-chips">${items.join('')}</div>
    </div>`;
}

function renderGameQualitySection(gq) {
  const items = [];

  if (gq.battle_win_rate != null) {
    const pct = (gq.battle_win_rate * 100).toFixed(1);
    items.push(metricChip('⚔ 战斗胜率', `${pct}%`, gq.battles_won != null ? `${fmtInt(gq.battles_won)} / ${fmtInt(gq.battles_total)}` : null));
  }
  if (gq.gold_earned) {
    items.push(metricChip('💰 金币', fmtInt(gq.gold_earned.mean), gq.gold_earned.best !== gq.gold_earned.mean ? `最佳 ${fmtInt(gq.gold_earned.best)}` : null));
  }
  if (gq.exp_earned) {
    items.push(metricChip('✨ 经验', fmtInt(gq.exp_earned.mean), gq.exp_earned.best !== gq.exp_earned.mean ? `最佳 ${fmtInt(gq.exp_earned.best)}` : null));
  }

  if (!items.length) return '';
  return `
    <div class="metrics-group">
      <div class="metrics-group-title">游戏</div>
      <div class="metrics-chips">${items.join('')}</div>
    </div>`;
}

function metricChip(label, value, subtitle) {
  return `
    <div class="metric-chip"${subtitle ? ` title="${esc(subtitle)}"` : ''}>
      <div class="metric-chip-label">${esc(label)}</div>
      <div class="metric-chip-value">${esc(value)}</div>
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

    const partyText = run.party_size != null
      ? `${run.party_size}/${run.party_size_limit ?? '—'}`
      : '—';

    const seeds = [
      run.game_seed != null ? `game ${fmtInt(run.game_seed)}` : null,
      run.scoring_seed != null ? `score ${fmtInt(run.scoring_seed)}` : null,
    ].filter(Boolean).join(' · ');

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
        ${(tu.input_tokens || timing.total_seconds || tc.total) ? `
        <div class="run-metrics" style="margin-top:6px">
          ${tu.input_tokens ? runMetric('Input Tokens', fmtInt(tu.input_tokens)) : ''}
          ${tu.output_tokens ? runMetric('Output Tokens', fmtInt(tu.output_tokens)) : ''}
          ${timing.total_seconds ? runMetric('耗时', fmtDuration(timing.total_seconds)) : ''}
          ${tc.total ? runMetric('操作数', fmtInt(tc.total)) : ''}
          ${ga.battles_won != null && ga.battles_total ? runMetric('战斗胜率', `${ga.battles_won}/${ga.battles_total}`) : ''}
          ${ga.total_gold_earned != null ? runMetric('金币', fmtInt(ga.total_gold_earned)) : ''}
          ${ga.total_experience_earned != null ? runMetric('经验', fmtInt(ga.total_experience_earned)) : ''}
        </div>` : ''}
        <div class="run-submeta">
          ${run.preset ? `<span>${esc(run.preset)}</span>` : ''}
          ${seeds ? `<span>${esc(seeds)}</span>` : ''}
          ${run.score_mode ? `<span>${esc(run.score_mode)}</span>` : ''}
          ${run.rank_score_source ? `<span>rank ${esc(run.rank_score_source)}</span>` : ''}
        </div>
      </div>`;
  }).join('');

  return `
    <div class="detail-section run-section">
      <div class="detail-title">运行明细</div>
      <div class="run-list">${items}</div>
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
    const res = await fetch('leaderboard_data.json');
    if (!res.ok) throw new Error(`HTTP ${res.status}: ${res.statusText}`);
    const data = await res.json();
    $('#loadingState')?.remove();
    renderLeaderboard(data);
  } catch (e) {
    showError(`无法加载 leaderboard_data.json: ${e.message}`);
  }
}

document.addEventListener('DOMContentLoaded', init);
