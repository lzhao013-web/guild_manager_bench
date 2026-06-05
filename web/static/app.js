const state = {
  sessionId: null,
  observation: null,
  events: [],
  selectedHunts: new Map(),
  openDetails: new Set(),
  actionPending: false,
  socket: null,
  watchOnly: new URLSearchParams(window.location.search).get("watch") === "1",
  llm: {
    socket: null,
    running: false,
    status: "未启动",
    prompt: "",
    transcript: [],
    toolTrace: [],
    events: [],
    currentModelEntry: null,
    renderQueued: false,
    openThinking: new Set(),
    openToolTrace: new Set(),
    openTurns: new Set(),
    userCollapsedTurns: new Set(),
    thinkingTurns: new Set(),
    toolTraceSeq: 0,
    autoScroll: true,
    userScrolledUp: false,
    replay: {
      runs: [],
      selectedRunId: "",
      data: null,
      source: "",
      status: "尚未加载 replay",
    },
  },
};

const $ = (id) => document.getElementById(id);

/* ========== Class / Monster Visuals ========== */

// 职业视觉元数据：颜色 + 中文名 + 图标路径
// 颜色取自每个职业在游戏中的主题色；fallback 用于未知 template_id
const CLASS_META = {
  mercenary_warrior: { name: "佣兵战士", color: "#e07b3a", role: "近战" },
  foot_knight:       { name: "步行骑士", color: "#7daaf5", role: "坦克" },
  woodland_archer:   { name: "林地射手", color: "#4ade80", role: "远程" },
  spellshot_mage:    { name: "魔弹法师", color: "#a78bfa", role: "法师" },
  cleric:            { name: "神官",     color: "#fbbf24", role: "治疗" },
  jester:            { name: "连击师",   color: "#f472b6", role: "敏捷" },
  ascetic_monk:      { name: "苦行僧",   color: "#fb923c", role: "气功" },
  bloodfiend:        { name: "吸血魔",   color: "#dc2626", role: "暗影" },
  cannoneer:         { name: "炮手",     color: "#a16207", role: "炮击" },
  plague_mage:       { name: "瘟疫法师", color: "#84cc16", role: "毒系" },
};
const DEFAULT_CLASS_META = { name: "未知职业", color: "#636d8c", role: "?" };

// 怪物视觉元数据：颜色按 tier 区分
const MONSTER_TIER_META = {
  normal: { name: "普通", color: "#a8b2cc" },
  elite:  { name: "精英", color: "#fbbf24" },
  boss:   { name: "首领", color: "#ef5b5b" },
};
const DEFAULT_TIER_META = { name: "普通", color: "#a8b2cc" };

// 装备槽位图标
const SLOT_ICON = {
  main_hand: "⚔️",
  off_hand: "🛡️",
  two_hand: "⚔️",
  hand: "🗡️",
  boots: "👢",
  helmet: "🪖",
  armor: "🥋",
  accessory: "💍",
};

function classMeta(templateId) {
  return CLASS_META[templateId] || { ...DEFAULT_CLASS_META, name: templateId || DEFAULT_CLASS_META.name };
}

function tierMeta(tier) {
  return MONSTER_TIER_META[tier] || DEFAULT_TIER_META;
}

function classPortraitHtml(templateId, name) {
  const meta = classMeta(templateId);
  const initial = escapeHtml((name || meta.name || "?").slice(0, 1));
  const url = templateId ? `/assets/icons/classes/${encodeURIComponent(templateId)}.png` : "";
  if (!url) {
    return `<div class="avatar avatar-placeholder" style="--class-color:${meta.color}">${initial}</div>`;
  }
  return `<img class="avatar avatar-img" src="${escapeHtml(url)}" alt="${escapeHtml(meta.name)}" loading="lazy" style="--class-color:${meta.color}" onerror="this.outerHTML='<div class=&quot;avatar avatar-placeholder&quot; style=&quot;--class-color:${meta.color}&quot;>${initial}</div>'" />`;
}

function monsterPortraitHtml(monster) {
  const meta = tierMeta(monster?.tier);
  const aid = monster?.archetype_id || "";
  const initial = escapeHtml((monster?.name || "?").slice(0, 1));
  if (!aid) {
    return `<div class="avatar avatar-placeholder avatar-monster" style="--tier-color:${meta.color}">${initial}</div>`;
  }
  const url = `/assets/icons/monsters/${encodeURIComponent(aid)}.png`;
  return `<img class="avatar avatar-img avatar-monster" src="${escapeHtml(url)}" alt="${escapeHtml(monster.name || "")}" loading="lazy" style="--tier-color:${meta.color}" onerror="this.outerHTML='<div class=&quot;avatar avatar-placeholder avatar-monster&quot; style=&quot;--tier-color:${meta.color}&quot;>${initial}</div>'" />`;
}

function slotIcon(slot) {
  return SLOT_ICON[slot] || "📦";
}

window.addEventListener("load", () => {
  $("newSessionButton").addEventListener("click", () => createSession());
  $("exportButton").addEventListener("click", () => exportSession());
  $("importFile").addEventListener("change", (event) => importSession(event));
  $("llmStartButton").addEventListener("click", () => startLlmDebug());
  $("llmStopButton").addEventListener("click", () => stopLlmDebug());
  $("llmReplayRefreshButton").addEventListener("click", () => refreshLlmReplayRuns());
  $("llmReplayLoadButton").addEventListener("click", () => loadSelectedLlmReplay());
  $("llmReplayResumeButton").addEventListener("click", () => resumeSelectedLlmReplay());
  $("llmReplaySelect").addEventListener("change", (event) => {
    state.llm.replay.selectedRunId = event.target.value;
    renderLlmDebug();
  });
  $("llmReplayFile").addEventListener("change", (event) => loadLlmReplayFile(event));
  $("llmCopyPromptButton").addEventListener("click", () => copyLlmPromptToClipboard());
  $("llmTranscriptClearButton").addEventListener("click", () => clearLlmTranscript());
  $("llmEventClearButton").addEventListener("click", () => clearLlmEventLog());
  $("llmAutoScrollButton").addEventListener("click", () => toggleLlmAutoScroll());
  $("llmScrollBottomButton")?.addEventListener("click", () => scrollLlmPanelToBottom({ force: true, smooth: true }));
  $("llmPresetSelect").addEventListener("change", (event) => applyLlmPreset(event.target.value));
  initLlmRuntimeTabs();
  initLlmAutoScrollWatchers();
  syncLlmAutoScrollButton();
  $("combatModal").addEventListener("click", (e) => {
    if (e.target === e.currentTarget) closeCombatModal();
  });
  $("modelPromptModal").addEventListener("click", (e) => {
    if (e.target === e.currentTarget) closeModelPromptModal();
  });
  document.addEventListener("click", onDocumentClick);
  document.addEventListener("toggle", onDocumentToggle, true);
  initModes();
  initTabs();
  bootstrap();
  refreshLlmReplayRuns();
});

/* ========== Mode / Tab Switching ========== */

function initModes() {
  const params = new URLSearchParams(window.location.search);
  const initialMode = params.get("mode") === "manual" ? "manual" : "llm";
  document.querySelectorAll(".mode-button").forEach((button) => {
    button.addEventListener("click", () => setMode(button.dataset.mode || "manual"));
  });
  setMode(initialMode, false);
}

function setMode(mode, updateUrl = true) {
  const selectedMode = mode === "llm" ? "llm" : "manual";
  document.querySelectorAll(".mode-button").forEach((button) => {
    button.classList.toggle("active", button.dataset.mode === selectedMode);
  });
  document.querySelectorAll(".mode-panel").forEach((panel) => {
    panel.classList.toggle("active", panel.dataset.modePanel === selectedMode);
  });
  if (!updateUrl) {
    return;
  }
  const url = new URL(window.location.href);
  if (selectedMode === "manual") {
    url.searchParams.set("mode", "manual");
  } else {
    url.searchParams.delete("mode");
  }
  window.history.replaceState(null, "", url);
}

function initTabs() {
  document.querySelectorAll(".tab").forEach((tab) => {
    tab.addEventListener("click", () => {
      document.querySelectorAll(".tab").forEach((t) => t.classList.remove("active"));
      tab.classList.add("active");
      document.querySelectorAll(".tab-panel").forEach((p) => p.classList.remove("active"));
      const panel = document.querySelector(`[data-panel="${tab.dataset.tab}"]`);
      if (panel) panel.classList.add("active");
    });
  });
}

function initLlmRuntimeTabs() {
  document.querySelectorAll(".llm-runtime-tab").forEach((tab) => {
    tab.addEventListener("click", () => setLlmRuntimeTab(tab.dataset.runtime));
  });
}

function setLlmRuntimeTab(name) {
  if (!name) return;
  document.querySelectorAll(".llm-runtime-tab").forEach((tab) => {
    tab.classList.toggle("active", tab.dataset.runtime === name);
  });
  document.querySelectorAll(".llm-runtime-panel").forEach((panel) => {
    panel.classList.toggle("active", panel.dataset.runtimePanel === name);
  });
  // 切换 tab 时如果自动滚动开启，滚到底部
  if (state.llm.autoScroll) {
    requestAnimationFrame(() => scrollLlmPanelToBottom({ force: true }));
  }
}

/* ========== Auto Scroll ========== */

function initLlmAutoScrollWatchers() {
  // transcript 不再内嵌滚动，监听 window 滚
  window.addEventListener("scroll", handleWindowScroll, { passive: true });
}

function handleWindowScroll() {
  // 计算 window 距离页面底部的距离
  const doc = document.documentElement;
  const distanceFromBottom = doc.scrollHeight - window.scrollY - window.innerHeight;
  const atBottom = distanceFromBottom <= 24;
  if (atBottom) {
    if (state.llm.userScrolledUp) {
      state.llm.userScrolledUp = false;
      if (state.llm.autoScroll) syncLlmAutoScrollButton();
    }
    toggleScrollBottomButton(false);
  } else {
    if (state.llm.autoScroll) {
      // 自动滚动期间用户向上滚了 → 关闭自动滚动
      state.llm.autoScroll = false;
      state.llm.userScrolledUp = true;
      syncLlmAutoScrollButton();
    }
    toggleScrollBottomButton(true);
  }
}

function toggleLlmAutoScroll() {
  state.llm.autoScroll = !state.llm.autoScroll;
  state.llm.userScrolledUp = false;
  syncLlmAutoScrollButton();
  if (state.llm.autoScroll) {
    // 重新打开时立即滚到底部
    scrollLlmPanelToBottom({ force: true });
  }
}

function syncLlmAutoScrollButton() {
  const btn = $("llmAutoScrollButton");
  if (!btn) return;
  const on = state.llm.autoScroll && !state.llm.userScrolledUp;
  btn.dataset.active = on ? "true" : "false";
  btn.setAttribute("aria-pressed", on ? "true" : "false");
  btn.classList.toggle("on", on);
  btn.classList.toggle("off", !on);
  btn.textContent = on ? "📍 自动滚动" : "⏸ 已暂停";
  btn.title = on
    ? "自动滚动到最新（手动向上滚会暂停）"
    : "点击恢复自动滚动到最新";
}

function scrollLlmPanelToBottom(options = {}) {
  // transcript 自由高度，滚到 transcript 末尾（=滚 window 到 transcript 底部）
  const transcript = $("llmTranscript");
  if (!transcript) return;
  if (options.force || state.llm.autoScroll) {
    const rect = transcript.getBoundingClientRect();
    const absoluteBottom = rect.top + window.scrollY + rect.height;
    window.scrollTo({ top: absoluteBottom, behavior: options.smooth ? "smooth" : "auto" });
  }
  toggleScrollBottomButton(false);
}

function toggleScrollBottomButton(show) {
  const btn = $("llmScrollBottomButton");
  if (btn) btn.hidden = !show;
}

function updateLlmRuntimeCounts() {
  const setCount = (key, value) => {
    const node = document.querySelector(`[data-runtime-count="${key}"]`);
    if (node) node.textContent = String(value);
  };
  // 回合流程 tab：合并 transcript + toolTrace
  setCount("transcript", state.llm.transcript.length + state.llm.toolTrace.length);
  setCount("tools", state.llm.toolTrace.length);
  setCount("events", state.llm.events.length);
}

/* ========== Bootstrap / Session ========== */

async function bootstrap() {
  const params = new URLSearchParams(window.location.search);
  const sessionId = params.get("session");
  if (sessionId) {
    await loadSession(sessionId);
  } else {
    await createSession();
  }
}

async function createSession() {
  const response = await fetch("/api/sessions", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: "{}",
  });
  let data;
  try {
    data = await readJsonOrThrow(response, "创建会话失败");
  } catch (error) {
    showError(error?.message || "创建会话失败");
    return;
  }
  setSession(data);
  const url = new URL(window.location.href);
  url.searchParams.set("session", state.sessionId);
  url.searchParams.delete("watch");
  window.history.replaceState(null, "", url);
}

async function loadSession(sessionId) {
  const response = await fetch(`/api/sessions/${sessionId}`);
  let data;
  try {
    data = await readJsonOrThrow(response, "读取会话失败");
  } catch (error) {
    showError(error?.message || "读取会话失败");
    return;
  }
  setSession(data);
}

function setSession(data) {
  state.sessionId = data.session_id;
  state.observation = data.observation;
  state.events = data.events || [];
  state.watchOnly = false;
  state.selectedHunts.clear();
  state.openDetails.clear();
  state.actionPending = false;
  connectSocket();
  render();
}

function connectSocket() {
  if (state.socket) {
    state.socket.close();
  }
  const protocol = window.location.protocol === "https:" ? "wss" : "ws";
  state.socket = new WebSocket(`${protocol}://${window.location.host}/ws/sessions/${state.sessionId}`);
  state.socket.addEventListener("message", (message) => {
    const data = JSON.parse(message.data);
    if (data.event) {
      mergeEvent(data.event);
    }
    if (data.observation) {
      state.observation = data.observation;
    }
    render();
  });
}

/* ========== Action Submission ========== */

