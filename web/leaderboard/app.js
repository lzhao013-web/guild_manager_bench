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

function shortHash(hash) {
  return hash ? String(hash).slice(0, 10) : null;
}

function shortRunId(value) {
  if (!value) return '';
  const text = String(value);
  if (text.length <= 28) return text;
  return `${text.slice(0, 15)}…${text.slice(-10)}`;
}

/** Return CSS class for win-rate bar color */
function winrateClass(rate) {
  if (rate == null) return '';
  if (rate >= 0.7) return 'high';
  if (rate >= 0.4) return 'mid';
  return 'low';
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
    // Wrap to inject animation delay
    return html.replace(
      'class="model-card',
      `style="animation-delay:${i * 80}ms" class="model-card`
    );
  }).join('');

  main.innerHTML = `<div class="card-list">${cards}</div>`;

  // Bind expand toggle
  $$('.model-card').forEach((card) => {
    card.addEventListener('click', () => {
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
  const rankScoreHtml = rs
    ? `<span class="stat-value rank-val">${esc(fmtRankScore(rs.best))}</span>`
    : `<span class="stat-value empty-val">—</span>`;

  // Secondary stat: score
  const sc = m.score;
  const scoreHtml = sc
    ? `<span class="stat-value score-val">${esc(fmtScore(sc.best))}</span>`
    : `<span class="stat-value empty-val">—</span>`;

  // Win rate
  const wr = m.win_rate;
  const wrBest = wr ? fmtPct(wr.best) : null;
  const wrBestPct = wr && wr.best != null ? (wr.best * 100).toFixed(1) : 0;

  // Detail rows
  const details = [];
  if (rs) {
    details.push(detailRow('Rank Score · 最佳', fmtRankScore(rs.best)));
    details.push(detailRow('Rank Score · 均值', fmtRankScore(rs.mean)));
    details.push(detailRow('Rank Score · 中位', fmtRankScore(rs.median)));
  }
  if (sc) {
    details.push(detailRow('Arena Score · 最佳', fmtScore(sc.best)));
    details.push(detailRow('Arena Score · 均值', fmtScore(sc.mean)));
    details.push(detailRow('Arena Score · 中位', fmtScore(sc.median)));
  }
  if (wr) {
    details.push(detailRow('胜率 · 最佳', fmtPct(wr.best)));
    details.push(detailRow('胜率 · 均值', fmtPct(wr.mean)));
  }
  if (m.last_run) {
    details.push(detailRow('最近运行', fmtTimestamp(m.last_run)));
  }
  if (latestRun.preset) {
    details.push(detailRow('Preset', latestRun.preset));
  }
  if (latestRun.data_hash) {
    details.push(detailRow('Data Hash', latestRun.data_hash, { wide: true }));
  }
  if (latestRun.game_seed != null) {
    details.push(detailRow('Game Seed', fmtInt(latestRun.game_seed)));
  }
  if (latestRun.scoring_seed != null) {
    details.push(detailRow('Scoring Seed', fmtInt(latestRun.scoring_seed)));
  }
  if (latestRun.score_waves != null || latestRun.score_wave_size != null) {
    details.push(detailRow('Arena', `${fmtInt(latestRun.score_waves) || '—'} waves · size ${fmtInt(latestRun.score_wave_size) || '—'}`));
  }

  return `
    <div class="model-card${rankCls}" data-rank="${rank}">
      <div class="card-header">
        <div class="rank-badge">${medal}${rank}</div>
        <div class="model-info">
          <div class="model-name" title="${esc(m.model)}">${esc(m.model)}</div>
          <div class="model-meta">
            <span>${m.runs} 次运行</span>
            ${wrBest ? `<span>胜率 ${esc(wrBest)}</span>` : ''}
            ${latestRun.preset ? `<span>${esc(latestRun.preset)}</span>` : ''}
            ${m.last_run ? `<span>${esc(fmtTimestamp(m.last_run))}</span>` : ''}
          </div>
        </div>
        <div class="card-stats">
          <div class="stat-block">
            <div class="stat-label">Rank Score</div>
            ${rankScoreHtml}
          </div>
          <div class="stat-block">
            <div class="stat-label">Score</div>
            ${scoreHtml}
          </div>
        </div>
        <div class="expand-chevron" aria-label="展开详情">
          <svg width="14" height="14" viewBox="0 0 14 14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <path d="M3.5 5.25L7 8.75L10.5 5.25"/>
          </svg>
        </div>
      </div>
      ${renderCompactRunSummary(latestRun, wrBest, wrBestPct)}
      <div class="card-detail">
        <div class="detail-section">
          <div class="detail-title">聚合指标</div>
          <div class="detail-grid">${details.join('')}</div>
        </div>
        ${renderRunDetails(runDetails)}
      </div>
    </div>`;
}

function renderCompactRunSummary(run, wrBest, wrBestPct) {
  if (!run || !Object.keys(run).length) return '';
  const best = run.best_adventurer || {};
  const bestText = best.name
    ? `${best.name} · ${fmtScore(best.average_score) || '—'} · ${fmtPct(best.win_rate) || '—'}`
    : '—';
  const runId = run.run_id || run.session_id || '';

  // Build win-rate bar HTML if data available
  let winrateBarHtml = '';
  if (wrBest) {
    const cls = winrateClass(run.win_rate);
    winrateBarHtml = `<div class="winrate-bar-wrap"><div class="winrate-bar ${cls}" style="width:${wrBestPct}%"></div></div>`;
  }

  return `
    <div class="compact-run">
      ${compactMetric('Run', shortRunId(runId), { title: runId, wide: true })}
      ${compactMetric('Preset', run.preset || '—')}
      ${compactMetric('Party', run.party_size != null || run.party_size_limit != null ? `${run.party_size ?? '—'}/${run.party_size_limit ?? '—'}` : '—')}
      ${compactMetric('Gold / EXP', `${fmtInt(run.final_gold) || '—'} / ${fmtInt(run.final_experience_pool) || '—'}`)}
      ${compactMetric('Best', bestText)}
      ${compactMetric('Seeds', `${run.game_seed ?? '—'} / ${run.scoring_seed ?? '—'}`)}
      ${winrateBarHtml}
    </div>`;
}

function compactMetric(label, value, options = {}) {
  const display = value == null || value === '' ? '—' : String(value);
  const title = options.title == null ? display : String(options.title);
  const cls = options.wide ? 'compact-metric wide' : 'compact-metric';
  return `
    <div class="${cls}">
      <span>${esc(label)}</span>
      <strong title="${esc(title)}">${esc(display)}</strong>
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
      ? `${best.name} · ${fmtScore(best.average_score) || '—'} · ${fmtPct(best.win_rate) || '—'}`
      : '—';
    const partyText = run.party_size != null || run.party_size_limit != null
      ? `${run.party_size ?? '—'}/${run.party_size_limit ?? '—'}`
      : '—';
    const finalTurn = run.final_turn != null || run.max_turns != null
      ? `${run.final_turn ?? '—'}/${run.max_turns ?? '—'}`
      : '—';
    const seeds = [
      run.game_seed != null ? `game ${fmtInt(run.game_seed)}` : null,
      run.scoring_seed != null ? `score ${fmtInt(run.scoring_seed)}` : null,
    ].filter(Boolean).join(' · ');

    return `
      <div class="run-item">
        <div class="run-head">
          <span class="run-time">${esc(fmtTimestamp(run.created_at))}</span>
          <span class="run-id">${esc(run.run_id || run.session_id || '')}</span>
        </div>
        <div class="run-metrics">
          ${runMetric('Rank', fmtRankScore(run.rank_score))}
          ${runMetric('Score', fmtScore(run.score))}
          ${runMetric('Win', fmtPct(run.win_rate))}
          ${runMetric('Turns', `${run.turns ?? '—'} trace · ${finalTurn} obs`)}
          ${runMetric('Party', partyText)}
          ${runMetric('Gold', fmtInt(run.final_gold))}
          ${runMetric('EXP', fmtInt(run.final_experience_pool))}
          ${runMetric('Best Adventurer', bestText)}
        </div>
        <div class="run-submeta">
          ${run.preset ? `<span>${esc(run.preset)}</span>` : ''}
          ${run.data_hash ? `<span class="hash-line">${esc(run.data_hash)}</span>` : ''}
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
