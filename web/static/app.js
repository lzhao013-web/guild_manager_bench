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
    toolTraceSeq: 0,
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
      ${metric("材料", materialsText(obs.materials), "mat")}
      ${metric("状态", obs.finished ? "已结束" : "进行中", obs.finished ? "status finished" : "status")}
      <button class="combat-trigger" ${obs.finished || state.watchOnly ? "disabled" : ""} onclick="openCombatModal()">交战${countBadge}</button>
    </div>
  `;
}

function metric(label, value, type) {
  return `
    <div class="metric-card metric-${type}">
      <span class="metric-label">${label}</span>
      <span class="metric-value">${escapeHtml(String(value))}</span>
    </div>
  `;
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
  $("adventurers").innerHTML = list(obs.adventurers.map((adventurer, index) => {
    const isDead = adventurer.resources.current_hp <= 0;
    const isOpen = state.openDetails.has(adventurer.adventurer_id) || (state.openDetails.size === 0 && index === 0);
    return `
      <div class="adv-card ${isDead ? "adv-dead" : ""}">
        <div class="adv-header">
          <div style="display:flex;align-items:center;gap:8px;">
            <strong class="adv-name">${escapeHtml(adventurer.name)}</strong>
            <span class="badge">Lv.${adventurer.level}</span>
            ${isDead ? '<span class="badge badge-danger">阵亡</span>' : ""}
          </div>
          <div class="inline compact">
            <span class="small muted">${levelText(adventurer)}</span>
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
            ${statGrid(adventurer.base_stats, adventurer.effective_stats)}
            ${skillList(adventurer.skills)}
            ${levelSkillUnlocksBlock(adventurer)}
            <div class="slot-grid">
              ${adventurer.equipment_slots.map((slot) => equipmentSlotCell(adventurer, slot)).join("")}
            </div>
            ${experienceBlock(obs, adventurer)}
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
  $("recruitment").innerHTML = list(candidates.map((candidate) => `
    <div class="row recruit-row">
      <div class="row-title">
        <strong>${escapeHtml(candidate.name)}</strong>
        <span class="${candidate.can_recruit ? "ok" : "danger"} small">
          ${candidate.can_recruit ? "可招募" : "暂不可招募"}
        </span>
      </div>
      <div class="small muted">费用 ${candidate.recruit_gold} 金币 · ${escapeHtml(candidate.template_id)}</div>
      ${candidateStats(candidate.base_stats)}
      <div class="small">每级属性成长：${statModifierText(candidate.stat_growth_per_level)}</div>
      ${skillList(candidate.skills)}
      ${candidateLevelUnlocks(candidate)}
      ${candidate.can_recruit ? "" : `<div class="small danger">缺少：${missingText(candidate.missing)}</div>`}
      <button type="button" ${disabled(!candidate.can_recruit)} onclick="recruit('${candidate.candidate_id}')">招募</button>
    </div>
  `));
}

function candidateStats(stats) {
  return `
    <div class="stat-inline">
      HP ${stats.hp} · MP ${stats.mp} · 攻击 ${stats.attack} · 防御 ${stats.defense} · 速度 ${stats.speed} · 回血 ${stats.recovery} · 回魔 ${stats.mp_recovery ?? 0}
    </div>
  `;
}

function candidateLevelUnlocks(candidate) {
  const unlocks = candidate.level_skill_unlocks || [];
  if (!unlocks.length) {
    return "";
  }
  return `<div class="small muted">升级可学会技能：${unlocks.map((unlock) => levelPreviewUnlockText(unlock)).join(" · ")}</div>`;
}

/* ========== Crafting ========== */

function renderCrafting(obs) {
  $("crafting").innerHTML = list(obs.crafting_recipes.map((recipe) => `
    <div class="row">
      <div class="row-title">
        <strong>${escapeHtml(recipe.name)}</strong>
        <span class="${recipe.can_craft ? "ok" : "danger"} small">${recipe.can_craft ? "可合成" : "资源不足"}</span>
      </div>
      <div class="small muted">${escapeHtml(recipe.output_name)} · ${slotName(recipe.output_slot)}${recipe.output_allowed_class_names?.length ? ` · 限制: ${recipe.output_allowed_class_names.join("、")}` : ""}</div>
      <div class="small">产物：${statModifierText(recipe.output_stats)}</div>
      ${skillList(recipe.output_skills)}
      <div class="small">消耗：金币 ${recipe.gold_cost} · ${materialsText(recipe.material_costs)}</div>
      ${recipe.can_craft ? "" : `<div class="small danger">缺少：${missingText(recipe.missing)}</div>`}
      <button type="button" ${disabled(!recipe.can_craft)} onclick="craft('${recipe.recipe_id}')">合成</button>
    </div>
  `));
}