async function submitAction(payload) {
  if (state.watchOnly || state.actionPending) {
    return;
  }
  state.actionPending = true;
  render();
  try {
    const response = await fetch(`/api/sessions/${state.sessionId}/actions`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await readActionResponse(response);
    if (data.event) {
      mergeEvent(data.event);
    }
    if (data.observation) {
      state.observation = data.observation;
    }
    if (!response.ok || data.__error) {
      showError(data.detail || data.error || "动作失败");
    }
  } catch (error) {
    showError(error?.message || "动作请求失败");
  } finally {
    state.actionPending = false;
    render();
  }
}

async function readActionResponse(response) {
  const contentType = response.headers.get("content-type") || "";
  if (contentType.includes("application/json")) {
    try {
      return await response.json();
    } catch {
      return { __error: true, detail: "动作响应不是有效 JSON" };
    }
  }
  const text = (await response.text()).trim();
  return {
    __error: true,
    detail: text || `动作失败 (HTTP ${response.status})`,
  };
}

/* ========== Render Dispatch ========== */

function render() {
  const obs = state.observation;
  if (!obs) {
    return;
  }
  $("sessionMeta").textContent = `会话 ${obs.session_id || state.sessionId || "未知"} · 回合 ${turnText(obs)}`;
  $("newSessionButton").disabled = state.watchOnly || state.actionPending;
  $("exportButton").disabled = !state.sessionId;
  updateLlmSeedPlaceholders(obs);
  renderOverview(obs);
  renderAdventurers(obs);
  renderRecruitment(obs);
  renderCrafting(obs);
  renderEquipment(obs);
  renderUpgrades(obs);
  renderActionTimeline();
  renderBattleLog();
  renderEvents();
  renderModalHunts();
  renderLlmDebug();
}

function mergeEvent(event) {
  if (state.events.some((item) => item.sequence === event.sequence)) {
    return;
  }
  state.events.push(event);
  state.events.sort((left, right) => left.sequence - right.sequence);
}

/* ========== Overview ========== */

function renderOverview(obs) {
  const count = state.selectedHunts.size;
  const countBadge = count > 0 ? `<span class="hunt-count">${count}</span>` : "";
  $("overview").innerHTML = `
    <div class="overview">
      ${metric("回合", turnText(obs), "turn")}
      ${metric("种子", seedText(obs), "seed")}
      ${metric("金币", obs.gold, "gold")}
      ${metric("经验池", obs.experience_pool, "exp")}
      ${metric("队伍", `${obs.party_size ?? obs.adventurers.length}/${obs.party_size_limit ?? obs.adventurers.length}`, "party")}
      ${metric("材料", materialsSummary(obs.materials), "mat", materialsText(obs.materials))}
      ${metric("状态", obs.finished ? "已结束" : "进行中", obs.finished ? "status finished" : "status")}
      <button class="combat-trigger" ${obs.finished || state.watchOnly ? "disabled" : ""} onclick="openCombatModal()">交战${countBadge}</button>
    </div>
  `;
}

function metric(label, value, type, title) {
  return `
    <div class="metric-card metric-${type}"${title ? ` title="${escapeHtml(title)}"` : ""}>
      <span class="metric-label">${label}</span>
      <span class="metric-value">${escapeHtml(String(value))}</span>
    </div>
  `;
}

function materialsSummary(materials) {
  const entries = Object.entries(materials || {});
  if (!entries.length) return "无";
  const total = entries.reduce((sum, [, value]) => sum + Number(value || 0), 0);
  return `${entries.length} 种 · ${total}`;
}

function seedText(obs) {
  const scoringSeed = obs.scoring?.seed;
  if (scoringSeed === undefined || scoringSeed === null) {
    return obs.seed ?? "无";
  }
  return `${obs.seed ?? "无"} / ${scoringSeed}`;
}

function turnText(obs) {
  const turn = Number(obs.turn);
  const maxTurns = Number(obs.max_turns);
  if (!Number.isFinite(turn) || !Number.isFinite(maxTurns)) {
    return `${obs.turn ?? "?"}/${obs.max_turns ?? "?"}`;
  }
  return `${Math.min(turn, maxTurns)}/${maxTurns}`;
}

function updateLlmSeedPlaceholders(obs) {
  const gameSeedInput = $("llmGameSeed");
  const scoringSeedInput = $("llmScoringSeed");
  if (gameSeedInput) {
    gameSeedInput.placeholder = `默认 ${obs.seed ?? "game.yaml"}`;
  }
  if (scoringSeedInput) {
    scoringSeedInput.placeholder = `默认 ${obs.scoring?.seed ?? "scoring.seed"}`;
  }
}

/* ========== Adventurers ========== */

function renderAdventurers(obs) {
  if (!obs.adventurers.length) {
    $("adventurers").innerHTML = `
      <div class="empty-hint">
        <div class="empty-hint-icon">⚔️</div>
        <div class="empty-hint-title">队伍还是空的</div>
        <div class="empty-hint-text">从右侧 <strong>招募</strong> 面板里选一个冒险者开始</div>
      </div>
    `;
    return;
  }
  $("adventurers").innerHTML = list(obs.adventurers.map((adventurer, index) => {
    const isDead = adventurer.resources.current_hp <= 0;
    const isOpen = state.openDetails.has(adventurer.adventurer_id) || (state.openDetails.size === 0 && index === 0);
    const meta = classMeta(adventurer.template_id);
    return `
      <div class="adv-card ${isDead ? "adv-dead" : ""}" style="--class-color:${meta.color}">
        <div class="adv-header">
          <div class="adv-identity">
            ${classPortraitHtml(adventurer.template_id, adventurer.name)}
            <div class="adv-titles">
              <div class="adv-title-row">
                <strong class="adv-name">${escapeHtml(adventurer.name)}</strong>
                <span class="badge">Lv.${adventurer.level}</span>
                ${isDead ? '<span class="badge badge-danger">阵亡</span>' : ""}
              </div>
              <div class="adv-class-line">
                <span class="class-chip" title="${escapeHtml(meta.role)}">${escapeHtml(meta.name)}</span>
                <span class="small muted">${escapeHtml(levelText(adventurer))}</span>
              </div>
            </div>
          </div>
          <div class="inline compact">
            <button type="button" class="btn-danger compact" ${disabled()} onclick="dismissAdventurer('${adventurer.adventurer_id}')">解散</button>
          </div>
        </div>
        <div class="bar-row">
          ${hpBar(adventurer.resources.current_hp, adventurer.effective_stats.hp)}
          ${mpBar(adventurer.resources.current_mp, adventurer.effective_stats.mp)}
        </div>
        <details ${isOpen ? "open" : ""} data-adv="${adventurer.adventurer_id}">
          <summary class="adv-summary">属性 · 技能 · 装备 · 升级</summary>
          <div class="adv-body">
            <div class="adv-section">
              <div class="adv-section-label">属性</div>
              ${statGrid(adventurer.base_stats, adventurer.effective_stats)}
            </div>
            <div class="adv-section">
              <div class="adv-section-label">技能</div>
              ${adventurer.skills?.length ? skillList(adventurer.skills, "") : '<div class="small muted">无</div>'}
              ${levelSkillUnlocksBlock(adventurer)}
            </div>
            <div class="adv-section">
              <div class="adv-section-label">装备</div>
              <div class="slot-grid">
                ${adventurer.equipment_slots.map((slot) => equipmentSlotCell(adventurer, slot)).join("")}
              </div>
            </div>
            <div class="adv-section">
              <div class="adv-section-label">升级</div>
              ${experienceBlock(obs, adventurer)}
            </div>
          </div>
        </details>
      </div>
    `;
  }));
  document.querySelectorAll("[data-adv]").forEach((el) => {
    el.addEventListener("toggle", () => {
      if (el.open) {
        state.openDetails.add(el.dataset.adv);
      } else {
        state.openDetails.delete(el.dataset.adv);
      }
    });
  });
}

/* ========== Recruitment ========== */

function renderRecruitment(obs) {
  const candidates = obs.recruit_candidates || [];
  $("recruitmentLimit").textContent = `队伍 ${obs.party_size ?? obs.adventurers.length}/${obs.party_size_limit ?? obs.adventurers.length}`;
  if (!candidates.length) {
    $("recruitment").innerHTML = `
      <div class="empty-hint">
        <div class="empty-hint-text">本回合没有可招募的冒险者</div>
      </div>
    `;
    return;
  }
  $("recruitment").innerHTML = list(candidates.map((candidate) => {
    const meta = classMeta(candidate.template_id);
    return `
    <div class="row recruit-row" style="--class-color:${meta.color}">
      <div class="row-title recruit-head">
        ${classPortraitHtml(candidate.template_id, candidate.name)}
        <div class="recruit-titles">
          <div class="recruit-title-row">
            <strong>${escapeHtml(candidate.name)}</strong>
            <span class="class-chip" title="${escapeHtml(meta.role)}">${escapeHtml(meta.name)}</span>
          </div>
          <div class="small recruit-meta">
            <span class="recruit-cost">💰 ${candidate.recruit_gold}</span>
            <span class="${candidate.can_recruit ? "ok" : "danger"}">
              ${candidate.can_recruit ? "可招募" : "暂不可招募"}
            </span>
          </div>
        </div>
      </div>
      ${candidateStats(candidate.base_stats)}
      <div class="recruit-section">
        <div class="recruit-section-label">属性成长</div>
        <div class="small muted">${statModifierText(candidate.stat_growth_per_level)}</div>
      </div>
      ${candidate.skills?.length ? `
        <div class="recruit-section">
          <div class="recruit-section-label">初始技能</div>
          <div class="skill-list inline-list">${candidate.skills.map((s) => skillTag(s)).join("")}</div>
        </div>
      ` : ""}
      ${candidateLevelUnlocks(candidate)}
      ${candidate.can_recruit ? "" : `<div class="small danger recruit-missing">缺少：${missingText(candidate.missing)}</div>`}
      <div class="recruit-action">
        <button type="button" class="btn-primary" ${disabled(!candidate.can_recruit)} onclick="recruit('${candidate.candidate_id}')">招募</button>
      </div>
    </div>
  `;
  }));
}

function candidateStats(stats) {
  return `
    <div class="stat-tiles stat-tiles-vitals">
      <div class="stat-tile stat-tile-hp"><span class="stat-tile-label">HP</span><span class="stat-tile-val">${stats.hp}</span></div>
      <div class="stat-tile stat-tile-mp"><span class="stat-tile-label">MP</span><span class="stat-tile-val">${stats.mp}</span></div>
    </div>
    <div class="stat-tiles stat-tiles-combat">
      <div class="stat-tile stat-tile-atk"><span class="stat-tile-label">攻</span><span class="stat-tile-val">${stats.attack}</span></div>
      <div class="stat-tile stat-tile-def"><span class="stat-tile-label">防</span><span class="stat-tile-val">${stats.defense}</span></div>
      <div class="stat-tile stat-tile-spd"><span class="stat-tile-label">速</span><span class="stat-tile-val">${stats.speed}</span></div>
      <div class="stat-tile stat-tile-rec"><span class="stat-tile-label">回血</span><span class="stat-tile-val">${stats.recovery}</span></div>
      <div class="stat-tile stat-tile-mrec"><span class="stat-tile-label">回魔</span><span class="stat-tile-val">${stats.mp_recovery ?? 0}</span></div>
    </div>
  `;
}

function candidateLevelUnlocks(candidate) {
  const unlocks = candidate.level_skill_unlocks || [];
  if (!unlocks.length) {
    return "";
  }
  return `
    <div class="skill-unlocks">
      <span class="skill-unlocks-label">升级解锁</span>
      ${unlocks.map((unlock) => {
        const skillTags = (unlock.skills || []).map((s) => skillTag(s)).join(" ");
        return `<span class="skill-unlock">Lv.${unlock.level}${skillTags}</span>`;
      }).join("")}
    </div>
  `;
}

/* ========== Crafting ========== */

function renderCrafting(obs) {
  const recipes = obs.crafting_recipes || [];
  if (!recipes.length) {
    $("crafting").innerHTML = `<div class="empty-hint"><div class="empty-hint-text">暂无可合成配方</div></div>`;
    return;
  }
  // 按产物槽位分组
  const groups = new Map();
  for (const recipe of recipes) {
    const key = recipe.output_slot || "other";
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(recipe);
  }
  // 槽位显示顺序
  const order = ["main_hand", "two_hand", "off_hand", "armor", "helmet", "boots", "accessory", "other"];
  const sortedKeys = [...groups.keys()].sort((a, b) => {
    const ai = order.indexOf(a); const bi = order.indexOf(b);
    return (ai === -1 ? 99 : ai) - (bi === -1 ? 99 : bi);
  });
  const html = sortedKeys.map((key) => {
    const items = groups.get(key);
    const label = `${slotIcon(key)} ${slotName(key)}`;
    return renderGroupSection({
      id: `craft-${key}`,
      title: label,
      count: items.length,
      summary: craftingGroupSummary(items),
      defaultOpen: sortedKeys[0] === key,
      items: items.map((recipe) => craftRowHtml(recipe)),
    });
  }).join("");
  $("crafting").innerHTML = html;
}

function craftingGroupSummary(items) {
  const ready = items.filter((r) => r.can_craft).length;
  if (!ready) return `<span class="group-bad">资源不足 ${items.length}</span>`;
  if (ready === items.length) return `<span class="group-ok">可合成 ${ready}</span>`;
  return `<span class="group-mix">可合成 ${ready} / ${items.length}</span>`;
}

function craftRowHtml(recipe) {
  return `
    <div class="row craft-row">
      <div class="row-title">
        <span class="slot-icon">${slotIcon(recipe.output_slot)}</span>
        <strong>${escapeHtml(recipe.name)}</strong>
        <span class="${recipe.can_craft ? "ok" : "danger"} small">${recipe.can_craft ? "可合成" : "资源不足"}</span>
      </div>
      <div class="small muted">${escapeHtml(recipe.output_name)}${recipe.output_allowed_class_names?.length ? ` · 限制: ${recipe.output_allowed_class_names.join("、")}` : ""}</div>
      <div class="small">产物：${statModifierText(recipe.output_stats)}</div>
      ${recipe.output_skills?.length ? skillList(recipe.output_skills, "") : ""}
      <div class="small">消耗：金币 ${recipe.gold_cost} · ${materialsText(recipe.material_costs)}</div>
      ${recipe.can_craft ? "" : `<div class="small danger">缺少：${missingText(recipe.missing)}</div>`}
      <div class="craft-action">
        <button type="button" class="btn-primary" ${disabled(!recipe.can_craft)} onclick="craft('${recipe.recipe_id}')">合成</button>
      </div>
    </div>
  `;
}

/* ========== Equipment (read-only in workshop) ========== */

function renderEquipment(obs) {
  const inventory = obs.equipment_inventory || [];
  if (!inventory.length) {
    $("equipment").innerHTML = `<div class="empty-hint"><div class="empty-hint-text">暂未持有装备</div></div>`;
    return;
  }
  // 按槽位分组
  const groups = new Map();
  for (const item of inventory) {
    const key = item.slot || "other";
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(item);
  }
  const order = ["main_hand", "two_hand", "off_hand", "armor", "helmet", "boots", "accessory", "other"];
  const sortedKeys = [...groups.keys()].sort((a, b) => {
    const ai = order.indexOf(a); const bi = order.indexOf(b);
    return (ai === -1 ? 99 : ai) - (bi === -1 ? 99 : bi);
  });
  const html = sortedKeys.map((key) => {
    const items = groups.get(key);
    const equippedCount = items.filter((i) => i.equipped_by).length;
    const summary = equippedCount > 0
      ? `<span class="group-mix">装备中 ${equippedCount} / ${items.length}</span>`
      : `<span class="group-state-muted">闲置 ${items.length}</span>`;
    return renderGroupSection({
      id: `equip-${key}`,
      title: `${slotIcon(key)} ${slotName(key)}`,
      count: items.length,
      summary,
      defaultOpen: sortedKeys[0] === key,
      items: items.map(equipRowHtml),
    });
  }).join("");
  $("equipment").innerHTML = html;
}

function equipRowHtml(item) {
  const equippedInfo = item.equipped_by
    ? resolveName(item.equipped_by)
    : "未装备";
  const classInfo = item.allowed_class_names?.length
    ? ` · 限制: ${item.allowed_class_names.join("、")}`
    : "";
  return `
    <div class="row equip-row">
      <div class="row-title">
        <span class="slot-icon">${slotIcon(item.slot)}</span>
        <strong>${escapeHtml(item.name)}</strong>
        <span class="small ${item.equipped_by ? "equip-equipped" : "muted"}">${equippedInfo}</span>
      </div>
      <div class="small muted">${statModifierText(item.stats)}${classInfo}</div>
    </div>
  `;
}

/* ========== Upgrades ========== */

function renderUpgrades(obs) {
  const upgrades = obs.global_upgrades || [];
  if (!upgrades.length) {
    $("upgrades").innerHTML = `<div class="empty-hint"><div class="empty-hint-text">暂无全局加成</div></div>`;
    return;
  }
  // 按主要功能分组：队伍 > 恢复 > 战斗
  const groups = new Map();
  for (const upgrade of upgrades) {
    const key = upgradeGroupKey(upgrade);
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(upgrade);
  }
  const order = ["party", "recovery", "combat", "other"];
  const sortedKeys = [...groups.keys()].sort((a, b) => {
    const ai = order.indexOf(a); const bi = order.indexOf(b);
    return (ai === -1 ? 99 : ai) - (bi === -1 ? 99 : bi);
  });
  const labels = {
    party:   { icon: "👥", text: "队伍扩张" },
    recovery: { icon: "💚", text: "恢复支援" },
    combat:  { icon: "⚔️", text: "战斗强化" },
    other:   { icon: "✨", text: "其他" },
  };
  const html = sortedKeys.map((key) => {
    const items = groups.get(key);
    const meta = labels[key] || labels.other;
    return renderGroupSection({
      id: `upgrade-${key}`,
      title: `${meta.icon} ${meta.text}`,
      count: items.length,
      summary: upgradeGroupSummary(items),
      defaultOpen: sortedKeys[0] === key,
      items: items.map((upgrade) => upgradeRowHtml(obs, upgrade)),
    });
  }).join("");
  $("upgrades").innerHTML = html;
}

function upgradeGroupKey(upgrade) {
  if ((upgrade.party_size_bonus || 0) > 0) return "party";
  const s = upgrade.stats || {};
  if ((s.recovery || 0) > 0 || (s.mp_recovery || 0) > 0 || (s.mp || 0) > 0) return "recovery";
  if ((s.attack || 0) > 0 || (s.defense || 0) > 0 || (s.speed || 0) > 0 || (s.hp || 0) > 0) return "combat";
  return "other";
}

function upgradeGroupSummary(items) {
  const unlocked = items.filter((u) => u.unlocked).length;
  const purchasable = items.filter((u) => !u.unlocked && u.can_purchase).length;
  if (unlocked === items.length) return `<span class="group-ok">已全部解锁</span>`;
  if (purchasable > 0) return `<span class="group-mix">可购买 ${purchasable} / ${items.length}</span>`;
  return `<span class="group-bad">${items.length} 项待解锁</span>`;
}

function upgradeRowHtml(obs, upgrade) {
  const state = upgrade.unlocked ? "已解锁" : upgrade.can_purchase ? "可购买" : "不可购买";
  const stateClass = upgrade.unlocked || upgrade.can_purchase ? "ok" : "danger";
  return `
    <div class="row upgrade-row">
      <div class="row-title">
        <strong>${escapeHtml(upgrade.name)}</strong>
        <span class="${stateClass} small">${state}</span>
      </div>
      <div class="small">金币 ${upgrade.gold_cost} · ${statModifierText(upgrade.stats)}${upgrade.party_size_bonus ? ` · 队伍上限 +${upgrade.party_size_bonus}` : ""}</div>
      <div class="small muted">前置：${upgradePrereqText(obs, upgrade.required_upgrade_ids)}</div>
      ${!upgrade.unlocked && !upgrade.can_purchase ? `<div class="small danger upgrade-missing">缺少：${missingText(upgrade.missing)}</div>` : ""}
      ${upgrade.unlocked ? "" : `<div class="upgrade-action"><button type="button" class="btn-primary" ${disabled(!upgrade.can_purchase)} onclick="purchaseUpgrade('${upgrade.upgrade_id}')">购买</button></div>`}
    </div>
  `;
}

function renderGroupSection({ id, title, count, summary, items, defaultOpen = false }) {
  // title 形如 "⚔ 右手" 或 "👥 队伍扩张"，把第一个 token 当图标
  const m = String(title).match(/^(\S+)\s+(.+)$/);
  const titleHtml = m
    ? `<span class="group-title"><span class="group-title-icon">${m[1]}</span>${escapeHtml(m[2])}</span>`
    : `<span class="group-title">${escapeHtml(title)}</span>`;
  return `
    <details class="group-section" id="${escapeHtml(id)}" ${defaultOpen ? "open" : ""}>
      <summary class="group-summary">
        <span class="group-toggle">▶</span>
        ${titleHtml}
        <span class="group-count">${count}</span>
        <span class="group-state">${summary || ""}</span>
      </summary>
      <div class="group-body">${items.join("")}</div>
    </details>
  `;
}

/* ========== Combat Modal ========== */

function openCombatModal() {
  $("combatModal").hidden = false;
}

function closeCombatModal() {
  $("combatModal").hidden = true;
}

function openModelPromptModal(modelId) {
  const entry = findModelEntry(modelId);
  if (!entry?.request) {
    showToast("该次调用没有记录输入 prompt");
    return;
  }
  $("modelPromptMeta").textContent = `T${entry.turn} · step ${entry.step}`;
  $("modelPromptContent").textContent = formatModelRequest(entry.request);
  $("modelPromptModal").hidden = false;
}

function closeModelPromptModal() {
  $("modelPromptModal").hidden = true;
}

function findModelEntry(modelId) {
  return state.llm.transcript.find((entry) => entry.kind === "model" && entry.id === modelId) || null;
}

function formatModelRequest(request) {
  return JSON.stringify(request, null, 2);
}

function renderModalHunts() {
  const obs = state.observation;
  if (!obs) return;

  const aliveAdventurers = obs.adventurers.filter((a) => a.resources.current_hp > 0);
  const aliveAdventurerIds = new Set(aliveAdventurers.map((a) => a.adventurer_id));
  const validMonsterIds = new Set(obs.monsters.map((m) => m.monster_id));
  for (const [monsterId, adventurerId] of Array.from(state.selectedHunts.entries())) {
    if (!validMonsterIds.has(monsterId) || !aliveAdventurerIds.has(adventurerId)) {
      state.selectedHunts.delete(monsterId);
    }
  }

  const body = $("modalHunts");
  if (!body) return;

  body.innerHTML = obs.monsters.map((monster) => {
    const selectedAdv = state.selectedHunts.get(monster.monster_id);
    const adventurer = selectedAdv ? obs.adventurers.find((a) => a.adventurer_id === selectedAdv) : null;
    const previewPlaceholder = adventurer
      ? `<div class="hunt-preview" id="preview-${monster.monster_id}"><span class="muted">计算中…</span></div>`
      : "";
    const tier = tierMeta(monster.tier);
    return `
      <div class="hunt-entry" style="--tier-color:${tier.color}">
        ${monsterPortraitHtml(monster)}
        <div class="hunt-info">
          <div class="hunt-title-row">
            <strong>${escapeHtml(monster.name)}</strong>
            <span class="tier-chip">${escapeHtml(tier.name)}</span>
          </div>
          <div class="stat-tiles hunt-stats">
            <div class="stat-tile"><span class="stat-tile-label">HP</span><span class="stat-tile-val">${monster.stats.hp}</span></div>
            <div class="stat-tile"><span class="stat-tile-label">MP</span><span class="stat-tile-val">${monster.stats.mp}</span></div>
            <div class="stat-tile stat-tile-atk"><span class="stat-tile-label">⚔ 攻</span><span class="stat-tile-val">${monster.stats.attack}</span></div>
            <div class="stat-tile stat-tile-def"><span class="stat-tile-label">🛡 防</span><span class="stat-tile-val">${monster.stats.defense}</span></div>
            <div class="stat-tile stat-tile-spd"><span class="stat-tile-label">⚡ 速</span><span class="stat-tile-val">${monster.stats.speed}</span></div>
          </div>
          <div class="small muted">奖励：${rewardText(monster.reward)}</div>
          ${skillList(monster.skills)}
        </div>
        <select ${disabled()} onchange="selectHunt('${monster.monster_id}', this.value)">
          <option value="">不交战</option>
          ${aliveAdventurers.map((a) => `
            <option value="${a.adventurer_id}" ${selectedAdv === a.adventurer_id ? "selected" : ""}>
              ${escapeHtml(a.name)}
            </option>
          `).join("")}
        </select>
      </div>
      ${previewPlaceholder}
    `;
  }).join("");

  const endBtn = $("modalEndTurn");
  if (endBtn) {
    endBtn.disabled = state.watchOnly || obs.finished;
  }

  // Fetch accurate previews from backend
  fetchBattlePreviews();
}

async function fetchBattlePreviews() {
  const sessionId = state.sessionId;
  if (!sessionId) return;
  const obs = state.observation;
  if (!obs) return;

  const tasks = [];
  for (const [monsterId, adventurerId] of state.selectedHunts.entries()) {
    const el = $(`preview-${monsterId}`);
    if (!el) continue;
    const adventurer = obs.adventurers.find((a) => a.adventurer_id === adventurerId);
    if (!adventurer) continue;
    tasks.push(
      (async () => {
        try {
          const resp = await fetch(`/api/sessions/${sessionId}/preview-battle`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ adventurer_id: adventurerId, monster_id: monsterId }),
          });
          if (!resp.ok) {
            el.innerHTML = `<span class="muted">预览不可用</span>`;
            return;
          }
          const sim = await resp.json();
          const afterHp = sim.adventurer_after_resources.current_hp;
          const afterMp = sim.adventurer_after_resources.current_mp;
          const maxHp = adventurer.effective_stats.hp;
          const maxMp = adventurer.effective_stats.mp;
          const hpPct = Math.round((afterHp / maxHp) * 100);
          const won = sim.won;
          const cls = won ? "ok" : "danger";
          const label = won ? "胜利" : "失败";
          const hpColor = hpPct > 60 ? "" : hpPct > 30 ? "hp-warn-text" : "danger";
          const monster = obs.monsters.find((m) => m.monster_id === monsterId);
          const monMaxHp = monster ? monster.stats.hp : "?";
          const monHpAfter = won ? 0 : "存活";
          const dmgTaken = adventurer.resources.current_hp - afterHp;
          const mpUsed = adventurer.resources.current_mp - afterMp;
          const reward = won && monster ? rewardText(monster.reward) : "";
          el.innerHTML = `
            <span class="preview-result ${cls}">${label}</span>
            <span class="preview-hp ${hpColor}">${escapeHtml(adventurer.name)} HP ${afterHp}/${maxHp}${dmgTaken > 0 ? ` (-${dmgTaken})` : ""}${mpUsed > 0 ? ` · MP ${afterMp}/${maxMp} (-${mpUsed})` : ""}</span>
            <span class="preview-detail">怪物 HP ${monMaxHp} → ${monHpAfter} · ${sim.combat.actions_taken} 次行动 · ${sim.combat.time_elapsed} 时序</span>
            ${reward ? `<span class="preview-reward">奖励: ${reward}</span>` : ""}
          `;
        } catch {
          el.innerHTML = `<span class="muted">预览失败</span>`;
        }
      })()
    );
  }
  await Promise.all(tasks);
}

