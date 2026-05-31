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

function fmtTimestamp(ts) {
  if (!ts) return '—';
  // ts format: "20260530-204516-968867"
  const m = ts.match(/^(\d{4})(\d{2})(\d{2})-(\d{2})(\d{2})(\d{2})/);
  if (!m) return ts;
  return `${m[1]}-${m[2]}-${m[3]} ${m[4]}:${m[5]}:${m[6]}`;
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
  meta.innerHTML = `<span class="muted">生成于 ${esc(genTime)} · ${data.total_runs} 次运行 · ${data.models.length} 个模型</span>`;

  if (!data.models.length) {
    main.innerHTML = `
      <div class="empty-state">
        <h2>暂无数据</h2>
        <p>将 replay.json 文件放入 <code>data/</code> 目录，然后运行 <code>python scripts/build_leaderboard.py</code></p>
      </div>`;
    return;
  }

  // Build cards
  const cards = data.models.map((m, i) => renderCard(m, i)).join('');
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

  return `
    <div class="model-card${rankCls}" data-rank="${rank}">
      <div class="card-header">
        <div class="rank-badge">${medal}${rank}</div>
        <div class="model-info">
          <div class="model-name">${esc(m.model)}</div>
          <div class="model-meta">
            <span>${m.runs} 次运行</span>
            ${wrBest ? `<span>胜率 ${esc(wrBest)}</span>` : ''}
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
      </div>
      <div class="card-detail">${details.join('')}</div>
      <div class="expand-hint">点击展开详情</div>
    </div>`;
}

function detailRow(label, value) {
  return `
    <div class="detail-item">
      <span class="detail-label">${esc(label)}</span>
      <span class="detail-value">${esc(value || '—')}</span>
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
