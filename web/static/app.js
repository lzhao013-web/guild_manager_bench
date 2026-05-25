const state = {
  sessionId: null,
  observation: null,
  events: [],
  selectedHunts: new Map(),
  openDetails: new Set(),
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
  },
};

const $ = (id) => document.getElementById(id);

window.addEventListener("load", () => {
  $("newSessionButton").addEventListener("click", () => createSession());
  $("llmStartButton").addEventListener("click", () => startLlmDebug());
  $("llmStopButton").addEventListener("click", () => stopLlmDebug());
  $("combatModal").addEventListener("click", (e) => {
    if (e.target === e.currentTarget) closeCombatModal();
  });
  document.addEventListener("click", onDocumentClick);
  initTabs();
  bootstrap();
});

/* ========== Tab Switching ========== */

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
  const data = await response.json();
  if (!response.ok) {
    showError(data.detail || "创建会话失败");
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
  const data = await response.json();
  if (!response.ok) {
    showError(data.detail || "读取会话失败");
    return;
  }
  setSession(data);
}

function setSession(data) {
  state.sessionId = data.session_id;
  state.observation = data.observation;
  state.events = data.events || [];
  state.selectedHunts.clear();
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
  if (state.watchOnly) {
    return;
  }
  const response = await fetch(`/api/sessions/${state.sessionId}/actions`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const data = await response.json();
  if (!response.ok) {
    if (data.event) {
      mergeEvent(data.event);
    }
    if (data.observation) {
      state.observation = data.observation;
      render();
    }
    showError(data.detail || "动作失败");
    return;
  }
  if (data.event) {
    mergeEvent(data.event);
  }
  if (data.observation) {
    state.observation = data.observation;
  }
  render();
}

/* ========== Render Dispatch ========== */

function render() {
  const obs = state.observation;
  if (!obs) {
    return;
  }
  $("sessionMeta").textContent = `会话 ${obs.session_id} · 回合 ${obs.turn}/${obs.max_turns}`;
  $("newSessionButton").disabled = state.watchOnly;
  renderOverview(obs);
  renderAdventurers(obs);
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
      ${metric("回合", `${obs.turn}/${obs.max_turns}`, "turn")}
      ${metric("金币", obs.gold, "gold")}
      ${metric("经验池", obs.experience_pool, "exp")}
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
          <span class="small muted">${levelText(adventurer)}</span>
        </div>
        <div class="bar-row">
          ${hpBar(adventurer.resources.current_hp, adventurer.effective_stats.hp)}
          ${mpBar(adventurer.resources.current_mp, adventurer.effective_stats.mp)}
        </div>
        <details ${isOpen ? "open" : ""} data-adv="${adventurer.adventurer_id}">
          <summary class="adv-summary">属性 · 技能 · 装备 · 升级</summary>
          <div class="adv-body">
            ${statGrid(adventurer.base_stats, adventurer.effective_stats)}
            <div class="small muted">技能：${adventurer.skills.map((s) => skillTag(s)).join("")}</div>
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

/* ========== Crafting ========== */

function renderCrafting(obs) {
  $("crafting").innerHTML = list(obs.crafting_recipes.map((recipe) => `
    <div class="row">
      <div class="row-title">
        <strong>${escapeHtml(recipe.name)}</strong>
        <span class="${recipe.can_craft ? "ok" : "danger"} small">${recipe.can_craft ? "可合成" : "资源不足"}</span>
      </div>
      <div class="small muted">${escapeHtml(recipe.output_name)} · ${slotName(recipe.output_slot)}</div>
      <div class="small">产物：${statModifierText(recipe.output_stats)} · 技能：${names(recipe.output_skills)}</div>
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
    return `
      <div class="row">
        <div class="row-title">
          <strong>${escapeHtml(item.name)}</strong>
          <span class="small ${item.equipped_by ? "ok" : "muted"}">${equippedInfo}</span>
        </div>
        <div class="small">${slotName(item.slot)} · ${statModifierText(item.stats)}</div>
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
      <div class="small">金币 ${upgrade.gold_cost} · ${statModifierText(upgrade.stats)}</div>
      <div class="small muted">前置：${upgradePrereqText(obs, upgrade.required_upgrade_ids)}</div>
      <button type="button" ${disabled(!upgrade.can_purchase)} onclick="purchaseUpgrade('${upgrade.upgrade_id}')">购买</button>
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

function renderModalHunts() {
  const obs = state.observation;
  if (!obs) return;

  const aliveAdventurers = obs.adventurers.filter((a) => a.resources.current_hp > 0);
  const validMonsterIds = new Set(obs.monsters.map((m) => m.monster_id));
  for (const monsterId of Array.from(state.selectedHunts.keys())) {
    if (!validMonsterIds.has(monsterId)) {
      state.selectedHunts.delete(monsterId);
    }
  }

  const body = $("modalHunts");
  if (!body) return;

  body.innerHTML = obs.monsters.map((monster) => {
    const selectedAdv = state.selectedHunts.get(monster.monster_id);
    const adventurer = selectedAdv ? obs.adventurers.find((a) => a.adventurer_id === selectedAdv) : null;
    let preview = "";
    if (adventurer) {
      const sim = simulateCombat(adventurer, monster);
      const hpPct = Math.round((sim.hpLeft / sim.hpMax) * 100);
      const cls = sim.won ? "ok" : sim.draw ? "warning" : "danger";
      const label = sim.won ? "胜利" : sim.draw ? "平局" : "失败";
      const hpColor = hpPct > 60 ? "" : hpPct > 30 ? "hp-warn-text" : "danger";
      preview = `
        <div class="hunt-preview">
          <span class="preview-result ${cls}">${label}</span>
          <span class="preview-hp ${hpColor}">${escapeHtml(adventurer.name)} 剩余 ${sim.hpLeft}/${sim.hpMax} HP</span>
          <span class="preview-detail">${sim.actions} 次行动</span>
        </div>
      `;
    }
    return `
      <div class="hunt-entry">
        <div class="hunt-info">
          <strong>${escapeHtml(monster.name)}</strong>
          <div class="stat-inline">攻 ${monster.stats.attack} · 防 ${monster.stats.defense} · 速 ${monster.stats.speed}</div>
          <div class="small muted">奖励：${rewardText(monster.reward)}</div>
          ${monster.skills.length ? `<div class="small muted">技能：${monster.skills.map((s) => skillTag(s)).join("")}</div>` : ""}
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
      ${preview}
    `;
  }).join("");

  const endBtn = $("modalEndTurn");
  if (endBtn) {
    endBtn.disabled = state.watchOnly || obs.finished;
  }
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
  const popup = $("equipPopup");
  if (!popup.hidden && !popup.contains(e.target) && !e.target.closest(".slot-empty")) {
    closeEquipPopup();
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
  submitAction({ type: "allocate_experience", adventurer_id: adventurerId, amount });
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
      ${statRow("回复", baseStats.recovery, effectiveStats.recovery, "rec")}
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
  return `
    <div class="xp-block">
      <div class="inline small">
        <span>${next.max_level ? "已达到最高等级" : `距离下一级还差 ${next.remaining} 经验`}</span>
        <span class="muted">经验池 ${obs.experience_pool}</span>
      </div>
      <div class="progress"><span style="width: ${percent}%"></span></div>
      <div class="small muted">下级成长：${statModifierText(obs.experience_rules.stat_growth_per_level)}</div>
      ${next.preview_level !== adventurer.level ? `<div class="small ok">投入全部经验池可到 Lv.${next.preview_level}</div>` : ""}
    </div>
    <div class="xp-form">
      <input id="xp-${adventurer.adventurer_id}" type="number" min="0" max="${obs.experience_pool}" value="0" ${disabled()} oninput="updateExperiencePreview('${adventurer.adventurer_id}')" />
      <button type="button" ${disabled()} onclick="allocateExperience('${adventurer.adventurer_id}')">分配经验</button>
    </div>
    <div id="xp-preview-${adventurer.adventurer_id}" class="small muted">${experiencePreviewText(adventurer, 0, obs.experience_rules)}</div>
  `;
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
  return Object.entries(missing).map(([key, value]) => `${materialName(key)}:${value}`).join(" · ");
}

function materialName(key) {
  return { iron_ore: "铁矿", wood: "木材", leather: "皮革", herb: "草药" }[key] || key;
}

function names(items) {
  return items.length ? items.map((item) => item.name).join(" · ") : "无";
}

function skillTag(skill) {
  const desc = skillDescText(skill);
  return ` <span class="skill-tag" data-tip="${escapeHtml(desc)}">${escapeHtml(skill.name)}</span>`;
}

function skillDescText(skill) {
  const parts = [];
  parts.push(skill.kind === "active" ? "主动" : "被动");
  if (skill.mp_cost > 0) parts.push(`消耗 ${skill.mp_cost} MP`);
  if (skill.once_per_battle) parts.push("每场限一次");

  const condText = skillConditionText(skill.condition);
  if (condText) parts.push(`条件：${condText}`);

  for (const eff of skill.effects) {
    if (eff.type === "damage_multiplier") parts.push(`伤害 ×${eff.value}`);
    if (eff.type === "heal") parts.push(`治疗 ${eff.value} HP`);
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
  if (cond.type === "all") return (cond.conditions || []).map(skillConditionText).filter(Boolean).join(" 且 ");
  if (cond.type === "any") return (cond.conditions || []).map(skillConditionText).filter(Boolean).join(" 或 ");
  return "";
}

function statModifierText(stats) {
  const entries = Object.entries(stats || {}).filter(([, value]) => value !== 0);
  return entries.length ? entries.map(([key, value]) => `${statName(key)} +${value}`).join(" · ") : "无属性";
}

function upgradePrereqText(obs, ids) {
  if (!ids || !ids.length) return "无";
  return ids.map((id) => {
    const u = obs.global_upgrades.find((g) => g.upgrade_id === id);
    return u ? u.name : id;
  }).join("、");
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
  return Number.isFinite(amount) ? Math.max(0, amount) : 0;
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
  return state.watchOnly || state.observation?.finished || extra ? "disabled" : "";
}

function showError(message) {
  const toast = $("toast");
  toast.textContent = message;
  toast.hidden = false;
  window.setTimeout(() => {
    toast.hidden = true;
  }, 3600);
}

/* ========== LLM Debug ========== */

function startLlmDebug() {
  stopLlmDebug(false);
  state.llm.running = true;
  state.llm.status = "连接中";
  state.llm.prompt = "";
  state.llm.transcript = [];
  state.llm.toolTrace = [];
  state.llm.events = [];
  state.llm.currentModelEntry = null;
  renderLlmDebug();

  const protocol = window.location.protocol === "https:" ? "wss" : "ws";
  const socket = new WebSocket(`${protocol}://${window.location.host}/ws/llm/debug`);
  state.llm.socket = socket;

  socket.addEventListener("open", () => {
    const payload = llmPayload();
    socket.send(JSON.stringify({ type: "start", payload }));
    state.llm.status = "运行中";
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

function llmPayload() {
  return {
    model: readOptionalValue("llmModel"),
    base_url: readOptionalValue("llmBaseUrl"),
    api_key: readOptionalValue("llmApiKey"),
    max_tool_calls_per_turn: readNumberValue("llmMaxToolCalls", 20),
    temperature: readOptionalNumber("llmTemperature"),
    objective: readOptionalValue("llmObjective"),
  };
}

function handleLlmEvent(event) {
  state.llm.events.push(compactLlmEvent(event));
  if (state.llm.events.length > 300) {
    state.llm.events.shift();
  }

  if (event.type === "run_started") {
    state.llm.status = `运行中 · 会话 ${event.session_id}`;
  } else if (event.type === "turn_started") {
    state.llm.prompt = event.prompt || "";
    state.llm.status = `第 ${event.turn} 回合`;
    state.llm.currentModelEntry = null;
    state.llm.transcript.push({ kind: "turn", title: `第 ${event.turn} 回合开始` });
  } else if (event.type === "model_request") {
    const entry = { kind: "model", turn: event.turn, step: event.step, text: "" };
    state.llm.currentModelEntry = entry;
    state.llm.transcript.push(entry);
  } else if (event.type === "model_delta") {
    ensureModelEntry().text += event.text || "";
  } else if (event.type === "model_response") {
    const entry = ensureModelEntry();
    if (!entry.text && event.text) {
      entry.text = event.text;
    }
    entry.toolCalls = event.tool_calls || [];
  } else if (event.type === "tool_call") {
    state.llm.toolTrace.push({
      callId: event.call_id,
      turn: event.turn,
      name: event.name,
      arguments: event.arguments || {},
      result: null,
    });
  } else if (event.type === "tool_result") {
    const item = findToolTrace(event.call_id, event.name);
    if (item) {
      item.result = event.result;
    } else {
      state.llm.toolTrace.push({
        callId: event.call_id,
        turn: event.turn,
        name: event.name,
        arguments: event.arguments || {},
        result: event.result,
      });
    }
  } else if (event.type === "retry") {
    state.llm.transcript.push({ kind: "retry", reason: event.reason, text: event.message });
  } else if (event.type === "turn_completed") {
    state.llm.status = `第 ${event.trace?.turn || ""} 回合完成`;
    state.llm.transcript.push({ kind: "turn", title: `第 ${event.trace?.turn || ""} 回合完成` });
  } else if (event.type === "turn_failed") {
    state.llm.running = false;
    state.llm.status = `回合失败：${event.trace?.failure_reason || "unknown"}`;
  } else if (event.type === "run_completed") {
    state.llm.running = false;
    state.llm.status = `完成 · ${event.run?.turns || 0} 回合`;
  } else if (event.type === "run_failed") {
    state.llm.running = false;
    state.llm.status = `失败：${event.run?.failure_reason || "unknown"}`;
  } else if (event.type === "debug_error") {
    state.llm.running = false;
    state.llm.status = `错误：${event.error}`;
  }

  renderLlmDebug();
}

function ensureModelEntry() {
  if (!state.llm.currentModelEntry) {
    state.llm.currentModelEntry = { kind: "model", turn: "", step: "", text: "" };
    state.llm.transcript.push(state.llm.currentModelEntry);
  }
  return state.llm.currentModelEntry;
}

function findToolTrace(callId, name) {
  for (let i = state.llm.toolTrace.length - 1; i >= 0; i--) {
    const item = state.llm.toolTrace[i];
    if (callId && item.callId === callId) return item;
    if (!callId && item.name === name && !item.result) return item;
  }
  return null;
}

function renderLlmDebug() {
  if (!$("llmStatus")) return;
  $("llmStatus").textContent = state.llm.status;
  $("llmStartButton").disabled = state.llm.running;
  $("llmStopButton").disabled = !state.llm.running;
  $("llmPrompt").textContent = state.llm.prompt || "尚未开始";
  $("llmTranscript").innerHTML = renderLlmTranscript();
  $("llmToolTrace").innerHTML = renderLlmToolTrace();
  $("llmEventLog").innerHTML = renderLlmEventLog();
}

function renderLlmTranscript() {
  if (!state.llm.transcript.length) {
    return '<div class="muted">尚无模型输出</div>';
  }
  return state.llm.transcript.slice(-80).map((entry) => {
    if (entry.kind === "turn") {
      return `<div class="llm-turn-marker">${escapeHtml(entry.title)}</div>`;
    }
    if (entry.kind === "retry") {
      return `<div class="llm-retry">重试提示：${escapeHtml(entry.text)}</div>`;
    }
    const tools = entry.toolCalls?.length
      ? `<div class="small muted">请求工具：${entry.toolCalls.map((call) => escapeHtml(call.name)).join(" · ")}</div>`
      : "";
    return `
      <div class="llm-model-message">
        <div class="row-title">
          <strong>模型响应</strong>
          <span class="small muted">T${escapeHtml(entry.turn)} · step ${escapeHtml(entry.step)}</span>
        </div>
        <pre>${escapeHtml(entry.text || "（无文本，可能只请求工具）")}</pre>
        ${tools}
      </div>
    `;
  }).join("");
}

function renderLlmToolTrace() {
  if (!state.llm.toolTrace.length) {
    return '<div class="muted">尚无工具调用</div>';
  }
  return state.llm.toolTrace.slice(-120).reverse().map((item) => {
    const ok = item.result?.ok;
    const cls = ok === false ? "danger" : ok === true ? "ok" : "muted";
    const label = ok === false ? "失败" : ok === true ? "成功" : "等待结果";
    return `
      <details class="llm-tool-item" ${ok === false ? "open" : ""}>
        <summary>
          <span>T${escapeHtml(item.turn)} · ${escapeHtml(item.name)}</span>
          <strong class="${cls}">${label}</strong>
        </summary>
        <div class="small muted">call_id：${escapeHtml(item.callId || "n/a")}</div>
        <div class="llm-json-label">参数</div>
        <pre>${escapeHtml(JSON.stringify(item.arguments, null, 2))}</pre>
        ${item.result ? `
          <div class="llm-json-label">结果</div>
          <pre>${escapeHtml(JSON.stringify(summarizeToolResult(item.result), null, 2))}</pre>
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
  return compact;
}

function readOptionalValue(id) {
  const value = $(id)?.value?.trim();
  return value || undefined;
}

function readNumberValue(id, fallback) {
  const value = Number.parseInt($(id)?.value || "", 10);
  return Number.isFinite(value) ? value : fallback;
}

function readOptionalNumber(id) {
  const raw = $(id)?.value;
  if (!raw) return undefined;
  const value = Number.parseFloat(raw);
  return Number.isFinite(value) ? value : undefined;
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

/* ========== Combat Simulator ========== */

function simulateCombat(adventurer, monster) {
  const left = buildRuntime("left", adventurer.adventurer_id, adventurer.effective_stats, {
    current_hp: adventurer.resources.current_hp,
    current_mp: adventurer.resources.current_mp,
  }, adventurer.skills);

  const right = buildRuntime("right", monster.monster_id, monster.stats, {
    current_hp: monster.stats.hp,
    current_mp: monster.stats.mp,
  }, monster.skills);

  const immediate = resolveImmediate(left, right);
  if (immediate) {
    return finishSim(left, right, immediate.winner, immediate.reason, 0, 0);
  }

  let actions = 0;
  let time = 0;
  for (let i = 0; i < 1000; i++) {
    const ready = advanceUntilReady(left, right);
    if (!ready) return finishSim(left, right, null, "no_combatant_can_act", actions, time);

    const [actor, target, elapsed] = ready;
    time += elapsed;
    actor.gauge -= 100;
    actions++;

    performAction(actor, target, left, right);

    if (target.hp <= 0) {
      return finishSim(left, right, actor.side, "target_defeated", actions, time);
    }
  }

  return finishSim(left, right, null, "max_actions_reached", actions, time);
}

function buildRuntime(side, id, stats, resources, skills) {
  const active = skills
    .filter((s) => s.kind === "active")
    .sort((a, b) => b.priority - a.priority || a.skill_id.localeCompare(b.skill_id));
  const passive = skills.filter((s) => s.kind === "passive");
  return {
    side, id, stats,
    hp: resources.current_hp,
    mp: resources.current_mp,
    maxHp: stats.hp,
    maxMp: stats.mp,
    active, passive,
    usedOnce: new Set(),
    gauge: 0,
  };
}

function resolveImmediate(left, right) {
  const leftAlive = left.hp > 0;
  const rightAlive = right.hp > 0;
  if (leftAlive && rightAlive) return null;
  if (leftAlive) return { winner: "left", reason: "right_already_defeated" };
  if (rightAlive) return { winner: "right", reason: "left_already_defeated" };
  return { winner: null, reason: "both_already_defeated" };
}

function advanceUntilReady(left, right) {
  const leftSpd = effectiveSpeed(left, right);
  const rightSpd = effectiveSpeed(right, left);

  const candidates = [];
  if (leftSpd > 0) candidates.push({ c: left, spd: leftSpd });
  if (rightSpd > 0) candidates.push({ c: right, spd: rightSpd });
  if (!candidates.length) return null;

  const ticks = candidates.map(({ c, spd }) => c.gauge >= 100 ? 0 : Math.ceil((100 - c.gauge) / spd));
  const elapsed = Math.min(...ticks);

  for (const { c, spd } of candidates) {
    c.gauge += spd * elapsed;
  }

  const ready = [left, right].filter((c) => c.gauge >= 100);
  if (!ready.length) return null;

  const actor = ready.sort((a, b) => {
    const pA = [a.gauge, effectiveSpeed(a, a.side === "left" ? right : left), a.side === "left" ? 1 : 0];
    const pB = [b.gauge, effectiveSpeed(b, b.side === "left" ? right : left), b.side === "left" ? 1 : 0];
    for (let i = 0; i < 3; i++) {
      if (pA[i] !== pB[i]) return pB[i] - pA[i];
    }
    return 0;
  })[0];

  const target = actor.side === "left" ? right : left;
  return [actor, target, elapsed];
}

function performAction(actor, target, left, right) {
  const skill = selectActiveSkill(actor, target, left, right);
  if (!skill) {
    const dmg = basicDmg(effectiveAtk(actor, target, left, right), effectiveDef(target, actor, left, right));
    target.hp = Math.max(0, target.hp - dmg);
    return;
  }

  actor.mp -= skill.mp_cost;
  if (skill.once_per_battle) actor.usedOnce.add(skill.skill_id);

  const baseDmg = basicDmg(effectiveAtk(actor, target, left, right), effectiveDef(target, actor, left, right));

  for (const eff of skill.effects) {
    if (eff.type === "damage_multiplier") {
      const dmg = Math.max(1, Math.floor(baseDmg * eff.value));
      target.hp = Math.max(0, target.hp - dmg);
    }
    if (eff.type === "heal") {
      const recipient = eff.target === "self" ? actor : target;
      recipient.hp = Math.min(recipient.maxHp, recipient.hp + Math.floor(eff.value));
    }
  }
}

function selectActiveSkill(actor, target, left, right) {
  for (const skill of actor.active) {
    if (actor.mp < skill.mp_cost) continue;
    if (skill.once_per_battle && actor.usedOnce.has(skill.skill_id)) continue;
    if (isConditionMet(skill.condition, actor, target, left, right)) return skill;
  }
  return null;
}

function basicDmg(atk, def) {
  return Math.max(1, atk - def);
}

function effectiveAtk(combatant, target, left, right) {
  return effectiveStat(combatant, target, left, right, "attack");
}

function effectiveDef(combatant, target, left, right) {
  return effectiveStat(combatant, target, left, right, "defense");
}

function effectiveSpeed(combatant, opponent) {
  return effectiveStatSimple(combatant, opponent, "speed");
}

function effectiveStatSimple(combatant, opponent, stat) {
  let bonus = 0;
  let mult = 1.0;
  for (const skill of combatant.passive) {
    if (!isConditionMetSimple(skill.condition, combatant, opponent)) continue;
    for (const eff of skill.effects) {
      if (eff.stat === stat) {
        if (eff.type === "stat_bonus") bonus += eff.value;
        else if (eff.type === "stat_multiplier") mult *= eff.value;
      }
    }
  }
  return Math.max(0, Math.floor((combatant.stats[stat] + bonus) * mult));
}

function effectiveStat(combatant, target, left, right, stat) {
  const opponent = combatant.side === "left" ? right : left;
  return effectiveStatSimple(combatant, opponent, stat);
}

function isConditionMet(cond, actor, target, left, right) {
  if (!cond) return true;
  const actorOpp = actor.side === "left" ? right : left;
  const targetOpp = target.side === "left" ? right : left;
  return isConditionMetSimple(cond, actor, target);
}

function isConditionMetSimple(cond, actor, target) {
  if (!cond || cond.type === "always") return true;
  if (cond.type === "self_hp_pct_lte") return actor.hp / actor.stats.hp <= cond.value;
  if (cond.type === "self_hp_pct_gte") return actor.hp / actor.stats.hp >= cond.value;
  if (cond.type === "target_hp_pct_lte") return target.hp / target.stats.hp <= cond.value;
  if (cond.type === "target_hp_pct_gte") return target.hp / target.stats.hp >= cond.value;
  if (cond.type === "all") return (cond.conditions || []).every((c) => isConditionMetSimple(c, actor, target));
  if (cond.type === "any") return (cond.conditions || []).some((c) => isConditionMetSimple(c, actor, target));
  return true;
}

function finishSim(left, right, winner, reason, actions, time) {
  if (winner === "left") {
    left.hp = Math.min(left.maxHp, left.hp + left.stats.recovery);
  } else if (winner === "right") {
    right.hp = Math.min(right.maxHp, right.hp + right.stats.recovery);
  }
  return {
    won: winner === "left",
    draw: winner === null,
    reason,
    hpLeft: left.hp,
    hpMax: left.maxHp,
    actions,
  };
}

/* ========== Global Exports ========== */
window.allocateExperience = allocateExperience;
window.craft = craft;
window.purchaseUpgrade = purchaseUpgrade;
window.equip = equip;
window.equipFromPopup = equipFromPopup;
window.unequip = unequip;
window.selectHunt = selectHunt;
window.endTurn = endTurn;
window.openEquipPopup = openEquipPopup;
window.closeCombatModal = closeCombatModal;