/* ========== Equip Popup ========== */

function openEquipPopup(adventurerId, slotType, element) {
  if (state.watchOnly || state.observation?.finished) return;

  const obs = state.observation;
  const available = obs.equipment_inventory.filter(
    (item) => item.slot === slotType && !item.equipped_by
  );

  const popup = $("equipPopup");

  if (!available.length) {
    popup.innerHTML = '<div class="popup-empty">无可用装备</div>';
  } else {
    popup.innerHTML = available.map((item) => `
      <div class="popup-item" onclick="equipFromPopup('${adventurerId}', '${item.instance_id}')">
        <strong>${escapeHtml(item.name)}</strong>
        <div class="small muted">${statModifierText(item.stats)}</div>
      </div>
    `).join("");
  }

  const rect = element.getBoundingClientRect();
  popup.style.top = (rect.bottom + 4) + "px";
  popup.style.left = Math.min(rect.left, window.innerWidth - 290) + "px";
  popup.hidden = false;
}

function equipFromPopup(adventurerId, instanceId) {
  closeEquipPopup();
  equip(adventurerId, instanceId);
}

function closeEquipPopup() {
  $("equipPopup").hidden = true;
}

function onDocumentClick(e) {
  const promptButton = e.target.closest?.(".model-prompt-button");
  if (promptButton) {
    openModelPromptModal(promptButton.dataset.modelId);
    return;
  }

  const thinkingSummary = e.target.closest?.(".llm-thinking > summary");
  if (thinkingSummary) {
    e.preventDefault();
    const details = thinkingSummary.closest(".llm-thinking");
    if (details) {
      setThinkingOpen(details, !details.open);
    }
    return;
  }

  const popup = $("equipPopup");
  if (!popup.hidden && !popup.contains(e.target) && !e.target.closest(".slot-empty")) {
    closeEquipPopup();
  }
}

function onDocumentToggle(e) {
  if (e.target.classList?.contains("llm-thinking")) {
    syncThinkingOpenState(e.target);
  }

  if (e.target.classList?.contains("llm-tool-item")) {
    const toolId = e.target.dataset.toolId;
    if (!toolId) {
      return;
    }
    if (e.target.open) {
      state.llm.openToolTrace.add(toolId);
    } else {
      state.llm.openToolTrace.delete(toolId);
    }
  }

  if (e.target.classList?.contains("llm-turn-block")) {
    const turnAttr = e.target.dataset.turn;
    const turn = turnAttr != null ? Number(turnAttr) : null;
    if (turn == null || Number.isNaN(turn)) return;
    if (e.target.open) {
      state.llm.openTurns.add(turn);
      state.llm.userCollapsedTurns.delete(turn);
    } else {
      state.llm.openTurns.delete(turn);
      state.llm.userCollapsedTurns.add(turn);
    }
  }
}

function setThinkingOpen(details, open) {
  details.open = open;
  syncThinkingOpenState(details);
}

function syncThinkingOpenState(details) {
  const entryId = details.dataset.entryId;
  if (!entryId) {
    return;
  }
  if (details.open) {
    state.llm.openThinking.add(entryId);
  } else {
    state.llm.openThinking.delete(entryId);
  }
}

/* ========== Timeline ========== */

function renderActionTimeline() {
  const events = state.events.filter((event) => event.type !== "session_started");
  $("actionTimeline").innerHTML = list(events.slice(-80).reverse().map((event) => {
    const changes = event.payload?.changes || [];
    return `
      <div class="timeline-item ${event.type === "action_rejected" ? "rejected" : ""}">
        <div class="row-title">
          <strong>#${event.sequence} · T${event.turn}</strong>
          <span>${escapeHtml(eventTypeName(event.type))}</span>
        </div>
        <div>${escapeHtml(event.payload?.summary || event.type)}</div>
        ${event.payload?.error ? `<div class="small danger">${escapeHtml(event.payload.error)}</div>` : ""}
        ${changes.length ? `<div class="change-list">${changes.slice(0, 12).map(changeText).join("")}</div>` : ""}
      </div>
    `;
  }));
}

/* ========== Battle Log ========== */

function renderBattleLog() {
  const battleEvents = state.events.filter((event) => event.type === "turn_ended" && event.payload?.battles?.length);
  $("battleLog").innerHTML = list(battleEvents.slice(-30).reverse().flatMap((event) => (
    event.payload.battles.map((battle, index) => battleBlock(event, battle, index))
  )));
}

function battleBlock(event, battle, index) {
  const reward = rewardText(battle.reward);
  return `
    <details class="battle-item" ${index === 0 ? "open" : ""}>
      <summary>
        <span>T${event.turn} · ${escapeHtml(battle.adventurer_name)} vs ${escapeHtml(battle.monster_name)}</span>
        <strong class="${battle.won ? "ok" : "danger"}">${battle.won ? "胜利" : "失败"}</strong>
      </summary>
      <div class="battle-summary">
        <div>${escapeHtml(battle.adventurer_name)} HP ${battle.adventurer_before_resources.current_hp} -> ${battle.adventurer_after_resources.current_hp}，MP ${battle.adventurer_before_resources.current_mp} -> ${battle.adventurer_after_resources.current_mp}</div>
        <div>奖励：${reward}</div>
        <div>行动 ${battle.combat.actions_taken} 次，耗时 ${battle.combat.time_elapsed}</div>
      </div>
      <div class="combat-events">
        ${battle.combat.events.map(combatEventText).join("") || `<div class="small muted">无行动记录</div>`}
      </div>
    </details>
  `;
}

function combatEventText(event) {
  let action, target;
  if (event.action_type === "status") {
    action = `受到 ${event.status_name || "状态"} 效果`;
    target = resolveName(event.target_id);
  } else if (event.action_type === "skill") {
    action = `使用 ${event.skill_name || event.skill_id}`;
    target = resolveName(event.target_id);
  } else {
    action = "普通攻击";
    target = resolveName(event.target_id);
  }
  const healing = event.healing > 0
    ? `，治疗 ${event.healing}，目标 HP ${event.healing_target_hp}`
    : "";
  const actor = resolveName(event.actor_id);
  return `
    <div class="combat-line">
      <span>#${event.action_index} t=${event.time_elapsed}</span>
      <span>${escapeHtml(actor)} ${escapeHtml(action)}，造成 ${event.damage} 伤害，${escapeHtml(target)} HP ${event.target_hp}${healing}</span>
    </div>
  `;
}

/* ========== Events ========== */

function renderEvents() {
  $("events").innerHTML = state.events.slice(-80).reverse().map((event) => `
    <div class="event-line">
      <div><strong>#${event.sequence} T${event.turn} ${escapeHtml(eventTypeName(event.type))}</strong></div>
      <div>${escapeHtml(event.payload?.summary || "")}</div>
      <details>
        <summary class="small muted">原始数据</summary>
        <pre>${escapeHtml(JSON.stringify(event.payload, null, 2))}</pre>
      </details>
    </div>
  `).join("");
}

/* ========== Action Handlers ========== */

function craft(recipeId) {
  submitAction({ type: "craft", recipe_id: recipeId });
}

function purchaseUpgrade(upgradeId) {
  submitAction({ type: "purchase_upgrade", upgrade_id: upgradeId });
}

function allocateExperience(adventurerId) {
  const amount = readExperienceAmount(adventurerId);
  if (amount <= 0) {
    showError("请输入要分配的经验值");
    return;
  }
  submitAction({ type: "allocate_experience", adventurer_id: adventurerId, amount });
}

function dismissAdventurer(adventurerId) {
  const adventurer = state.observation?.adventurers.find((item) => item.adventurer_id === adventurerId);
  const name = adventurer?.name || adventurerId;
  if (!window.confirm(`确定解散 ${name}？`)) {
    return;
  }
  submitAction({ type: "dismiss", adventurer_id: adventurerId });
}

function recruit(candidateId) {
  submitAction({ type: "recruit", candidate_id: candidateId });
}

function equip(adventurerId, instanceId) {
  submitAction({ type: "equip", adventurer_id: adventurerId, equipment_instance_id: instanceId });
}

function unequip(adventurerId, slot) {
  submitAction({ type: "unequip", adventurer_id: adventurerId, slot });
}

function selectHunt(monsterId, adventurerId) {
  if (!adventurerId) {
    state.selectedHunts.delete(monsterId);
  } else {
    for (const [selectedMonsterId, selectedAdventurerId] of state.selectedHunts.entries()) {
      if (selectedAdventurerId === adventurerId && selectedMonsterId !== monsterId) {
        state.selectedHunts.delete(selectedMonsterId);
      }
    }
    state.selectedHunts.set(monsterId, adventurerId);
  }
  renderOverview(state.observation);
  renderModalHunts();
}

function endTurn() {
  const hunts = Array.from(state.selectedHunts.entries()).map(([monsterId, adventurerId]) => ({
    adventurer_id: adventurerId,
    monster_id: monsterId,
  }));
  state.selectedHunts.clear();
  closeCombatModal();
  submitAction({ type: "end_turn", hunts });
}

async function exportSession() {
  if (!state.sessionId) return;
  const btn = $("exportButton");
  btn.disabled = true;
  btn.textContent = "⏳ 保存中…";
  try {
    const resp = await fetch(`/api/sessions/${state.sessionId}/export`);
    if (!resp.ok) throw new Error("导出失败");
    const data = await resp.json();
    const json = JSON.stringify(data, null, 2);
    const blob = new Blob([json], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `manual-${data.session_id || state.sessionId}.json`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
    btn.textContent = "✅ 已保存";
    setTimeout(() => { btn.textContent = "💾 保存"; }, 2000);
  } catch (e) {
    console.error(e);
    btn.textContent = "❌ 失败";
    setTimeout(() => { btn.textContent = "💾 保存"; }, 2000);
  } finally {
    btn.disabled = !state.sessionId;
  }
}

function importSession(event) {
  const file = event.target.files?.[0];
  if (!file) return;
  event.target.value = "";
  const reader = new FileReader();
  reader.onload = async () => {
    try {
      const data = JSON.parse(reader.result);
      if (!data.final_observation && !data.observation && !data._state) {
        showError("无效的存档文件：缺少游戏状态");
        return;
      }

      // Try server-side restore for full playability
      try {
        const resp = await fetch("/api/sessions/restore", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: reader.result,
        });
        if (resp.ok) {
          const result = await resp.json();
          setSession(result);
          const url = new URL(window.location.href);
          url.searchParams.set("session", state.sessionId);
          url.searchParams.delete("watch");
          window.history.replaceState(null, "", url);
          showToast("已恢复存档，可继续操作");
          return;
        }
        const errText = `(HTTP ${resp.status})`;
        showToast("服务端恢复失败" + errText + "，尝试只读加载");
      } catch {
        showToast("无法连接服务端，尝试只读加载");
      }

      // Fallback: client-side read-only
      const obs = data.final_observation || data.observation;
      state.sessionId = data.session_id || null;
      state.observation = obs;
      state.events = data.events || [];
      state.watchOnly = true;
      state.selectedHunts.clear();
      $("sessionMeta").textContent = `📂 存档 ${state.sessionId || "?"} · 回合 ${turnText(obs)}${obs.finished ? " · 已结束" : ""}`;
      $("newSessionButton").disabled = false;
      $("exportButton").disabled = true;
      renderOverview(obs);
      renderAdventurers(obs);
      renderRecruitment(obs);
      renderCrafting(obs);
      renderEquipment(obs);
      renderUpgrades(obs);
      renderActionTimeline();
      renderBattleLog();
      renderEvents();
      const score = data.score;
      const stats = data.stats?.game_actions;
      const parts = [];
      if (score?.rank_score != null) parts.push(`段位分: ${Math.round(score.rank_score)}`);
      if (stats) {
        if (stats.battles_total) parts.push(`战斗: ${stats.battles_won}胜/${stats.battles_total}场`);
        if (stats.total_gold_earned) parts.push(`收入: 💰${stats.total_gold_earned}`);
      }
      if (parts.length) {
        $("sessionMeta").textContent += ` · ${parts.join(" · ")}`;
      }
      setMode("manual", false);
      showToast("已加载存档（只读，无服务端状态数据）");
    } catch (e) {
      showError("读取存档失败: " + (e.message || e));
    }
  };
  reader.readAsText(file);
}

/* ========== HP / MP Bars ========== */

function hpBar(current, max) {
  const pct = max > 0 ? (current / max) * 100 : 0;
  const cls = pct > 60 ? "" : pct > 30 ? "hp-warn" : "hp-danger";
  return `
    <div class="bar hp-bar ${cls}">
      <span class="bar-fill" style="width: ${pct}%"></span>
      <span class="bar-text">HP ${current}/${max}</span>
    </div>
  `;
}

function mpBar(current, max) {
  const pct = max > 0 ? (current / max) * 100 : 0;
  return `
    <div class="bar mp-bar">
      <span class="bar-fill" style="width: ${pct}%"></span>
      <span class="bar-text">MP ${current}/${max}</span>
    </div>
  `;
}

/* ========== Stat Grid ========== */