/* ========== Equipment (read-only in workshop) ========== */

function renderEquipment(obs) {
  $("equipment").innerHTML = list(obs.equipment_inventory.map((item) => {
    const equippedInfo = item.equipped_by
      ? resolveName(item.equipped_by)
      : "未装备";
    const classInfo = item.allowed_class_names?.length
      ? ` · 限制: ${item.allowed_class_names.join("、")}`
      : "";
    return `
      <div class="row">
        <div class="row-title">
          <strong>${escapeHtml(item.name)}</strong>
          <span class="small ${item.equipped_by ? "ok" : "muted"}">${equippedInfo}</span>
        </div>
        <div class="small">${slotName(item.slot)} · ${statModifierText(item.stats)}${classInfo}</div>
      </div>
    `;
  }));
}

/* ========== Upgrades ========== */

function renderUpgrades(obs) {
  $("upgrades").innerHTML = list(obs.global_upgrades.map((upgrade) => `
    <div class="row">
      <div class="row-title">
        <strong>${escapeHtml(upgrade.name)}</strong>
        <span class="${upgrade.unlocked ? "ok" : upgrade.can_purchase ? "ok" : "danger"} small">
          ${upgrade.unlocked ? "已解锁" : upgrade.can_purchase ? "可购买" : "不可购买"}
        </span>
      </div>
      <div class="small">金币 ${upgrade.gold_cost} · ${statModifierText(upgrade.stats)}${upgrade.party_size_bonus ? ` · 队伍上限 +${upgrade.party_size_bonus}` : ""}</div>
      <div class="small muted">前置：${upgradePrereqText(obs, upgrade.required_upgrade_ids)}</div>
      ${!upgrade.unlocked && !upgrade.can_purchase ? `<div class="small danger">缺少：${missingText(upgrade.missing)}</div>` : ""}
      ${upgrade.unlocked ? "" : `<button type="button" ${disabled(!upgrade.can_purchase)} onclick="purchaseUpgrade('${upgrade.upgrade_id}')">购买</button>`}
    </div>
  `));
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
    return `
      <div class="hunt-entry">
        <div class="hunt-info">
          <strong>${escapeHtml(monster.name)}</strong>
          <div class="stat-inline">HP ${monster.stats.hp} · MP ${monster.stats.mp} · 攻 ${monster.stats.attack} · 防 ${monster.stats.defense} · 速 ${monster.stats.speed}</div>
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
  const action = event.action_type === "skill"
    ? `使用 ${event.skill_name || event.skill_id}`
    : "普通攻击";
  const healing = event.healing > 0
    ? `，治疗 ${event.healing}，目标 HP ${event.healing_target_hp}`
    : "";
  const actor = resolveName(event.actor_id);
  const target = resolveName(event.target_id);
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

function list(items) {
  return items.length ? `<div class="list">${items.join("")}</div>` : `<div class="muted">无</div>`;
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
    <div class="small muted">升级可学会技能：
      ${unlocks.map((unlock) => `<span class="skill-unlock ${unlock.unlocked ? "ok" : "muted"}">${levelUnlockText(unlock)}</span>`).join(" ")}
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
        <div class="inline">
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
        <strong>${slotName(slot.slot)}</strong>
        <div class="small muted">被${slotName(slot.blocked_by)}占用</div>
      </div>
    `;
  }
  return `
    <div class="slot-cell slot-empty" onclick="openEquipPopup('${adventurer.adventurer_id}', '${slot.slot}', this)">
      <strong>${slotName(slot.slot)}</strong>
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
  const toast = $("toast");
  toast.textContent = message;
  toast.hidden = false;
  window.setTimeout(() => {
    toast.hidden = true;
  }, 3600);
}

function showToast(message) {
  showError(message);
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
  state.llm.toolTraceSeq = 0;
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
  } else if (event.type === "model_request") {
    const entry = createModelEntry(event.turn, event.step);
    entry.request = event.request || null;
    state.llm.currentModelEntry = entry;
    state.llm.transcript.push(entry);
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
    } else {
      item = createToolTraceItem(event);
      item.ok = event.result?.ok ?? null;
      item.error = event.result?.error || "";
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
  } else if (event.type === "turn_failed") {
    state.llm.running = false;
    state.llm.status = `回合失败：${event.trace?.failure_reason || "unknown"}`;
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
  if (streamOnly && state.llm.currentModelEntry) {
    streamUpdateModelEntry(state.llm.currentModelEntry);
  } else {
    $("llmTranscript").innerHTML = renderLlmTranscript();
  }
  if (streamOnly) {
    return;
  }
  $("llmToolTrace").innerHTML = renderLlmToolTrace();
  $("llmEventLog").innerHTML = renderLlmEventLog();
  if (includeReplay) {
    renderLlmReplayControls();
    $("llmReplayStatus").textContent = state.llm.replay.status;
    $("llmReplayView").innerHTML = renderLlmReplay();
  }
}

function renderLlmTranscript() {
  if (!state.llm.transcript.length) {
    return '<div class="muted">尚无模型行为</div>';
  }
  return state.llm.transcript.slice(-80).map((entry) => {
    if (entry.kind === "turn") {
      const meta = renderTurnTimingUsage(entry.timingUsage);
      return `<div class="llm-turn-marker">${escapeHtml(entry.title)}${meta}</div>`;
    }
    if (entry.kind === "summary") {
      return renderRunSummary(entry);
    }
    if (entry.kind === "retry") {
      return `<div class="llm-retry">重试提示：${escapeHtml(entry.text)}</div>`;
    }
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
  }).join("");
}

function renderModelBodyContent(entry, entryId) {
  return [
    renderModelThinking(entry, entryId),
    renderModelReply(entry.text),
    renderModelToolCalls(entry.toolCalls),
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
    return '<div class="muted">尚无工具调用</div>';
  }
  return state.llm.toolTrace.slice(-120).reverse().map((item) => {
    const ok = item.ok;
    const contentOk = typeof item.content === "string" && item.content.startsWith("OK");
    const isSuccess = ok === true || (ok === null && contentOk);
    const cls = ok === false ? "danger" : isSuccess ? "ok" : "muted";
    const label = ok === false ? "失败" : isSuccess ? "成功" : "等待结果";
    const toolId = toolTraceId(item);
    const open = state.llm.openToolTrace.has(toolId) ? "open" : "";
    const received = item.content || "";
    return `
      <details class="llm-tool-item" data-tool-id="${escapeHtml(toolId)}" ${open}>
        <summary>
          <span>T${escapeHtml(item.turn)} · ${escapeHtml(item.name)}</span>
          <strong class="${cls}">${label}</strong>
        </summary>
        <div class="small muted">call_id：${escapeHtml(item.callId || "n/a")}</div>
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
    return '<div class="muted">尚无调试事件</div>';
  }
  return state.llm.events.slice(-120).reverse().map((event) => `
    <details class="llm-event">
      <summary>${escapeHtml(event.type)}</summary>
      <pre>${escapeHtml(JSON.stringify(event, null, 2))}</pre>
    </details>
  `).join("");
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
    return '<div class="muted">选择归档或打开 replay.json 后开始回放</div>';
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
  if (prompt !== undefined) parts.push(`in ${prompt}`);
  if (completion !== undefined) parts.push(`out ${completion}`);
  if (total !== undefined) parts.push(`total ${total}`);
  return parts.length ? `tokens ${parts.join(" · ")}` : "";
}

function renderTurnTimingUsage(tu) {
  if (!tu) return "";
  const parts = [];
  const ms = Number(tu.duration_ms);
  if (Number.isFinite(ms) && ms > 0) parts.push(formatDurationMs(ms));
  if (tu.input_tokens) parts.push(`in ${tu.input_tokens}`);
  if (tu.output_tokens) parts.push(`out ${tu.output_tokens}`);
  return parts.length ? `<span class="llm-turn-meta">${escapeHtml(parts.join(" · "))}</span>` : "";
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
    craft_equipment: "制作装备",
    purchase_upgrade: "购买升级",
    allocate_experience: "分配经验",
    recruit_adventurer: "招募冒险者",
    dismiss_adventurer: "遣散冒险者",
    equip_item: "装备物品",
    unequip_item: "卸下装备",
    end_turn: "结束回合",
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
