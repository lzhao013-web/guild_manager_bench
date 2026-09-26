import { normalizeReplay } from './compare-model.mjs';
import { ProfileView } from './profile-view.mjs';

const $ = id => document.getElementById(id);
const initial = new URLSearchParams(location.search);
const view = new ProfileView($('profileView'));
let runId = null, currentTurn = null, loadTicket = 0, listTicket = 0;

async function getJSON(url) {
  const response = await fetch(url);
  if (!response.ok) throw new Error(`读取失败 (HTTP ${response.status})`);
  return response.json();
}
function updateURL() {
  const params = new URLSearchParams();
  if (runId) params.set('run', runId);
  if (currentTurn !== null) params.set('turn', currentTurn);
  history.replaceState(null, '', `${location.pathname}${params.size ? `?${params}` : ''}`);
}
async function listRuns() {
  const ticket = ++listTicket;
  $('profileListStatus').textContent = '正在读取存档列表…';
  try {
    const data = await getJSON('/api/llm/runs');
    if (!Array.isArray(data.runs)) throw new Error('存档列表格式不正确');
    if (ticket !== listTicket) return;
    const previous = $('profileRun').value;
    $('profileRun').replaceChildren(new Option('选择服务端存档…', ''));
    for (const run of data.runs) $('profileRun').add(new Option(`${run.model || run.run_id} · ${run.preset || '?'} · ${run.created_at || run.run_id}`, run.run_id));
    $('profileRun').value = previous || runId || '';
    $('profileListStatus').textContent = `${data.runs.length} 份服务端存档，也可打开本地文件。`;
  } catch (error) {
    if (ticket === listTicket) $('profileListStatus').textContent = `列表不可用：${error.message}。仍可打开本地文件。`;
  }
}
async function load(loader, label, id = null) {
  const ticket = ++loadTicket;
  $('profileLoad').disabled = $('profileFile').disabled = true;
  $('profileLoadStatus').textContent = '正在整理本局经营记录…';
  $('profileError').hidden = true;
  try {
    const normalized = normalizeReplay(await loader(), label);
    if (ticket !== loadTicket) return;
    const requestedTurn = ticket === 1 ? Number(initial.get('turn')) : null;
    view.show(normalized, { runId: id, turn: requestedTurn });
    runId = id;
    currentTurn = view.selectedTurn;
    $('profileRun').value = id || '';
    $('profileSourceTitle').textContent = `${normalized.meta.model} · ${label}`;
    $('profileLoadStatus').textContent = `已生成 ${normalized.meta.model} 的本局画像。`;
    $('profileSource').open = false;
    $('profileEmpty').hidden = true;
    document.body.classList.add('compare-ready');
    updateURL();
  } catch (error) {
    if (ticket !== loadTicket) return;
    $('profileError').textContent = error.message;
    $('profileError').hidden = false;
    $('profileLoadStatus').textContent = view.profile ? '加载失败，保留上一份画像。' : '尚未成功加载跑局。';
  } finally {
    if (ticket === loadTicket) $('profileLoad').disabled = $('profileFile').disabled = false;
  }
}
function loadRun(id) { if (id) return load(() => getJSON(`/api/llm/runs/${encodeURIComponent(id)}/replay`), id, id); }
$('profileLoad').addEventListener('click', () => loadRun($('profileRun').value));
$('profileFile').addEventListener('change', event => {
  const file = event.target.files[0];
  if (file) load(async () => JSON.parse(await file.text()), file.name);
  event.target.value = '';
});
$('profileRefresh').addEventListener('click', listRuns);
$('profileView').addEventListener('profile-navigate', event => { currentTurn = event.detail.turn; updateURL(); });
await listRuns();
if (initial.get('run') && loadTicket === 0) await loadRun(initial.get('run'));