function statGrid(baseStats, effectiveStats) {
  return `
    <div class="stat-grid">
      ${statRow("攻击", baseStats.attack, effectiveStats.attack, "atk")}
      ${statRow("防御", baseStats.defense, effectiveStats.defense, "def")}
      ${statRow("速度", baseStats.speed, effectiveStats.speed, "spd")}
      ${statRow("回血", baseStats.recovery, effectiveStats.recovery, "rec")}
      ${statRow("回魔", baseStats.mp_recovery, effectiveStats.mp_recovery, "mrec")}
    </div>
  `;
}

function statRow(label, base, effective, type) {
  const diff = effective - base;
  const bonus = diff > 0 ? ` <span class="stat-bonus">+${diff}</span>` : "";
  return `<div class="stat-row stat-${type}"><span class="stat-label">${label}</span><span class="stat-val">${effective}${bonus}</span></div>`;
}

/* ========== Helpers ========== */

function list(items, emptyHint) {
  if (items.length) return `<div class="list">${items.join("")}</div>`;
  if (emptyHint) {
    return `<div class="empty-hint compact">
      <div class="empty-hint-icon">${escapeHtml(emptyHint.icon || "📭")}</div>
      <div class="empty-hint-title">${escapeHtml(emptyHint.title || "暂无内容")}</div>
      ${emptyHint.text ? `<div class="empty-hint-text">${escapeHtml(emptyHint.text)}</div>` : ""}
    </div>`;
  }
  return `<div class="muted small">无</div>`;
}

function levelText(adventurer) {
  if (adventurer.next_level.max_level) {
    return "已满级";
  }
  return `EXP ${adventurer.experience}/${adventurer.next_level.required} · 差 ${adventurer.next_level.remaining}`;
}

function experienceBlock(obs, adventurer) {
  const next = adventurer.next_level;
  const percent = next.max_level ? 100 : Math.min(100, Math.floor((next.current / next.required) * 100));
  const previewUnlocks = next.preview_level_skill_unlocks || [];
  return `
    <div class="xp-block">
      <div class="inline small">
        <span>${next.max_level ? "已达到最高等级" : `距离下一级还差 ${next.remaining} 经验`}</span>
        <span class="muted">经验池 ${obs.experience_pool}</span>
      </div>
      <div class="progress"><span style="width: ${percent}%"></span></div>
      <div class="small muted">下级属性成长：${statModifierText(obs.experience_rules.stat_growth_per_level)}</div>
      ${next.preview_level !== adventurer.level ? `<div class="small ok">投入全部经验池可到 Lv.${next.preview_level}</div>` : ""}
      ${previewUnlocks.length ? `<div class="small ok">可解锁：${previewUnlocks.map((item) => levelPreviewUnlockText(item)).join(" · ")}</div>` : ""}
    </div>
    <div class="xp-form">
      <input id="xp-${adventurer.adventurer_id}" type="number" min="0" max="${obs.experience_pool}" value="0" ${disabled()} oninput="updateExperiencePreview('${adventurer.adventurer_id}')" />
      <button type="button" ${disabled()} onclick="allocateExperience('${adventurer.adventurer_id}')">分配经验</button>
    </div>
    <div id="xp-preview-${adventurer.adventurer_id}" class="small muted">${experiencePreviewText(adventurer, 0, obs.experience_rules)}</div>
  `;
}

function levelSkillUnlocksBlock(adventurer) {
  const unlocks = adventurer.level_skill_unlocks || [];
  if (!unlocks.length) {
    return "";
  }
  return `
    <div class="skill-unlocks">
      <span class="skill-unlocks-label">升级解锁</span>
      ${unlocks.map((unlock) => {
        const stateClass = unlock.unlocked ? "ok" : "locked";
        const skillTags = (unlock.skills || []).map((s) => skillTag(s)).join(" ");
        return `<span class="skill-unlock ${stateClass}">Lv.${unlock.level}${skillTags}</span>`;
      }).join("")}
    </div>
  `;
}

function levelUnlockText(unlock) {
  const stateText = unlock.unlocked ? "已解锁" : "未解锁";
  const skillDetails = (unlock.skills || []).map((s) => skillTag(s)).join(" ");
  return `Lv.${unlock.level} ${stateText} ${skillDetails}`;
}

function levelPreviewUnlockText(unlock) {
  const skillDetails = (unlock.skills || []).map((s) => skillTag(s)).join(" ");
  return `Lv.${unlock.level} ${skillDetails}`;
}

function equipmentSlotCell(adventurer, slot) {
  if (slot.item) {
    return `
      <div class="slot-cell filled">
        <div class="inline slot-head">
          <span class="slot-icon">${slotIcon(slot.slot)}</span>
          <strong>${slotName(slot.slot)}</strong>
          <button type="button" ${disabled()} onclick="unequip('${adventurer.adventurer_id}', '${slot.slot}')">卸下</button>
        </div>
        <div>${escapeHtml(slot.item.name)}</div>
        <div class="small muted">${statModifierText(slot.item.stats)}</div>
      </div>
    `;
  }
  if (slot.blocked_by) {
    return `
      <div class="slot-cell slot-blocked">
        <div class="inline slot-head">
          <span class="slot-icon">${slotIcon(slot.slot)}</span>
          <strong>${slotName(slot.slot)}</strong>
        </div>
        <div class="small muted">被${slotName(slot.blocked_by)}占用</div>
      </div>
    `;
  }
  return `
    <div class="slot-cell slot-empty" onclick="openEquipPopup('${adventurer.adventurer_id}', '${slot.slot}', this)">
      <div class="inline slot-head">
        <span class="slot-icon">${slotIcon(slot.slot)}</span>
        <strong>${slotName(slot.slot)}</strong>
      </div>
      <div class="small muted">空 · 点击装备</div>
    </div>
  `;
}

function rewardText(reward) {
  return `金币 ${reward.gold} · 经验 ${reward.experience} · ${materialsText(reward.materials)}`;
}

function materialsText(materials) {
  const entries = Object.entries(materials || {});
  return entries.length ? entries.map(([key, value]) => `${materialName(key)}:${value}`).join(" · ") : "无";
}

function missingText(missing) {
  if (!missing || Object.keys(missing).length === 0) {
    return "无";
  }
  return Object.entries(missing).map(([key, value]) => missingEntryText(key, value)).join(" · ");
}

function missingEntryText(key, value) {
  if (key === "gold") {
    return `金币 ${value}`;
  }
  if (key === "party_size_limit" && value && typeof value === "object") {
    const current = value.current ?? "?";
    const limit = value.limit ?? "?";
    return `队伍已满（${current}/${limit}）`;
  }
  if (key === "required_upgrade_ids") {
    const values = Array.isArray(value) ? value : [value];
    return `前置升级 ${values.map(upgradeName).join("、")}`;
  }
  if (Array.isArray(value)) {
    return `${materialName(key)} ${value.map(String).join("、")}`;
  }
  if (value && typeof value === "object") {
    return `${materialName(key)} ${Object.entries(value).map(([subKey, subValue]) => `${materialName(subKey)} ${subValue}`).join("、")}`;
  }
  return `${materialName(key)} ${value}`;
}

function materialName(key) {
  return {
    iron_ore: "铁矿石",
    wood: "木材",
    leather: "皮革",
    herb: "草药",
    bone: "骨头",
    beast_hide: "兽皮",
    sharp_claw: "利爪",
    arcane_dust: "奥术之尘",
    spider_silk: "蛛丝",
    mithril_shard: "秘银碎片",
    dragon_scale: "龙鳞",
    demon_core: "恶魔核心",
    soul_shard: "灵魂碎片",
    dragon_blood: "龙血",
  }[key] || key;
}

function names(items) {
  return items.length ? items.map((item) => item.name).join(" · ") : "无";
}

function skillList(skills, label = "技能") {
  const values = skills || [];
  if (!values.length) {
    return `<div class="small muted">${label}：无</div>`;
  }
  return `
    <div class="small muted">${label}：</div>
    <div class="skill-list">${values.map((skill) => skillTag(skill)).join("")}</div>
  `;
}

function skillTag(skill) {
  const desc = skillDescText(skill);
  return `<span class="skill-tag" data-tip="${escapeHtml(desc)}">${escapeHtml(skill.name)}</span>`;
}

function skillDescText(skill) {
  const parts = [];
  parts.push(skill.kind === "active" ? "主动" : "被动");
  if (skill.mp_cost > 0) parts.push(`消耗 ${skill.mp_cost} MP`);
  if (skill.free) parts.push("即时（附赠普攻）");
  if (skill.once_per_battle) parts.push("每场限一次");

  const condText = skillConditionText(skill.condition);
  if (condText) parts.push(`条件：${condText}`);

  for (const eff of skill.effects) {
    if (eff.type === "damage_multiplier") parts.push(`伤害 ×${eff.value}`);
    if (eff.type === "heal") parts.push(`治疗 ${eff.value} HP`);
    if (eff.type === "heal_percent") parts.push(`治疗 ${Math.round(eff.value * 100)}% 最大HP`);
    if (eff.type === "mp_restore") parts.push(`${eff.target === "self" ? "自身" : "目标"}恢复 ${eff.value} MP`);
    if (eff.type === "damage_bonus") parts.push(`伤害 +${eff.value}`);
    if (eff.type === "true_damage") parts.push(`真实伤害 ${eff.value}`);
    if (eff.type === "self_damage") parts.push(`自身受伤 ${eff.value}`);
    if (eff.type === "apply_status" && eff.status) {
      const polarity = eff.status.polarity === "positive" ? "正面" : eff.status.polarity === "negative" ? "负面" : "";
      const dur = eff.status.duration ? ` ${eff.status.duration}回合` : "";
      const statusEffects = (eff.status.effects || []).map((se) => {
        if (se.type === "true_damage") return `每回合${se.value}伤害`;
        if (se.type === "heal") return `每回合恢复${se.value}HP`;
        if (se.type === "heal_percent") return `每回合恢复${Math.round(se.value * 100)}%HP`;
        if (se.type === "mp_restore") return `每回合恢复${se.value}MP`;
        if (se.type === "stat_bonus") return `${statName(se.stat)}+${se.value}`;
        if (se.type === "stat_multiplier") return `${statName(se.stat)}×${se.value}`;
        return null;
      }).filter(Boolean).join("，");
      const detail = statusEffects ? `（${statusEffects}）` : "";
      parts.push(`施加${polarity}状态 ${eff.status.name}${dur}${detail}`);
    }
    if (eff.type === "stat_bonus") parts.push(`${statName(eff.stat)} +${eff.value}`);
    if (eff.type === "stat_multiplier") parts.push(`${statName(eff.stat)} ×${eff.value}`);
  }
  return parts.join("，");
}

function skillConditionText(cond) {
  if (!cond || cond.type === "always") return "";
  if (cond.type === "self_hp_pct_lte") return `自身HP ≤ ${Math.round(cond.value * 100)}%`;
  if (cond.type === "self_hp_pct_gte") return `自身HP ≥ ${Math.round(cond.value * 100)}%`;
  if (cond.type === "target_hp_pct_lte") return `目标HP ≤ ${Math.round(cond.value * 100)}%`;
  if (cond.type === "target_hp_pct_gte") return `目标HP ≥ ${Math.round(cond.value * 100)}%`;
  if (cond.type === "self_mp_pct_lte") return `自身MP ≤ ${Math.round(cond.value * 100)}%`;
  if (cond.type === "self_mp_pct_gte") return `自身MP ≥ ${Math.round(cond.value * 100)}%`;
  if (cond.type === "target_mp_pct_lte") return `目标MP ≤ ${Math.round(cond.value * 100)}%`;
  if (cond.type === "target_mp_pct_gte") return `目标MP ≥ ${Math.round(cond.value * 100)}%`;
  if (cond.type === "action_index_lte") return `行动序号 ≤ ${cond.value}`;
  if (cond.type === "action_index_gte") return `行动序号 ≥ ${cond.value}`;
  if (cond.type === "all") return (cond.conditions || []).map(skillConditionText).filter(Boolean).join(" 且 ");
  if (cond.type === "any") return (cond.conditions || []).map(skillConditionText).filter(Boolean).join(" 或 ");
  return "";
}

function statModifierText(stats) {
  const entries = Object.entries(stats || {}).filter(([, value]) => value !== 0);
  return entries.length ? entries.map(([key, value]) => `${statName(key)} ${signedNumber(value)}`).join(" · ") : "无属性";
}

function signedNumber(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) {
    return String(value);
  }
  return number > 0 ? `+${number}` : String(number);
}

function upgradePrereqText(obs, ids) {
  if (!ids || !ids.length) return "无";
  return ids.map((id) => {
    const u = obs.global_upgrades.find((g) => g.upgrade_id === id);
    return u ? u.name : id;
  }).join("、");
}

function upgradeName(id) {
  const upgrade = state.observation?.global_upgrades?.find((item) => item.upgrade_id === id);
  return upgrade?.name || String(id);
}

function resolveName(id) {
  if (!state.observation) return id;
  const adventurer = state.observation.adventurers.find((a) => a.adventurer_id === id);
  if (adventurer) return adventurer.name;
  const monster = state.observation.monsters.find((m) => m.monster_id === id);
  if (monster) return monster.name;
  return id;
}

function eventTypeName(type) {
  return {
    session_started: "会话开始",
    preparation_applied: "回合内操作",
    turn_ended: "结束回合",
    action_rejected: "动作失败",
  }[type] || type;
}

function changeText(change) {
  if (!("before" in change)) {
    return `<span class="change">${escapeHtml(change.label)}：${escapeHtml(change.after)}</span>`;
  }
  return `<span class="change">${escapeHtml(change.label)}：${escapeHtml(change.before)} → ${escapeHtml(change.after)}</span>`;
}

function updateExperiencePreview(adventurerId) {
  const adventurer = state.observation.adventurers.find((item) => item.adventurer_id === adventurerId);
  const target = $(`xp-preview-${adventurerId}`);
  if (!adventurer || !target) {
    return;
  }
  const amount = readExperienceAmount(adventurerId);
  target.textContent = experiencePreviewText(adventurer, amount, state.observation.experience_rules);
}

function readExperienceAmount(adventurerId) {
  const input = $(`xp-${adventurerId}`);
  if (!input) {
    return 0;
  }
  const amount = Number.parseInt(input.value || "0", 10);
  const pool = Number(state.observation?.experience_pool ?? 0);
  const max = Number.isFinite(pool) ? Math.max(0, pool) : 0;
  return Number.isFinite(amount) ? Math.min(Math.max(0, amount), max) : 0;
}

function experiencePreviewText(adventurer, amount, rules) {
  if (adventurer.next_level.max_level) {
    return "已满级，无法继续升级";
  }
  let level = adventurer.level;
  let experience = adventurer.experience + amount;
  while (level < rules.max_level) {
    const required = rules.base_required_experience + (level - 1) * rules.required_experience_growth;
    if (experience < required) {
      break;
    }
    experience -= required;
    level += 1;
  }
  if (level >= rules.max_level) {
    return `投入后：Lv.${rules.max_level}，已满级`;
  }
  const required = rules.base_required_experience + (level - 1) * rules.required_experience_growth;
  return `投入后：Lv.${level}，EXP ${experience}/${required}，差 ${required - experience}`;
}

function statName(key) {
  return {
    hp: "HP",
    mp: "MP",
    attack: "攻击",
    defense: "防御",
    speed: "速度",
    recovery: "回血",
    mp_recovery: "回魔",
  }[key] || key;
}

function slotName(slot) {
  return {
    main_hand: "右手",
    off_hand: "左手",
    two_hand: "双手",
    hand: "单手槽",
    boots: "鞋子",
    helmet: "头盔",
    armor: "护甲",
    accessory: "饰品",
  }[slot] || slot;
}

function disabled(extra = false) {
  return state.watchOnly || state.actionPending || state.observation?.finished || extra ? "disabled" : "";
}

function showError(message) {
  showToast(message, "error");
}

function showToast(message, variant = "info") {
  const toast = $("toast");
  if (!toast) return;
  toast.textContent = message;
  toast.dataset.variant = variant;
  toast.hidden = false;
  window.setTimeout(() => {
    toast.hidden = true;
  }, 3000);
}

/* ========== LLM Debug ========== */

function startLlmDebug(options = {}) {
  stopLlmDebug(false);
  state.llm.running = true;
  state.llm.status = options.resumeRunId ? "续跑连接中" : "连接中";
  state.llm.prompt = "";
  state.llm.transcript = [];
  state.llm.toolTrace = [];
  state.llm.events = [];
  state.llm.currentModelEntry = null;
  state.llm.openThinking.clear();
  state.llm.openToolTrace.clear();
  state.llm.openTurns.clear();
  state.llm.toolTraceSeq = 0;
  state.llm.autoScroll = true;
  state.llm.userScrolledUp = false;
  syncLlmAutoScrollButton();
  renderLlmDebug();

  const protocol = window.location.protocol === "https:" ? "wss" : "ws";
  const socket = new WebSocket(`${protocol}://${window.location.host}/ws/llm/debug`);
  state.llm.socket = socket;

  socket.addEventListener("open", () => {
    const payload = llmPayload(options);
    socket.send(JSON.stringify({ type: "start", payload }));
    state.llm.status = options.resumeRunId ? "续跑运行中" : "运行中";
    renderLlmDebug();
  });
  socket.addEventListener("message", (message) => {
    handleLlmEvent(JSON.parse(message.data));
  });
  socket.addEventListener("close", () => {
    if (state.llm.running) {
      state.llm.running = false;
      state.llm.status = "连接已关闭";
      renderLlmDebug();
    }
  });
  socket.addEventListener("error", () => {
    state.llm.running = false;
    state.llm.status = "连接错误";
    renderLlmDebug();
  });
}

function stopLlmDebug(update = true) {
  if (state.llm.socket) {
    state.llm.socket.close();
    state.llm.socket = null;
  }
  if (update) {
    state.llm.running = false;
    state.llm.status = "已停止";
    renderLlmDebug();
  }
}

function llmPayload(options = {}) {
  const payload = {
    model: readOptionalValue("llmModel"),
    base_url: readOptionalValue("llmBaseUrl"),
    api_key: readOptionalValue("llmApiKey"),
    max_tool_calls_per_turn: readNumberValue("llmMaxToolCalls", 20),
    game_seed: readOptionalInteger("llmGameSeed"),
    scoring_seed: readOptionalInteger("llmScoringSeed"),
    temperature: readOptionalNumber("llmTemperature"),
    objective: readOptionalValue("llmObjective"),
  };
  if (options.resumeRunId) {
    payload.resume_run_id = options.resumeRunId;
  }
  return payload;
}

function handleLlmEvent(event) {
  state.llm.events.push(compactLlmEvent(event));
  if (state.llm.events.length > 300) {
    state.llm.events.shift();
  }

  if (event.type === "run_started") {
    state.llm.status = `运行中 · 会话 ${event.session_id}`;
  } else if (event.type === "run_resumed") {
    state.llm.status = `续跑中 · 会话 ${event.session_id}`;
    state.llm.transcript.push({
      kind: "turn",
      title: `从归档续跑：${event.archive?.directory || ""}`,
    });
  } else if (event.type === "turn_started") {
    state.llm.prompt = event.prompt || "";
    state.llm.status = `第 ${event.turn} 回合`;
    state.llm.currentModelEntry = null;
    state.llm.transcript.push({ kind: "turn", title: `第 ${event.turn} 回合开始` });
    if (event.turn != null) {
      state.llm.openTurns.add(event.turn);
    }
  } else if (event.type === "model_request") {
    const entry = createModelEntry(event.turn, event.step);
    entry.request = event.request || null;
    state.llm.currentModelEntry = entry;
    state.llm.transcript.push(entry);
    if (event.turn != null) {
      state.llm.thinkingTurns.add(event.turn);
    }
  } else if (event.type === "model_reasoning_delta") {
    ensureModelEntry().reasoningText += event.text || "";
  } else if (event.type === "model_delta") {
    ensureModelEntry().text += event.text || "";
  } else if (event.type === "model_response") {
    const entry = ensureModelEntry();
    if (!entry.text && event.text) {
      entry.text = event.text;
    }
    const reasoning = event.assistant_metadata?.reasoning_content;
    if (!entry.reasoningText && reasoning) {
      entry.reasoningText = reasoning;
    }
    entry.toolCalls = event.tool_calls || [];
    entry.timing = event.timing || null;
    entry.usage = event.usage || null;
    if (event.turn != null) {
      state.llm.thinkingTurns.delete(event.turn);
    }
  } else if (event.type === "model_stream_completed") {
    const entry = ensureModelEntry();
    if (!entry.text && event.text) {
      entry.text = event.text;
    }
    entry.toolCalls = event.tool_calls || entry.toolCalls || [];
    entry.usage = event.usage || entry.usage || null;
  } else if (event.type === "tool_call") {
    state.llm.toolTrace.push(createToolTraceItem(event));
  } else if (event.type === "tool_result") {
    let item = findToolTrace(event.call_id, event.name);
    if (item) {
      item.ok = event.result?.ok ?? null;
      item.error = event.result?.error || "";
      item.content = event.content || item.content || "";
      item.hasResult = true;
    } else {
      item = createToolTraceItem(event);
      item.ok = event.result?.ok ?? null;
      item.error = event.result?.error || "";
      item.hasResult = true;
      state.llm.toolTrace.push(item);
    }
    const toolId = toolTraceId(item);
    if (event.result?.ok === false) {
      state.llm.openToolTrace.add(toolId);
    } else if (event.result?.ok === true) {
      state.llm.openToolTrace.delete(toolId);
    }
  } else if (event.type === "retry") {
    state.llm.transcript.push({ kind: "retry", reason: event.reason, text: event.message });
  } else if (event.type === "turn_completed") {
    const timingUsage = aggregateTranscriptTiming();
    state.llm.status = `第 ${event.trace?.turn || ""} 回合完成`;
    state.llm.transcript.push({ kind: "turn", title: `第 ${event.trace?.turn || ""} 回合完成`, timingUsage });
    if (event.trace?.turn != null) {
      state.llm.thinkingTurns.delete(event.trace.turn);
    }
  } else if (event.type === "turn_failed") {
    state.llm.running = false;
    state.llm.status = `回合失败：${event.trace?.failure_reason || "unknown"}`;
    if (event.trace?.turn != null) {
      state.llm.thinkingTurns.delete(event.trace.turn);
    }
  } else if (event.type === "run_archived") {
    state.llm.transcript.push({
      kind: "turn",
      title: `已留档：${event.archive?.directory || ""}`,
    });
    state.llm.replay.status = "新归档已生成，可刷新后加载 replay";
  } else if (event.type === "run_completed") {
    state.llm.running = false;
    const score = formatScore(event.run?.score);
    state.llm.status = `完成 · ${event.run?.turns || 0} 回合 · ${score ? `${score} · ` : ""}已留档`;
    state.llm.transcript.push({ kind: "summary", stats: event.run?.stats, score: event.run?.score, turns: event.run?.turns });
  } else if (event.type === "run_failed") {
    state.llm.running = false;
    state.llm.status = `失败：${event.run?.failure_reason || "unknown"} · 已留档`;
    state.llm.transcript.push({ kind: "summary", stats: event.run?.stats, turns: event.run?.turns, failureReason: event.run?.failure_reason });
  } else if (event.type === "debug_error") {
    state.llm.running = false;
    state.llm.status = `错误：${event.error}`;
  }

  if (isStreamingLlmEvent(event)) {
    scheduleLlmRender();
  } else {
    renderLlmDebug();
  }
}

function ensureModelEntry() {
  if (!state.llm.currentModelEntry) {
    state.llm.currentModelEntry = createModelEntry("", "");
    state.llm.transcript.push(state.llm.currentModelEntry);
  }
  return state.llm.currentModelEntry;
}

function createModelEntry(turn, step) {
  return {
    kind: "model",
    id: `model-${turn ?? "na"}-${step ?? state.llm.transcript.length}`,
    turn,
    step,
    text: "",
    reasoningText: "",
    timing: null,
    usage: null,
    request: null,
  };
}

function aggregateTranscriptTiming() {
  let totalMs = 0, totalIn = 0, totalOut = 0, hasData = false;
  const tr = state.llm.transcript;
  for (let i = tr.length - 1; i >= 0; i--) {
    const e = tr[i];
    if (e.kind === "turn") break;
    if (e.kind !== "model") continue;
    if (e.timing?.duration_ms != null) { totalMs += Number(e.timing.duration_ms); hasData = true; }
    if (e.usage) {
      const inp = e.usage.input_tokens ?? e.usage.prompt_tokens;
      const out = e.usage.output_tokens ?? e.usage.completion_tokens;
      if (inp != null) { totalIn += Number(inp); hasData = true; }
      if (out != null) { totalOut += Number(out); hasData = true; }
    }
  }
  return hasData ? { duration_ms: Math.round(totalMs), input_tokens: totalIn, output_tokens: totalOut } : null;
}

function createToolTraceItem(event) {
  return {
    id: event.call_id ? `tool-${event.call_id}` : `tool-${state.llm.toolTraceSeq++}`,
    callId: event.call_id,
    turn: event.turn,
    name: event.name,
    arguments: event.arguments || {},
    content: event.content || "",
    ok: null,
    hasResult: false,
    error: "",
  };
}

function toolTraceId(item) {
  if (!item.id) {
    item.id = item.callId ? `tool-${item.callId}` : `tool-${state.llm.toolTraceSeq++}`;
  }
  return item.id;
}

function isStreamingLlmEvent(event) {
  return event.type === "model_delta"
    || event.type === "model_reasoning_delta"
    || event.type === "tool_call_delta";
}

function scheduleLlmRender() {
  if (state.llm.renderQueued) {
    return;
  }
  state.llm.renderQueued = true;
  const scheduleFrame = window.requestAnimationFrame || ((callback) => window.setTimeout(callback, 16));
  scheduleFrame(() => {
    state.llm.renderQueued = false;
    renderLlmDebug({ streamOnly: true });
  });
}

function findToolTrace(callId, name) {
  for (let i = state.llm.toolTrace.length - 1; i >= 0; i--) {
    const item = state.llm.toolTrace[i];
    if (callId && item.callId === callId) return item;
    if (!callId && item.name === name && item.ok === null) return item;
  }
  return null;
}

function renderLlmDebug(options = {}) {
  if (!$("llmStatus")) return;
  const includeReplay = options.includeReplay !== false;
  const streamOnly = options.streamOnly === true;
  $("llmStatus").textContent = state.llm.status;
  $("llmStartButton").disabled = state.llm.running;
  $("llmStopButton").disabled = !state.llm.running;
  $("llmReplayResumeButton").disabled = state.llm.running || !state.llm.replay.selectedRunId;
  $("llmPrompt").textContent = state.llm.prompt || "尚未开始";
  updateLlmStatusPulse();
  updateLlmStatusStats();
  updateLlmRuntimeCounts();
  if (streamOnly && state.llm.currentModelEntry) {
    streamUpdateModelEntry(state.llm.currentModelEntry);
  } else {
    $("llmTranscript").innerHTML = renderLlmTranscript();
  }
  if (streamOnly) {
    // 流式更新也要滚到底部
    if (state.llm.autoScroll) {
      requestAnimationFrame(() => scrollLlmPanelToBottom({ force: true }));
    }
    return;
  }
  $("llmToolTrace").innerHTML = renderLlmToolTrace();
  $("llmEventLog").innerHTML = renderLlmEventLog();
  if (includeReplay) {
    renderLlmReplayControls();
    $("llmReplayStatus").textContent = state.llm.replay.status;
    $("llmReplayView").innerHTML = renderLlmReplay();
  }
  // 完整重渲染后，autoScroll 开启则滚到底部
  if (state.llm.autoScroll) {
    requestAnimationFrame(() => scrollLlmPanelToBottom({ force: true }));
  }
}

function updateLlmStatusPulse() {
  const dot = document.querySelector(".llm-pulse-dot");
  if (!dot) return;
  const status = state.llm.status || "";
  let stateName = "idle";
  if (state.llm.running) {
    stateName = "true";
  } else if (/失败|错误|连接错误|已停止/.test(status)) {
    stateName = "error";
  } else if (/完成/.test(status)) {
    stateName = "done";
  }
  dot.dataset.running = stateName === "idle" ? "false" : stateName;
}

function updateLlmStatusStats() {
  const container = $("llmStatusStats");
  if (!container) return;
  const stats = computeLlmRunStats();
  setStat(container, "turn", stats.turn ?? "—");
  setStat(container, "calls", stats.modelCalls);
  setStat(container, "tools", stats.toolCalls);
  setStat(container, "input", stats.inputTokens);
  setStat(container, "output", stats.outputTokens);
  setStat(container, "duration", stats.durationMs != null ? formatDurationMs(stats.durationMs) : (state.llm.running ? "运行中" : "—"));
}

function setStat(container, key, value) {
  const node = container.querySelector(`[data-stat="${key}"]`);
  if (node) node.textContent = value;
}

function computeLlmRunStats() {
  let turn = null;
  let modelCalls = 0;
  let toolCalls = 0;
  let inputTokens = 0;
  let outputTokens = 0;
  let totalDurationMs = 0;
  let hasTokens = false;
  let hasDuration = false;
  for (const entry of state.llm.transcript) {
    if (entry.kind === "turn" && typeof entry.title === "string") {
      const match = entry.title.match(/第\s*(\d+)\s*回合/);
      if (match) turn = match[1];
    } else if (entry.kind === "model") {
      modelCalls += 1;
      if (entry.usage) {
        const inp = entry.usage.input_tokens ?? entry.usage.prompt_tokens;
        const out = entry.usage.output_tokens ?? entry.usage.completion_tokens;
        if (inp != null) { inputTokens += Number(inp); hasTokens = true; }
        if (out != null) { outputTokens += Number(out); hasTokens = true; }
      }
      if (entry.timing?.duration_ms != null) {
        totalDurationMs += Number(entry.timing.duration_ms);
        hasDuration = true;
      }
    }
  }
  for (const item of state.llm.toolTrace) {
    if (item && item.name) toolCalls += 1;
  }
  return {
    turn,
    modelCalls,
    toolCalls,
    inputTokens: hasTokens ? inputTokens.toLocaleString() : "0",
    outputTokens: hasTokens ? outputTokens.toLocaleString() : "0",
    durationMs: hasDuration ? totalDurationMs : null,
  };
}

function renderEmptyState(icon, title, text) {
  return `
    <div class="llm-empty-state">
      <div class="llm-empty-state-icon">${escapeHtml(icon)}</div>
      <div class="llm-empty-state-title">${escapeHtml(title)}</div>
      <div class="llm-empty-state-text">${escapeHtml(text)}</div>
    </div>
  `;
}

function copyLlmPromptToClipboard() {
  const text = state.llm.prompt || "";
  if (!text) {
    showToast("当前没有可复制的 prompt", "info");
    return;
  }
  copyTextToClipboard(text)
    .then(() => showToast("已复制 prompt 到剪贴板", "ok"))
    .catch(() => showToast("复制失败，浏览器拒绝写入剪贴板", "error"));
}

function copyTextToClipboard(text) {
  if (navigator.clipboard?.writeText) {
    return navigator.clipboard.writeText(text);
  }
  return new Promise((resolve, reject) => {
    const textarea = document.createElement("textarea");
    textarea.value = text;
    textarea.style.position = "fixed";
    textarea.style.opacity = "0";
    document.body.appendChild(textarea);
    textarea.focus();
    textarea.select();
    try {
      const ok = document.execCommand("copy");
      document.body.removeChild(textarea);
      ok ? resolve() : reject(new Error("execCommand returned false"));
    } catch (error) {
      document.body.removeChild(textarea);
      reject(error);
    }
  });
}

function clearLlmTranscript() {
  if (state.llm.running) {
    showToast("运行中无法清空模型行为", "info");
    return;
  }
  state.llm.transcript = [];
  state.llm.toolTrace = [];
  state.llm.currentModelEntry = null;
  state.llm.openThinking.clear();
  state.llm.openToolTrace.clear();
  state.llm.openTurns.clear();
  state.llm.toolTraceSeq = 0;
  state.llm.prompt = "";
  renderLlmDebug();
  showToast("已清空模型行为", "ok");
}

function clearLlmEventLog() {
  state.llm.events = [];
  renderLlmDebug();
  showToast("已清空事件流", "ok");
}

const LLM_PRESETS = {
  openai: {
    base_url: "https://api.openai.com/v1",
    model: "gpt-4o-mini",
  },
  deepseek: {
    base_url: "https://api.deepseek.com/v1",
    model: "deepseek-chat",
  },
  anthropic: {
    base_url: "https://api.anthropic.com/v1",
    model: "claude-3-5-sonnet-latest",
  },
};

function applyLlmPreset(value) {
  if (!value || value === "custom") {
    $("llmPresetSelect").value = "";
    return;
  }
  const preset = LLM_PRESETS[value];
  if (!preset) return;
  setInputIfEmpty("llmBaseUrl", preset.base_url);
  setInputIfEmpty("llmModel", preset.model);
  $("llmPresetSelect").value = "";
}

function setInputIfEmpty(id, value) {
  const input = $(id);
  if (!input) return;
  if (input.value && input.value.trim()) {
    showToast(`${labelOf(id)} 已存在，未覆盖`, "info");
    return;
  }
  input.value = value;
}

function labelOf(id) {
  const labels = {
    llmBaseUrl: "Base URL",
    llmModel: "模型名",
    llmApiKey: "API Key",
    llmGameSeed: "游戏 Seed",
    llmScoringSeed: "评分 Seed",
    llmTemperature: "Temperature",
  };
  return labels[id] || id;
}

function renderLlmTranscript() {
  if (!state.llm.transcript.length) {
    return renderEmptyState("💭", "等待第一次模型行为", "点击「开始运行」后，这里会按回合流式展示思考、回复和工具调用");
  }
  return renderTurnFlow();
}

function renderTurnFlow() {
  const entries = state.llm.transcript.slice(-80);
  // toolTrace 按 turn 聚合
  const toolsByTurn = new Map();
  for (const tool of state.llm.toolTrace) {
    if (tool.turn == null) continue;
    if (!toolsByTurn.has(tool.turn)) toolsByTurn.set(tool.turn, []);
    toolsByTurn.get(tool.turn).push(tool);
  }
  // 拆分 entries 为 turn 块。turn_started / turn_completed 共享同一个 turn 块
  const blocks = [];
  let current = null;
  for (const entry of entries) {
    const matched = entry.kind === "turn" && typeof entry.title === "string"
      ? entry.title.match(/第\s*(\d+)\s*回合/)
      : null;
    if (matched) {
      const turnNum = Number(matched[1]);
      const isCompletion = /完成|失败/.test(entry.title);
      if (current && current.turn === turnNum) {
        // 同一个 turn 内的开始/完成标记合并
        if (isCompletion) {
          current.isComplete = true;
          current.completionTitle = entry.title;
          current.completionTiming = entry.timingUsage;
        } else {
          current.title = entry.title;
        }
      } else {
        if (current) blocks.push(current);
        current = {
          turn: turnNum,
          title: entry.title,
          timing: entry.timingUsage,
          isComplete: isCompletion,
          items: [],
        };
      }
      continue;
    }
    if (current) {
      current.items.push(entry);
    } else {
      if (!blocks.length || blocks[0].kind !== "intro") {
        blocks.unshift({ kind: "intro", items: [] });
      }
      blocks[0].items.push(entry);
    }
  }
  if (current) blocks.push(current);

  const lastIncomplete = [...blocks].reverse().find((b) => b.turn != null && !b.isComplete);
  const activeTurn = lastIncomplete?.turn ?? null;

  return blocks.map((block) => {
    if (block.kind === "intro") {
      return block.items.map((entry) => renderIntroEntry(entry)).join("");
    }
    return renderTurnBlock(block, toolsByTurn.get(block.turn) || [], activeTurn);
  }).join("");
}

function renderIntroEntry(entry) {
  if (entry.kind === "turn") {
    const meta = renderTurnTimingUsage(entry.timingUsage);
    return `<div class="llm-turn-marker">${escapeHtml(entry.title)}${meta}</div>`;
  }
  if (entry.kind === "summary") return renderRunSummary(entry);
  if (entry.kind === "retry") return `<div class="llm-retry">重试提示：${escapeHtml(entry.text)}</div>`;
  return "";
}

function renderTurnBlock(block, tools, activeTurn) {
  const isActive = block.turn === activeTurn;
  const wasOpened = state.llm.openTurns.has(block.turn);
  // 用户主动收起的 turn 保持收起，即使后续 re-render
  const userCollapsed = state.llm.userCollapsedTurns.has(block.turn);
  const open = isActive || (wasOpened && !userCollapsed);
  const turnNumber = escapeHtml(String(block.turn));
  // timing 来源优先级：turn_started/turn_completion 自带 > 本回合 model entry 汇总
  const timing = renderTurnTimingUsage(block.timing || block.completionTiming || aggregateTurnTiming(block.items));
  const turnStatus = block.isComplete
    ? `<span class="llm-turn-pill ok">已完成</span>`
    : (isActive
        ? (state.llm.thinkingTurns.has(block.turn)
            ? `<span class="llm-turn-pill live">思考中<span class="llm-ov-pending-dots" aria-hidden="true"><i></i><i></i><i></i></span></span>`
            : `<span class="llm-turn-pill live">执行中<span class="llm-ov-pending-dots" aria-hidden="true"><i></i><i></i><i></i></span></span>`)
        : `<span class="llm-turn-pill">未开始</span>`);
  const modelCount = block.items.filter((e) => e.kind === "model").length;
  const toolCount = tools.length;
  const counts = `<span class="llm-turn-counts small muted">💭 ${modelCount} · 🔧 ${toolCount}</span>`;
  const overview = renderTurnOverview(block.items, tools);
  const body = [
    ...block.items.map((entry) => renderTurnEntry(entry)),
    tools.length ? renderTurnToolsSection(tools) : "",
  ].filter(Boolean).join("");
  const metaText = extractTimingText(timing);
  return `
    <details class="llm-turn-block" data-turn="${turnNumber}" ${open ? "open" : ""}>
      <summary>
        <span class="llm-turn-handle" aria-hidden="true">▾</span>
        <span class="llm-turn-title"><strong>第 ${turnNumber} 回合</strong></span>
        ${turnStatus}
        ${counts}
        ${metaText ? `<span class="llm-turn-meta">${metaText}</span>` : ""}
        ${overview}
      </summary>
      <div class="llm-turn-block-body">${body}</div>
    </details>
  `;
}

function extractTimingText(timingHtml) {
  // timingHtml 是 `<span class="llm-turn-meta">1.20 s · in 1000 · out 30</span>`
  const m = /<span class="llm-turn-meta">([^<]*)<\/span>/.exec(timingHtml || "");
  return m ? m[1].trim() : "";
}

// 汇总本回合 model entry 的 timing/usage
function aggregateTurnTiming(modelEntries) {
  let totalMs = 0, totalIn = 0, totalOut = 0, hasData = false;
  for (const e of modelEntries) {
    if (!e || e.kind !== "model") continue;
    if (e.timing?.duration_ms != null) { totalMs += Number(e.timing.duration_ms); hasData = true; }
    if (e.usage) {
      const inp = e.usage.input_tokens ?? e.usage.prompt_tokens;
      const out = e.usage.output_tokens ?? e.usage.completion_tokens;
      if (inp != null) { totalIn += Number(inp); hasData = true; }
      if (out != null) { totalOut += Number(out); hasData = true; }
    }
  }
  if (!hasData) return null;
  return { duration_ms: Math.round(totalMs), input_tokens: totalIn, output_tokens: totalOut };
}

// 回合概览：按时间线展示"思考 → 调用 → 结果"决策链
function renderTurnOverview(modelEntries, tools) {
  // 提取所有 model entry 的 text + toolCalls（保留 step 顺序）
  const steps = modelEntries
    .filter((e) => e.kind === "model")
    .map((e) => ({
      text: (e.text || "").trim(),
      toolCalls: e.toolCalls || [],
    }))
    .filter((s) => s.text || s.toolCalls.length);

  if (!steps.length) return "";

  // 按 callId 索引 toolTrace，便于查结果
  const toolsByCallId = new Map();
  for (const t of tools) {
    if (t.callId) toolsByCallId.set(t.callId, t);
  }

  const thinkItems = [];
  const actionItems = [];
  let stepNum = 1;

  for (const s of steps) {
    if (s.text) {
      thinkItems.push(
        `<li class="llm-ov-step llm-ov-step-think">` +
          `<span class="llm-ov-bullet">${stepNum++}</span>` +
          `<div class="llm-ov-body">` +
            `<div class="llm-ov-text">${escapeHtml(s.text)}</div>` +
          `</div>` +
        `</li>`
      );
    }
    for (const tc of s.toolCalls) {
      const t = toolsByCallId.get(tc.id) || {};
      const toolName = tc.name || t.name || "?";
      const label = toolLabel(toolName);
      const result = renderToolResultInline(t, toolName);
      actionItems.push(
        `<span class="llm-ov-item">` +
          `<span class="llm-ov-num">${stepNum++}</span>` +
          `<span class="llm-ov-tool">${escapeHtml(label)}</span>` +
          `<span class="llm-ov-arrow">→</span>` +
          result +
        `</span>`
      );
    }
  }

  const items = [...thinkItems];
  if (actionItems.length) {
    const sep = `<span class="llm-ov-sep">·</span>`;
    items.push(
      `<li class="llm-ov-step llm-ov-step-action">` +
        `<span class="llm-ov-bullet">→</span>` +
        `<div class="llm-ov-body">` +
          `<div class="llm-ov-chain">${actionItems.join(sep)}</div>` +
        `</div>` +
      `</li>`
    );
  }

  if (!items.length) return "";
  return `<div class="llm-turn-overview"><ol class="llm-ov-flow">${items.join("")}</ol></div>`;
}

// 单个工具调用的内联结果（无截断、保留完整首行）
function renderToolResultInline(tool, toolName) {
  if (!tool || !tool.hasResult) {
    return `<span class="llm-ov-res pending">执行中<span class="llm-ov-pending-dots" aria-hidden="true"><i></i><i></i><i></i></span></span>`;
  }
  if (tool.ok === false) {
    return `<span class="llm-ov-res fail">失败 · ${escapeHtml(tool.error || toolName)}</span>`;
  }
  if (!isMutatingTool(toolName)) {
    return `<span class="llm-ov-res ok">成功</span>`;
  }
  // 写操作：取首行，把工具名替换为中文
  const raw = (tool.content || "").split("\n")[0];
  // 模式 1："成功 <tool>: <rest>" → 只显示 <rest>（chip 已标工具，pill 简洁）
  const escapedName = toolName.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const colonRe = new RegExp(`^成功\\s+${escapedName}\\s*[:：]\\s*(.+)$`, "i");
  const colonMatch = raw.match(colonRe);
  if (colonMatch) {
    return `<span class="llm-ov-res ok">${escapeHtml(colonMatch[1])}</span>`;
  }
  // 模式 2：把内容中的工具名替换为中文
  const labeled = raw.replace(new RegExp(`\\b${escapedName}\\b`, "g"), toolLabel(toolName));
  return `<span class="llm-ov-res ok">${escapeHtml(labeled || "成功")}</span>`;
}

const MUTATING_TOOL_NAMES = new Set([
  "craft_equipment", "purchase_upgrade", "allocate_experience",
  "recruit_adventurer", "dismiss_adventurer", "equip_item",
  "unequip_item", "end_turn", "write_memo",
]);

function isMutatingTool(name) {
  return MUTATING_TOOL_NAMES.has(name);
}

function renderTurnEntry(entry) {
  if (entry.kind === "summary") return renderRunSummary(entry);
  if (entry.kind === "retry") return `<div class="llm-retry">重试提示：${escapeHtml(entry.text)}</div>`;
  if (entry.kind !== "model") return "";
  const entryId = entry.id || `model-${entry.turn || "na"}-${entry.step || "na"}`;
  const meta = renderModelMeta(entry.timing, entry.usage);
  const promptButton = entry.request ? `
    <button type="button" class="model-prompt-button" data-model-id="${escapeHtml(entryId)}">查看输入</button>
  ` : "";
  return `
    <div class="llm-model-message" data-model-id="${escapeHtml(entryId)}">
      <div class="row-title">
        <strong>模型行为</strong>
        <span class="small muted">T${escapeHtml(entry.turn)} · step ${escapeHtml(entry.step)}</span>
        ${promptButton}
      </div>
      ${meta}
      <div class="llm-model-body">
        ${renderModelBodyContent(entry, entryId)}
      </div>
    </div>
  `;
}

function renderTurnToolsSection(tools) {
  return `
    <div class="llm-turn-tools">
      <div class="llm-section-label">本回合工具调用</div>
      ${tools.map((item) => renderTurnToolItem(item)).join("")}
    </div>
  `;
}

function renderTurnToolItem(item) {
  const toolId = toolTraceId(item);
  const ok = item.ok;
  const hasResult = item.hasResult === true;
  let cls = "muted";
  let label = "等待结果";
  if (ok === false) {
    cls = "danger";
    label = "失败";
  } else if (hasResult) {
    // 收到 tool_result 事件后，只要不是 ok===false，就视为成功
    cls = "ok";
    label = "成功";
  }
  const open = state.llm.openToolTrace.has(toolId) || ok === false ? "open" : "";
  const callId = item.callId ? escapeHtml(item.callId.slice(-6)) : "";
  const args = escapeHtml(JSON.stringify(item.arguments || {}, null, 2));
  const received = item.content || "";
  return `
    <details class="llm-tool-item" data-tool-id="${escapeHtml(toolId)}" ${open}>
      <summary>
        <span><strong>${escapeHtml(item.name || "tool")}</strong> <span class="muted small">${callId ? `· #${callId}` : ""}</span></span>
        <span class="llm-tool-tag ${cls}">${label}</span>
      </summary>
      <div class="llm-json-label">参数</div>
      <pre>${args}</pre>
      ${received ? `
        <div class="llm-json-label">模型收到</div>
        <pre>${escapeHtml(received)}</pre>
      ` : ""}
    </details>
  `;
}

function renderModelBodyContent(entry, entryId) {
  // 工具调用在回合块下方的"本回合工具调用"区域统一展示，这里不再嵌入
  return [
    renderModelThinking(entry, entryId),
    renderModelReply(entry.text),
  ].filter(Boolean).join("");
}

function renderModelThinking(entry, entryId) {
  if (!entry.reasoningText) {
    return "";
  }
  const thinkingOpen = state.llm.openThinking.has(entryId) ? "open" : "";
  return `
    <details class="llm-thinking" data-entry-id="${escapeHtml(entryId)}" ${thinkingOpen}>
      <summary>
        <span>思考内容</span>
        <span class="small muted">${entry.reasoningText.length} chars</span>
      </summary>
      <pre>${escapeHtml(entry.reasoningText)}</pre>
    </details>
  `;
}

function renderModelReply(text) {
  if (!text || !text.trim()) {
    return "";
  }
  return `
    <div class="llm-model-reply">
      <div class="llm-json-label">回复内容</div>
      <pre>${escapeHtml(text)}</pre>
    </div>
  `;
}

function renderModelToolCalls(toolCalls) {
  if (!toolCalls?.length) {
    return "";
  }
  const names = toolCalls.map((call) => call.name || "unknown").join(" · ");
  return `
    <details class="llm-model-tool-calls">
      <summary>
        <span>工具调用</span>
        <span class="small muted">${escapeHtml(names)}</span>
      </summary>
      ${toolCalls.map((call, index) => renderModelToolCall(call, index)).join("")}
    </details>
  `;
}

function renderModelToolCall(call, index) {
  const callId = call.id || call.call_id || call.callId || "";
  return `
    <div class="llm-model-tool-call">
      <div class="row-title">
        <strong>${escapeHtml(call.name || `tool_${index + 1}`)}</strong>
        <span class="small muted">${escapeHtml(callId || `#${index + 1}`)}</span>
      </div>
      <div class="llm-json-label">参数</div>
      <pre>${escapeHtml(formatToolArguments(call.arguments))}</pre>
    </div>
  `;
}

function formatToolArguments(argumentsValue) {
  if (argumentsValue === undefined || argumentsValue === null) {
    return "{}";
  }
  if (typeof argumentsValue === "string") {
    try {
      return JSON.stringify(JSON.parse(argumentsValue), null, 2);
    } catch {
      return argumentsValue;
    }
  }
  return JSON.stringify(argumentsValue, null, 2);
}

function streamUpdateModelEntry(entry) {
  const entryId = entry.id || `model-${entry.turn || "na"}-${entry.step || "na"}`;
  const container = $("llmTranscript");
  const existing = container.querySelector(`[data-model-id="${entryId}"]`);
  if (!existing) {
    $("llmTranscript").innerHTML = renderLlmTranscript();
    return;
  }
  updateModelMeta(existing, entry);
  updateModelThinking(existing, entry, entryId);
  updateModelReply(existing, entry.text);
  updateModelToolCalls(existing, entry.toolCalls);
}

function updateModelThinking(container, entry, entryId) {
  const body = container.querySelector(".llm-model-body");
  if (!body) {
    return;
  }
  const existing = body.querySelector(".llm-thinking");
  if (!entry.reasoningText) {
    existing?.remove();
    return;
  }
  if (!existing) {
    body.insertAdjacentHTML("afterbegin", renderModelThinking(entry, entryId));
    return;
  }
  const shouldOpen = state.llm.openThinking.has(entryId);
  if (existing.open !== shouldOpen) {
    existing.open = shouldOpen;
  }
  const count = existing.querySelector(".small");
  if (count) {
    count.textContent = `${entry.reasoningText.length} chars`;
  }
  const pre = existing.querySelector("pre");
  if (pre) {
    pre.textContent = entry.reasoningText;
  }
}

function updateModelReply(container, text) {
  const body = container.querySelector(".llm-model-body");
  if (!body) {
    return;
  }
  const existing = body.querySelector(".llm-model-reply");
  if (!text || !text.trim()) {
    existing?.remove();
    return;
  }
  if (existing) {
    const pre = existing.querySelector("pre");
    if (pre) {
      pre.textContent = text;
    }
    return;
  }
  const toolCalls = body.querySelector(".llm-model-tool-calls");
  const html = renderModelReply(text);
  if (toolCalls) {
    toolCalls.insertAdjacentHTML("beforebegin", html);
  } else {
    body.insertAdjacentHTML("beforeend", html);
  }
}

function updateModelToolCalls(container, toolCalls) {
  const body = container.querySelector(".llm-model-body");
  if (!body) {
    return;
  }
  const existing = body.querySelector(".llm-model-tool-calls");
  if (!toolCalls?.length) {
    existing?.remove();
    return;
  }
  const html = renderModelToolCalls(toolCalls);
  if (existing) {
    existing.outerHTML = html;
  } else {
    body.insertAdjacentHTML("beforeend", html);
  }
}

function updateModelMeta(container, entry) {
  const metaHtml = renderModelMeta(entry.timing, entry.usage);
  const existingMeta = container.querySelector(".llm-model-meta");
  if (existingMeta) {
    existingMeta.outerHTML = metaHtml;
    return;
  }
  const rowTitle = container.querySelector(".row-title");
  if (rowTitle) {
    rowTitle.insertAdjacentHTML("afterend", metaHtml);
  }
}

function renderLlmToolTrace() {
  if (!state.llm.toolTrace.length) {
    return renderEmptyState("🔧", "尚无工具调用", "模型每次调用工具的参数、结果和状态会在这里按时间倒序展示");
  }
  return state.llm.toolTrace.slice(-120).reverse().map((item) => {
    const ok = item.ok;
    const hasResult = item.hasResult === true;
    let cls = "muted";
    let label = "等待结果";
    if (ok === false) {
      cls = "danger";
      label = "失败";
    } else if (hasResult) {
      cls = "ok";
      label = "成功";
    }
    const toolId = toolTraceId(item);
    const open = state.llm.openToolTrace.has(toolId) || ok === false ? "open" : "";
    const received = item.content || "";
    return `
      <details class="llm-tool-item" data-tool-id="${escapeHtml(toolId)}" ${open}>
        <summary>
          <span><strong>${escapeHtml(item.name || "tool")}</strong> <span class="muted small">· T${escapeHtml(item.turn ?? "—")}${item.callId ? ` · ${escapeHtml(item.callId.slice(-6))}` : ""}</span></span>
          <span class="llm-tool-tag ${cls}">${label}</span>
        </summary>
        <div class="llm-json-label">参数</div>
        <pre>${escapeHtml(JSON.stringify(item.arguments || {}, null, 2))}</pre>
        ${received ? `
          <div class="llm-json-label">模型收到</div>
          <pre>${escapeHtml(received)}</pre>
        ` : ""}
      </details>
    `;
  }).join("");
}

function renderLlmEventLog() {
  if (!state.llm.events.length) {
    return renderEmptyState("📡", "事件流空闲中", "启动运行后，最近 120 条服务端原始事件会追加到这里，折叠查看");
  }
  return state.llm.events.slice(-120).reverse().map((event) => {
    const type = event.type || "event";
    const tagCls = eventTypeTagClass(type);
    return `
      <details class="llm-event">
        <summary>
          <span class="llm-event-type ${tagCls}">${escapeHtml(type)}</span>
          <span class="muted small">${formatLlmEventSummary(event)}</span>
        </summary>
        <pre>${escapeHtml(JSON.stringify(event, null, 2))}</pre>
      </details>
    `;
  }).join("");
}

function eventTypeTagClass(type) {
  if (!type) return "";
  if (/(error|fail|reject)/i.test(type)) return "error";
  if (/(complete|finish|done|success)/i.test(type)) return "ok";
  if (/(retry|warn|partial)/i.test(type)) return "warn";
  return "";
}

function formatLlmEventSummary(event) {
  if (!event || typeof event !== "object") return "";
  if (typeof event.turn !== "undefined" && event.turn !== null) {
    return `T${event.turn}${event.step != null ? ` · step ${event.step}` : ""}`;
  }
  if (event.archive?.directory) {
    return escapeHtml(event.archive.directory);
  }
  if (event.name) {
    return escapeHtml(event.name);
  }
  if (event.type === "model_delta" || event.type === "model_reasoning_delta") {
    return event.text_preview || "stream chunk";
  }
  if (event.error) {
    return escapeHtml(String(event.error).slice(0, 80));
  }
  return "";
}

function summarizeToolResult(result) {
  const summary = { ...result };
  if (summary.observation) {
    summary.observation = summarizeObservation(summary.observation);
  }
  if (summary.turn_result?.battles) {
    summary.turn_result = {
      ...summary.turn_result,
      battles: summary.turn_result.battles.map((battle) => ({
        adventurer_id: battle.adventurer_id,
        monster_id: battle.monster_id,
        won: battle.won,
        reward: battle.reward,
      })),
    };
  }
  return summary;
}

function summarizeObservation(obs) {
  return {
    session_id: obs.session_id,
    turn: obs.turn,
    max_turns: obs.max_turns,
    finished: obs.finished,
    gold: obs.gold,
    experience_pool: obs.experience_pool,
    seed: obs.seed,
    scoring_seed: obs.scoring?.seed,
    materials: obs.materials,
    adventurers: obs.adventurers?.map((a) => ({
      id: a.adventurer_id,
      name: a.name,
      level: a.level,
      hp: `${a.resources.current_hp}/${a.effective_stats.hp}`,
      mp: `${a.resources.current_mp}/${a.effective_stats.mp}`,
    })),
    monsters: obs.monsters?.map((m) => ({
      id: m.monster_id,
      name: m.name,
      hp: m.stats.hp,
      attack: m.stats.attack,
    })),
  };
}

function compactLlmEvent(event) {
  const compact = { ...event };
  if (compact.type === "model_delta" || compact.type === "model_reasoning_delta") {
    const text = typeof compact.text === "string" ? compact.text : "";
    compact.text_length = text.length;
    compact.text_preview = text.length > 80 ? `${text.slice(0, 80)}...` : text;
    delete compact.text;
  }
  if (compact.type === "tool_call_delta") {
    const delta = typeof compact.arguments_delta === "string" ? compact.arguments_delta : "";
    compact.arguments_delta_length = delta.length;
    compact.arguments_delta_preview = delta.length > 80 ? `${delta.slice(0, 80)}...` : delta;
    delete compact.arguments_delta;
  }
  if (compact.request) {
    compact.request = {
      message_count: compact.request.messages?.length || 0,
      tool_names: compact.request.tools?.map((tool) => tool.name || tool.function?.name).filter(Boolean) || [],
    };
  }
  if (compact.raw) {
    compact.raw = {
      kind: compact.raw?.stream ? "stream" : Array.isArray(compact.raw?.chunks) ? "stream_chunks" : "response",
      chunk_count: compact.raw?.chunk_count ?? compact.raw?.chunks?.length,
      finish_reason: compact.raw?.finish_reason,
      usage: compact.raw?.usage,
    };
  }
  if (compact.type === "tool_result" && compact.result) {
    compact.result = {
      ok: compact.result.ok,
      error: compact.result.error,
    };
  }
  if (compact.observation) compact.observation = summarizeObservation(compact.observation);
  if (compact.result?.observation) compact.result = summarizeToolResult(compact.result);
  if (compact.run?.final_observation) {
    compact.run = {
      ...compact.run,
      final_observation: summarizeObservation(compact.run.final_observation),
    };
  }
  if (compact.prompt && compact.prompt.length > 500) {
    compact.prompt = compact.prompt.slice(0, 500) + "...";
  }
  if (compact.content && compact.content.length > 500) {
    compact.content = compact.content.slice(0, 500) + "...";
  }
  return compact;
}

async function refreshLlmReplayRuns() {
  const replayState = state.llm.replay;
  replayState.status = "正在刷新归档";
  renderLlmDebug();
  try {
    const response = await fetch("/api/llm/runs");
    const data = await readJsonOrThrow(response, "刷新归档失败");
    replayState.runs = data.runs || [];
    if (!replayState.selectedRunId && replayState.runs.length) {
      replayState.selectedRunId = replayState.runs[0].run_id;
    }
    replayState.status = replayState.runs.length
      ? `找到 ${replayState.runs.length} 个归档`
      : "尚无归档";
  } catch (error) {
    replayState.status = error.message || "刷新归档失败";
  }
  renderLlmDebug();
}

async function loadSelectedLlmReplay() {
  const runId = state.llm.replay.selectedRunId || $("llmReplaySelect")?.value;
  if (!runId) {
    state.llm.replay.status = "请选择一个归档";
    renderLlmDebug();
    return;
  }
  state.llm.replay.status = "正在加载 replay";
  renderLlmDebug();
  try {
    let response = await fetch(`/api/llm/runs/${encodeURIComponent(runId)}/replay`);
    let replay = await readJsonOrThrow(response, "加载 replay 失败");
    if (!hasReplayObservations(replay)) {
      state.llm.replay.status = "缺少回合快照，正在重建";
      renderLlmDebug();
      replay = await postLlmReplayMaintenance(runId, "rebuild", "重建 replay 失败");
    } else if (needsReplayRankScores(replay)) {
      state.llm.replay.status = "缺少段位分，正在补全";
      renderLlmDebug();
      replay = await postLlmReplayMaintenance(runId, "rescore", "补全段位分失败");
    }
    updateLoadedRunMetadata(runId, replay);
    setLlmReplay(replay, runId);
  } catch (error) {
    state.llm.replay.status = error.message || "加载 replay 失败";
    renderLlmDebug();
  }
}

async function postLlmReplayMaintenance(runId, action, fallbackMessage) {
  const response = await fetch(`/api/llm/runs/${encodeURIComponent(runId)}/${action}`, {
    method: "POST",
  });
  return readJsonOrThrow(response, fallbackMessage);
}

async function readJsonOrThrow(response, fallbackMessage) {
  const contentType = response.headers.get("content-type") || "";
  if (contentType.includes("application/json")) {
    let data;
    try {
      data = await response.json();
    } catch {
      throw new Error(fallbackMessage);
    }
    if (!response.ok) {
      throw new Error(data?.detail || data?.error || fallbackMessage);
    }
    return data;
  }
  const text = (await response.text()).trim();
  if (!response.ok) {
    throw new Error(text || fallbackMessage);
  }
  throw new Error(fallbackMessage);
}

function hasReplayObservations(replay) {
  const turns = replay?.turns;
  return Array.isArray(turns) && turns.length > 0 && turns[0]?.observation_before != null;
}

function needsReplayRankScores(replay) {
  if (!replay || typeof replay !== "object") return false;
  const turns = Array.isArray(replay.turns) ? replay.turns : [];
  const hasFinalObservation = replay.final_observation && typeof replay.final_observation === "object";
  const score = replay.score && typeof replay.score === "object" ? replay.score : null;
  if (hasFinalObservation && (!score || score.rank_score == null)) return true;
  if (hasFinalObservation && !hasReplayRankScoreContributions(score)) return true;
  return turns.some((turn, index) => {
    if (!turn || typeof turn !== "object" || turn.status !== "completed" || turn.rank_score != null) {
      return false;
    }
    const nextTurn = turns[index + 1];
    return Boolean(
      (nextTurn && nextTurn.observation_before) ||
      (index === turns.length - 1 && hasFinalObservation)
    );
  });
}

function hasReplayRankScoreContributions(score) {
  if (!score || typeof score !== "object") return false;
  return replayRankContributionItems(score).length > 0;
}

function updateLoadedRunMetadata(runId, replay) {
  const run = state.llm.replay.runs.find((item) => item.run_id === runId);
  if (!run || !replay || typeof replay !== "object") return;
  run.has_observations = hasReplayObservations(replay);
  if (replay.score && typeof replay.score === "object") {
    run.score = replay.score.score;
    run.rank_score = replay.score.rank_score;
  }
}

function resumeSelectedLlmReplay() {
  const runId = state.llm.replay.selectedRunId || $("llmReplaySelect")?.value;
  if (!runId) {
    state.llm.replay.status = "请选择一个归档";
    renderLlmDebug();
    return;
  }
  startLlmDebug({ resumeRunId: runId });
}

async function loadLlmReplayFile(event) {
  const file = event.target.files?.[0];
  if (!file) return;
  try {
    const replay = JSON.parse(await file.text());
    setLlmReplay(replay, file.name);
  } catch (error) {
    state.llm.replay.status = `读取 replay 失败：${error.message || "invalid JSON"}`;
    renderLlmDebug();
  } finally {
    event.target.value = "";
  }
}

function setLlmReplay(replay, source) {
  if (!replay || typeof replay !== "object" || !Array.isArray(replay.turns)) {
    state.llm.replay.status = "replay 格式不正确：缺少 turns";
    renderLlmDebug();
    return;
  }
  state.llm.replay.data = replay;
  state.llm.replay.source = source;
  state.llm.replay.status = `已加载 ${source} · ${replay.turns.length} 回合 · ${replay.status || "unknown"}`;
  setSeedInputIfEmpty("llmGameSeed", replay.final_observation?.seed ?? replay.data?.game_seed);
  setSeedInputIfEmpty(
    "llmScoringSeed",
    replay.score?.seed ?? replay.final_observation?.scoring?.seed ?? replay.data?.scoring_seed,
  );
  renderLlmDebug();
}

function setSeedInputIfEmpty(id, value) {
  const input = $(id);
  if (!input || input.value || value === undefined || value === null) {
    return;
  }
  input.value = String(value);
}

function renderLlmReplayControls() {
  const select = $("llmReplaySelect");
  if (!select) return;
  const selected = state.llm.replay.selectedRunId || select.value;
  select.innerHTML = state.llm.replay.runs.length
    ? state.llm.replay.runs.map((run) => {
      const preset = run.preset ? ` · ${run.preset}` : "";
      const rank = run.rank_score !== undefined && run.rank_score !== null
        ? ` · 段位 ${Math.round(run.rank_score)}`
        : "";
      const label = `${run.created_at || run.run_id} · ${run.status || "unknown"}${preset} · ${run.turns || 0} 回合${rank}`;
      return `<option value="${escapeHtml(run.run_id)}">${escapeHtml(label)}</option>`;
    }).join("")
    : '<option value="">无归档</option>';
  if (selected) {
    select.value = selected;
    state.llm.replay.selectedRunId = select.value;
  }
}

function replayRankContributionItems(score) {
  if (!score || typeof score !== "object") return [];
  const values = Array.isArray(score.rank_score_per_adventurer)
    ? score.rank_score_per_adventurer
    : Array.isArray(score.per_adventurer)
      ? score.per_adventurer
      : [];
  return values
    .filter((item) => item && typeof item === "object")
    .map((item) => {
      const scoreValue = item.rank_score_contribution ?? item.rank_score;
      return {
        name: item.name || item.adventurer_id || "?",
        score: Number(scoreValue),
        share: item.rank_score_share != null ? Number(item.rank_score_share) : null,
      };
    })
    .filter((item) => Number.isFinite(item.score))
    .sort((a, b) => b.score - a.score);
}

function renderReplayRankContributors(score) {
  const items = replayRankContributionItems(score);
  if (!items.length) return "";
  return `
    <div class="llm-replay-summary">
      ${items.map((item) => {
        const share = item.share != null ? ` · ${(item.share * 100).toFixed(1)}%` : "";
        return `<span>${escapeHtml(item.name)}：${escapeHtml(item.score.toLocaleString(undefined, { maximumFractionDigits: 1 }))}${share}</span>`;
      }).join("")}
    </div>
  `;
}

function renderLlmReplay() {
  const replay = state.llm.replay.data;
  if (!replay) {
    return renderEmptyState("🎬", "尚未选择归档", "从顶部下拉选择一个 run，或点击「打开 replay.json」导入本地归档");
  }
  const gameSeed = replay.final_observation?.seed;
  const scoringSeed = replay.score?.seed ?? replay.final_observation?.scoring?.seed;
  const scoreText = formatScore(replay.score);
  const rankContrib = renderReplayRankContributors(replay.score);
  const stats = computeReplayStats(replay);
  const statsBadges = stats ? renderReplayStatsBadges(stats) : "";
  const summary = `
    <div class="llm-replay-summary">
      <span>来源：${escapeHtml(state.llm.replay.source || "replay.json")}</span>
      <span>会话：${escapeHtml(replay.session_id || "n/a")}</span>
      <span>状态：${escapeHtml(replay.status || "unknown")}</span>
      ${gameSeed !== undefined ? `<span>Game seed：${escapeHtml(gameSeed)}</span>` : ""}
      ${scoringSeed !== undefined ? `<span>Scoring seed：${escapeHtml(scoringSeed)}</span>` : ""}
      <span>Preset：${escapeHtml(replay.data?.preset || "custom")}</span>
      <span>Data hash：${escapeHtml(shortHash(replay.data?.data_hash))}</span>
      <span>回合：${escapeHtml(String(replay.turns?.length || 0))}</span>
      ${scoreText ? `<span>${escapeHtml(scoreText)}</span>` : ""}
      ${statsBadges}
    </div>
    ${rankContrib}
  `;
  const turns = (replay.turns || []).map((turn) => renderReplayTurn(turn)).join("");
  return summary + turns;
}

function renderReplayTurn(turn) {
  const steps = (turn.steps || []).map((step, index) => renderReplayStep(step, index + 1)).join("");
  const open = turn.status === "failed" ? "open" : "";
  const timingHtml = renderTurnTimingUsage(turn.timing_usage);
  return `
    <details class="llm-replay-turn" ${open}>
      <summary>
        <span>第 ${escapeHtml(turn.turn)} 回合</span>
        <strong>${escapeHtml(turn.status || "unknown")}</strong>
        ${timingHtml}
      </summary>
      ${turn.failure_reason ? `<div class="llm-retry">失败原因：${escapeHtml(turn.failure_reason)}</div>` : ""}
      <div class="llm-replay-steps">${steps}</div>
    </details>
  `;
}

function renderReplayStep(step, index) {
  if (step.type === "turn_prompt") {
    return `
      <details class="llm-replay-step">
        <summary>${index}. Turn prompt</summary>
        <pre>${escapeHtml(step.content || "")}</pre>
      </details>
    `;
  }
  if (step.type === "retry_prompt") {
    return `<div class="llm-retry">${index}. 重试提示：${escapeHtml(step.content || "")}</div>`;
  }
  if (step.type === "assistant") {
    const toolCalls = step.tool_calls?.length
      ? `<div class="llm-json-label">工具调用</div><pre>${escapeHtml(JSON.stringify(step.tool_calls, null, 2))}</pre>`
      : "";
    const reasoning = step.reasoning_content
      ? `<div class="llm-json-label">reasoning_content</div><pre>${escapeHtml(step.reasoning_content)}</pre>`
      : "";
    const meta = renderModelMeta(step.timing, step.usage);
    return `
      <details class="llm-replay-step" open>
        <summary>${index}. Assistant</summary>
        ${meta}
        <pre>${escapeHtml(step.content || "（无文本，可能只请求工具）")}</pre>
        ${reasoning}
        ${toolCalls}
      </details>
    `;
  }
  if (step.type === "tool_result") {
    const content = step.content || "";
    return `
      <details class="llm-replay-step">
        <summary>${index}. Tool · ${escapeHtml(step.name || "unknown")}</summary>
        <div class="small muted">call_id：${escapeHtml(step.call_id || "n/a")}</div>
        <div class="llm-json-label">参数</div>
        <pre>${escapeHtml(JSON.stringify(step.arguments || {}, null, 2))}</pre>
        ${content ? `
          <div class="llm-json-label">模型收到</div>
          <pre>${escapeHtml(content)}</pre>
        ` : '<div class="small muted">旧归档缺少模型可见的工具返回文本</div>'}
      </details>
    `;
  }
  return `
    <details class="llm-replay-step">
      <summary>${index}. ${escapeHtml(step.type || "step")}</summary>
      <pre>${escapeHtml(JSON.stringify(step, null, 2))}</pre>
    </details>
  `;
}

function renderModelMeta(timing, usage) {
  const parts = [];
  const duration = Number(timing?.duration_ms);
  if (Number.isFinite(duration)) {
    parts.push(`耗时 ${formatDurationMs(duration)}`);
  } else {
    parts.push("耗时统计中");
  }
  const usageText = formatUsage(usage);
  if (usageText) {
    parts.push(usageText);
  } else {
    parts.push("tokens n/a");
  }
  return `<div class="llm-model-meta">${parts.map((part) => `<span>${escapeHtml(part)}</span>`).join("")}</div>`;
}

function formatDurationMs(value) {
  if (value < 1000) {
    return `${Math.round(value)} ms`;
  }
  return `${(value / 1000).toFixed(2)} s`;
}

function formatUsage(usage) {
  if (!usage || typeof usage !== "object") {
    return "";
  }
  const prompt = usage.prompt_tokens ?? usage.input_tokens;
  const completion = usage.completion_tokens ?? usage.output_tokens;
  const total = usage.total_tokens;
  const parts = [];
  if (prompt != null) parts.push(`in ${prompt}`);
  if (completion != null) parts.push(`out ${completion}`);
  if (total != null) parts.push(`total ${total}`);
  return parts.length ? `tokens ${parts.join(" · ")}` : "";
}

function renderTurnTimingUsage(tu) {
  if (!tu) return "";
  const parts = [];
  const ms = Number(tu.duration_ms);
  if (Number.isFinite(ms) && ms > 0) {
    parts.push(formatDurationMs(ms));
  } else {
    parts.push("—");
  }
  const inTok = tu.input_tokens ?? tu.prompt_tokens;
  const outTok = tu.output_tokens ?? tu.completion_tokens;
  parts.push(inTok != null ? `in ${inTok}` : "in —");
  parts.push(outTok != null ? `out ${outTok}` : "out —");
  return `<span class="llm-turn-meta">${escapeHtml(parts.join(" · "))}</span>`;
}

function formatScore(score) {
  if (!score || typeof score !== "object") {
    return "";
  }
  const hasScore = score.score !== undefined && score.score !== null;
  const hasRank = score.rank_score !== undefined && score.rank_score !== null;
  if (!hasScore && !hasRank) {
    return "";
  }
  let text = hasScore ? `得分 ${score.score}` : "";
  if (score.rank_score !== undefined && score.rank_score !== null) {
    text += `${text ? " · " : ""}段位 ${Math.round(score.rank_score)}`;
  }
  return text;
}

function renderRunSummary(entry) {
  const stats = entry.stats;
  if (!stats) return "";
  const isFailed = !!entry.failureReason;
  const titleParts = [];
  if (isFailed) {
    titleParts.push(`运行失败：${escapeHtml(entry.failureReason || "unknown")}`);
  } else {
    titleParts.push("运行总结");
    const scoreText = formatScore(entry.score);
    if (scoreText) titleParts.push(escapeHtml(scoreText));
  }
  if (entry.turns) titleParts.push(`${entry.turns} 回合`);

  const badges = [];
  // Timing
  const timing = stats.timing;
  if (timing && (timing.total_duration_ms > 0 || timing.total_duration_seconds > 0)) {
    badges.push(`⏱ 耗时 ${formatDurationMs(timing.total_duration_ms || 0)}`);
  }
  // Tokens
  const tokens = stats.token_usage;
  if (tokens) {
    const tokenParts = [];
    if (tokens.input_tokens) tokenParts.push(`in ${tokens.input_tokens.toLocaleString()}`);
    if (tokens.output_tokens) tokenParts.push(`out ${tokens.output_tokens.toLocaleString()}`);
    if (tokenParts.length) badges.push(`🔤 tokens ${tokenParts.join(" · ")}`);
    if (tokens.cache_read_input_tokens) badges.push(`📦 缓存命中 ${tokens.cache_read_input_tokens.toLocaleString()}`);
  }
  // Tool calls
  const tc = stats.tool_calls;
  if (tc && tc.total > 0) {
    const breakdown = formatToolBreakdown(tc);
    badges.push(`🔧 工具调用 ${tc.total} 次 (✓ ${tc.successful} · ✗ ${tc.failed})${breakdown ? `：${breakdown}` : ""}`);
  }
  // Game actions
  const ga = stats.game_actions;
  if (ga) {
    if (ga.battles_total > 0) badges.push(`⚔ 战斗 ${ga.battles_won}/${ga.battles_total} 胜`);
    if (ga.total_gold_earned > 0) badges.push(`💰 金币收入 ${ga.total_gold_earned.toLocaleString()}`);
    if (ga.total_experience_earned > 0) badges.push(`⭐ 经验收入 ${ga.total_experience_earned.toLocaleString()}`);
    if (ga.total_equipment_crafted > 0) badges.push(`⚒ 合成 ${ga.total_equipment_crafted} 件`);
    if (ga.total_upgrades_purchased > 0) badges.push(`📈 升级 ${ga.total_upgrades_purchased} 个`);
    if (ga.total_recruits > 0) badges.push(`👥 招募 ${ga.total_recruits} 人`);
    if (ga.total_experience_allocated > 0) badges.push(`💫 分配经验 ${ga.total_experience_allocated} 次`);
    if (ga.total_equips > 0) badges.push(`🗡 装备 ${ga.total_equips} 次`);
    if (ga.strongest_defeated_enemy?.name) {
      badges.push(`🏆 最强击败 ${ga.strongest_defeated_enemy.name} 强度 ${ga.strongest_defeated_enemy.power ?? "—"}`);
    }
  }
  // Model interaction
  const mi = stats.model_interaction;
  if (mi && mi.total_model_steps > 0) {
    badges.push(`🧠 模型步数 ${mi.total_model_steps}`);
  }

  return `
    <div class="llm-run-summary${isFailed ? " failed" : ""}">
      <div class="llm-run-summary-title">${titleParts.join(" · ")}</div>
      <div class="llm-replay-summary">
        ${badges.map((b) => `<span>${escapeHtml(b)}</span>`).join("")}
      </div>
    </div>
  `;
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
  return true;
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

function toolLabel(name) {
  const labels = {
    get_party: "查看队伍",
    get_monsters: "查看怪物",
    get_crafting: "查看制作",
    get_inventory: "查看背包",
    get_upgrades: "查看升级",
    get_recruitment: "查看招募",
    get_events: "查看事件",
    preview_battle: "预览战斗",
    preview_team_power: "预览战力",
    craft_equipment: "制作装备",
    purchase_upgrade: "购买升级",
    allocate_experience: "分配经验",
    recruit_adventurer: "招募冒险者",
    dismiss_adventurer: "遣散冒险者",
    equip_item: "装备物品",
    unequip_item: "卸下装备",
    end_turn: "结束回合",
    write_memo: "写备忘",
  };
  return labels[name] || name || "未知工具";
}

function strongestDefeatedEnemyFromStep(step, observation, turnNumber) {
  const battles = step?.result?.turn_result?.battles;
  let best = null;
  if (Array.isArray(battles)) {
    for (const battle of battles) {
      if (!battle || typeof battle !== "object" || battleWon(battle) !== true) continue;
      best = strongerEnemy(best, defeatedEnemyFromBattle(battle, observation, turnNumber));
    }
    return best;
  }
  return strongestDefeatedEnemyFromText(step?.content, observation, turnNumber);
}

function strongestDefeatedEnemyFromText(content, observation, turnNumber) {
  if (typeof content !== "string") return null;
  let best = null;
  for (const line of content.split(/\r?\n/)) {
    if (!/^\s*-/.test(line) || !line.includes(" vs ")) continue;
    const match = line.match(/^\s*-\s+(?:(\d+)\s+)?(.+?)\s+vs\s+(?:(\d+)\s+)?(.+?)[:：]\s*([^;；]+)/i);
    if (!match) continue;
    const outcome = String(match[5] || "").trim().toLowerCase();
    if (outcome.includes("负") || ["right_win", "monster_win", "enemy_win", "loss", "lost", "defeat"].includes(outcome)) continue;
    if (!outcome.includes("胜") && !["left_win", "adventurer_win", "player_win", "win", "won", "victory"].includes(outcome)) continue;
    const battle = { monster_name: match[4].trim() };
    const monsterId = monsterIdFromObservationRef(observation, match[3]);
    if (monsterId != null) battle.monster_id = monsterId;
    best = strongerEnemy(best, defeatedEnemyFromBattle(battle, observation, turnNumber));
  }
  return best;
}

function defeatedEnemyFromBattle(battle, observation, turnNumber) {
  const monster = monsterFromObservation(battle, observation);
  const statsSource = (monster && typeof monster.stats === "object") ? monster.stats : (battle.monster_stats || battle.stats);
  const stats = numericMap(statsSource);
  if (!Object.keys(stats).length) return null;
  const rewardSource = (monster && typeof monster.reward === "object") ? monster.reward : battle.reward;
  const reward = numericMap(rewardSource);
  const monsterId = monster?.monster_id ?? battle.monster_id ?? null;
  const name = battle.monster_name ?? monster?.name ?? battle.monster ?? monsterId;
  const result = {
    turn: turnNumber,
    monster_id: monsterId != null ? String(monsterId) : null,
    name: name != null ? String(name) : null,
    power: monsterPower(stats),
    stats,
  };
  if (Object.keys(reward).length) result.reward = reward;
  if (monster?.tier != null) result.tier = monster.tier;
  if (monster?.archetype_id != null) result.archetype_id = monster.archetype_id;
  return result;
}

function monsterFromObservation(battle, observation) {
  const monsters = Array.isArray(observation?.monsters) ? observation.monsters : [];
  const monsterId = battle?.monster_id != null ? String(battle.monster_id) : null;
  if (monsterId) {
    const found = monsters.find(monster => monster && String(monster.monster_id) === monsterId);
    if (found) return found;
  }
  const monsterName = battle?.monster_name ?? battle?.monster;
  if (monsterName != null) {
    const found = monsters.find(monster => monster && monster.name === String(monsterName));
    if (found) return found;
  }
  return null;
}

function monsterIdFromObservationRef(observation, ref) {
  const index = Number.parseInt(ref, 10) - 1;
  const monsters = Array.isArray(observation?.monsters) ? observation.monsters : [];
  if (!Number.isInteger(index) || index < 0 || index >= monsters.length) return null;
  const monsterId = monsters[index]?.monster_id;
  return monsterId != null ? String(monsterId) : null;
}

function numericMap(value) {
  if (!value || typeof value !== "object") return {};
  const result = {};
  for (const [key, raw] of Object.entries(value)) {
    const num = Number(raw);
    if (Number.isFinite(num)) result[key] = Math.trunc(num);
  }
  return result;
}

function monsterPower(stats) {
  return (stats.hp || 0)
    + (stats.mp || 0)
    + (stats.attack || 0) * 8
    + (stats.defense || 0) * 8
    + (stats.speed || 0) * 5
    + (stats.recovery || 0) * 5
    + (stats.mp_recovery || 0) * 5;
}

function strongerEnemy(current, candidate) {
  if (!candidate) return current;
  if (!current) return candidate;
  return Number(candidate.power || 0) > Number(current.power || 0) ? candidate : current;
}

function formatToolBreakdown(toolCalls, limit = 5) {
  const detail = toolCalls?.by_name_detail && Object.keys(toolCalls.by_name_detail).length
    ? toolCalls.by_name_detail
    : null;
  const items = detail
    ? Object.entries(detail).map(([name, counts]) => ({ name, total: counts.total || 0, failed: counts.failed || 0 }))
    : Object.entries(toolCalls?.by_name || {}).map(([name, total]) => ({ name, total: Number(total) || 0, failed: 0 }));
  return items
    .sort((a, b) => b.total - a.total || a.name.localeCompare(b.name))
    .slice(0, limit)
    .map(item => `${toolLabel(item.name)} ${item.total}${item.failed ? `/失败${item.failed}` : ""}`)
    .join("，");
}

function computeReplayStats(replay) {
  if (!replay || !Array.isArray(replay.turns)) return null;
  let totalMs = 0, inputTokens = 0, outputTokens = 0, cacheRead = 0, cacheWrite = 0;
  let totalCalls = 0, successfulCalls = 0, failedCalls = 0;
  const callsByName = {};
  const callsByNameDetail = {};
  let battlesTotal = 0, battlesWon = 0, battlesLost = 0;
  let goldEarned = 0, expEarned = 0;
  let crafted = 0, upgrades = 0, allocated = 0, recruited = 0, dismissed = 0, equipped = 0, unequipped = 0;
  let modelSteps = 0, turnsCompleted = 0, turnsFailed = 0;
  let cumulativeGoldEarned = 0, cumulativeExpEarned = 0;
  const economyCurve = [];

  // Prefer pre-computed stats from replay.json
  const savedGA = replay.stats && replay.stats.game_actions;
  const savedEconomyCurve = Array.isArray(savedGA?.economy_curve) ? savedGA.economy_curve : null;
  let strongestDefeatedEnemy = savedGA?.strongest_defeated_enemy || null;
  if (savedGA) {
    goldEarned = savedGA.total_gold_earned || 0;
    expEarned = savedGA.total_experience_earned || 0;
  }

  for (const [turnIndex, turn] of replay.turns.entries()) {
    if (!turn || typeof turn !== "object") continue;
    let turnGoldEarned = 0, turnExpEarned = 0;
    // Turn status
    if (turn.status === "completed") turnsCompleted++;
    else if (turn.status === "failed") turnsFailed++;

    // Timing/usage from turn-level aggregate or step-level
    if (turn.timing_usage) {
      const tu = turn.timing_usage;
      if (typeof tu.duration_ms === "number") totalMs += tu.duration_ms;
      if (typeof tu.input_tokens === "number") inputTokens += tu.input_tokens;
      if (typeof tu.output_tokens === "number") outputTokens += tu.output_tokens;
    }
    const steps = Array.isArray(turn.steps) ? turn.steps : [];
    for (const step of steps) {
      if (!step || typeof step !== "object") continue;
      // Assistant steps: timing + usage + count model steps
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
      // Tool results: count calls + game actions
      if (step.type === "tool_result") {
        totalCalls++;
        const name = step.name || "";
        callsByName[name] = (callsByName[name] || 0) + 1;
        const detail = callsByNameDetail[name] || { total: 0, successful: 0, failed: 0 };
        detail.total++;
        callsByNameDetail[name] = detail;
        const content = typeof step.content === "string" ? step.content : "";
        const ok = toolStepSucceeded(step, content);
        if (ok) {
          successfulCalls++;
          detail.successful++;
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
              turnGoldEarned += structured.goldEarned;
              turnExpEarned += structured.expEarned;
              if (!savedGA) {
                goldEarned += structured.goldEarned;
                expEarned += structured.expEarned;
              }
            } else {
              const battleMatch = content.match(/(\d+)\s*场战斗[,，]\s*(\d+)\s*胜\s*(\d+)\s*负/);
              if (battleMatch) {
                const total = parseInt(battleMatch[1], 10);
                const won = parseInt(battleMatch[2], 10);
                const lost = parseInt(battleMatch[3], 10);
                battlesTotal += total;
                battlesWon += won;
                battlesLost += lost;
              }
              const rewards = textRewardStats(content);
              turnGoldEarned += rewards.goldEarned;
              turnExpEarned += rewards.expEarned;
              if (!savedGA) {
                goldEarned += rewards.goldEarned;
                expEarned += rewards.expEarned;
              }
            }
            strongestDefeatedEnemy = strongerEnemy(
              strongestDefeatedEnemy,
              strongestDefeatedEnemyFromStep(step, turn.observation_before, turn.turn ?? turnIndex + 1),
            );
          }
        } else {
          failedCalls++;
          detail.failed++;
        }
      }
    }
    if (turn.status === "completed") {
      cumulativeGoldEarned += turnGoldEarned;
      cumulativeExpEarned += turnExpEarned;
      economyCurve.push({
        turn: turn.turn ?? turnIndex + 1,
        gold_earned: turnGoldEarned,
        experience_earned: turnExpEarned,
        cumulative_gold_earned: cumulativeGoldEarned,
        cumulative_experience_earned: cumulativeExpEarned,
      });
    }
  }

  const tokenUsage = { input_tokens: inputTokens, output_tokens: outputTokens };
  if (cacheRead > 0) tokenUsage.cache_read_input_tokens = cacheRead;
  if (cacheWrite > 0) tokenUsage.cache_creation_input_tokens = cacheWrite;

  return {
    timing: { total_duration_ms: Math.round(totalMs), total_duration_seconds: Math.round(totalMs) / 1000 },
    tool_calls: { total: totalCalls, successful: successfulCalls, failed: failedCalls, by_name: callsByName, by_name_detail: callsByNameDetail },
    token_usage: tokenUsage,
    game_actions: {
      battles_total: battlesTotal, battles_won: battlesWon, battles_lost: battlesLost,
      total_gold_earned: goldEarned, total_experience_earned: expEarned,
      total_equipment_crafted: crafted, total_upgrades_purchased: upgrades,
      total_recruits: recruited, total_dismissals: dismissed,
      total_experience_allocated: allocated, total_equips: equipped, total_unequips: unequipped,
      economy_curve: savedEconomyCurve || economyCurve,
      strongest_defeated_enemy: strongestDefeatedEnemy,
    },
    model_interaction: { total_model_steps: modelSteps, total_turns_completed: turnsCompleted, total_turns_failed: turnsFailed },
  };
}

function renderReplayStatsBadges(stats) {
  const badges = [];
  const timing = stats.timing;
  if (timing && timing.total_duration_ms > 0) {
    badges.push(`⏱ 耗时 ${formatDurationMs(timing.total_duration_ms)}`);
  }
  const tokens = stats.token_usage;
  if (tokens) {
    const tp = [];
    if (tokens.input_tokens) tp.push(`in ${tokens.input_tokens.toLocaleString()}`);
    if (tokens.output_tokens) tp.push(`out ${tokens.output_tokens.toLocaleString()}`);
    if (tp.length) badges.push(`🔤 tokens ${tp.join(" · ")}`);
    if (tokens.cache_read_input_tokens) badges.push(`📦 缓存命中 ${tokens.cache_read_input_tokens.toLocaleString()}`);
  }
  const tc = stats.tool_calls;
  if (tc && tc.total > 0) {
    const breakdown = formatToolBreakdown(tc);
    badges.push(`🔧 工具调用 ${tc.total} (✓ ${tc.successful} · ✗ ${tc.failed})${breakdown ? `：${breakdown}` : ""}`);
  }
  const ga = stats.game_actions;
  if (ga) {
    if (ga.battles_total > 0) badges.push(`⚔ 战斗 ${ga.battles_won}/${ga.battles_total} 胜`);
    if (ga.total_gold_earned > 0) badges.push(`💰 金币收入 ${ga.total_gold_earned.toLocaleString()}`);
    if (ga.total_experience_earned > 0) badges.push(`⭐ 经验收入 ${ga.total_experience_earned.toLocaleString()}`);
    if (ga.total_equipment_crafted > 0) badges.push(`⚒ 合成 ${ga.total_equipment_crafted} 件`);
    if (ga.total_upgrades_purchased > 0) badges.push(`📈 升级 ${ga.total_upgrades_purchased} 个`);
    if (ga.total_recruits > 0) badges.push(`👥 招募 ${ga.total_recruits} 人`);
    if (ga.strongest_defeated_enemy?.name) {
      badges.push(`🏆 最强击败 ${ga.strongest_defeated_enemy.name} 强度 ${ga.strongest_defeated_enemy.power ?? "—"}`);
    }
  }
  return badges.map((b) => `<span>${escapeHtml(b)}</span>`).join("");
}

function readOptionalValue(id) {
  const value = $(id)?.value?.trim();
  return value || undefined;
}

function readNumberValue(id, fallback) {
  const value = Number.parseInt($(id)?.value || "", 10);
  return Number.isFinite(value) ? value : fallback;
}

function readOptionalInteger(id) {
  const raw = $(id)?.value;
  if (!raw) return undefined;
  const value = Number.parseInt(raw, 10);
  return Number.isFinite(value) ? value : undefined;
}

function readOptionalNumber(id) {
  const raw = $(id)?.value;
  if (!raw) return undefined;
  const value = Number.parseFloat(raw);
  return Number.isFinite(value) ? value : undefined;
}

function shortHash(value) {
  return typeof value === "string" && value.length > 12 ? value.slice(0, 12) : value || "n/a";
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

/* ========== Global Exports ========== */
window.allocateExperience = allocateExperience;
window.craft = craft;
window.purchaseUpgrade = purchaseUpgrade;
window.recruit = recruit;
window.equip = equip;
window.equipFromPopup = equipFromPopup;
window.unequip = unequip;
window.selectHunt = selectHunt;
window.endTurn = endTurn;
window.openEquipPopup = openEquipPopup;
window.closeCombatModal = closeCombatModal;
