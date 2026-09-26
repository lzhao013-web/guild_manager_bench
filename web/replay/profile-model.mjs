import { numeric } from './compare-model.mjs';

const COST_TOOLS = { recruit_adventurer: '招募', craft_equipment: '制作', purchase_upgrade: '公会升级' };
const QUERY_TOOLS = new Set(['get_party', 'get_monsters', 'get_crafting', 'get_inventory', 'get_upgrades', 'get_recruitment', 'get_events']);
const PREVIEW_TOOLS = new Set(['preview_battle', 'preview_team_power']);
const percent = (part, total) => `${Math.round(part / total * 100)}%`;
const eventRef = (turn, attempt = null, step = null) => ({ turn, attempt, step });

function spent(event, resource) {
  const before = numeric(event.beforeObservation?.[resource]);
  const after = numeric(event.afterObservation?.[resource]);
  return before !== null && after !== null && before >= after ? before - after : null;
}

function flatten(round, attempt, index) {
  return attempt.actions.map(action => ({ ...action, turn: round.number, attempt: index, step: action.index }));
}

function trainingProfile(events) {
  const allocations = events.filter(e => e.name === 'allocate_experience');
  const recipients = new Map();
  let knownAmount = 0, unknownAmounts = 0, unresolvedTargets = 0, unresolvedAmount = 0;
  for (const event of allocations) {
    const declared = numeric(event.arguments.amount);
    const amount = declared !== null && declared >= 0 ? declared : spent(event, 'experience_pool');
    const member = event.subjects[0]?.item;
    if (amount === null) unknownAmounts++;
    else knownAmount += amount;
    if (!member) {
      unresolvedTargets++;
      unresolvedAmount += amount ?? 0;
      continue;
    }
    let recipient = recipients.get(member.adventurer_id);
    if (!recipient) {
      recipient = { id: member.adventurer_id, name: member.name, amount: 0, unknownAmounts: 0, events: [] };
      recipients.set(member.adventurer_id, recipient);
    }
    if (amount === null) recipient.unknownAmounts++;
    else recipient.amount += amount;
    recipient.events.push(event);
  }
  const ordered = [...recipients.values()].sort((a, b) => b.amount - a.amount || a.id.localeCompare(b.id));
  for (const recipient of ordered) {
    recipient.label = ordered.filter(r => r.name === recipient.name).length > 1 ? `${recipient.name} · ${recipient.id}` : recipient.name;
  }
  return { calls: allocations.length, events: allocations, knownAmount, unknownAmounts, unresolvedTargets, unresolvedAmount, recipients: ordered };
}

function investmentProfile(events, completed, horizon) {
  const spending = events.filter(e => Object.hasOwn(COST_TOOLS, e.name)).map(e => ({ ...e, gold: spent(e, 'gold') }));
  const sum = values => values.reduce((total, e) => total + (e.gold ?? 0), 0);
  const phases = [];
  if (Number.isInteger(horizon) && horizon > 0) {
    let start = 1;
    for (let i = 1; i <= 3; i++) {
      const end = Math.ceil(horizon * i / 3);
      if (start <= end) {
        const subset = spending.filter(e => e.turn >= start && e.turn <= end);
        phases.push({ start, end, knownGold: sum(subset), unknownCosts: subset.filter(e => e.gold === null).length,
          calls: subset.length, events: subset, observedRounds: completed.filter(r => r.number >= start && r.number <= end).length });
      }
      start = end + 1;
    }
  }
  return {
    calls: spending.length, events: spending, knownGold: sum(spending), unknownCosts: spending.filter(e => e.gold === null).length, phases,
    first: Object.fromEntries(Object.keys(COST_TOOLS).map(name => [name, spending.find(e => e.name === name) || null])),
    categories: Object.entries(COST_TOOLS).map(([name, label]) => {
      const subset = spending.filter(e => e.name === name);
      return { name, label, knownGold: sum(subset), unknownCosts: subset.filter(e => e.gold === null).length, events: subset };
    }),
  };
}

function equipmentSnapshot(round) {
  const inventory = round?.selected.after?.equipment_inventory;
  if (!Array.isArray(inventory)) return null;
  return { turn: round.number, total: inventory.length,
    free: inventory.filter(item => item.equipped_by === null),
    unknown: inventory.filter(item => typeof item.equipped_by !== 'string' && item.equipped_by !== null).length };
}

function equipmentProfile(events, completed) {
  const crafts = events.filter(e => e.name === 'craft_equipment');
  const equips = events.filter(e => e.name === 'equip_item');
  const unequips = events.filter(e => e.name === 'unequip_item');
  const swaps = [];
  let unknownSwaps = 0;
  for (const event of equips) {
    const member = event.subjects[0]?.item, item = event.subjects[1]?.item;
    const before = event.beforeObservation?.adventurers?.find(a => a.adventurer_id === member?.adventurer_id);
    if (!before || !Array.isArray(before.equipment) || !item?.slot) {
      unknownSwaps++;
      continue;
    }
    const previous = before.equipment.find(e => e.slot === item.slot);
    if (previous && previous.instance_id !== item.instance_id) swaps.push(event);
  }
  return { crafted: crafts.length, equipped: equips.length, unequipped: unequips.length,
    crafts, equips, unequips, swaps, unknownSwaps, snapshot: equipmentSnapshot(completed.at(-1)) };
}

function stableArgs(args) {
  const canonical = value => Array.isArray(value) ? value.map(canonical)
    : value && typeof value === 'object' ? Object.fromEntries(Object.entries(value).sort(([a], [b]) => a.localeCompare(b)).map(([k, v]) => [k, canonical(v)])) : value;
  const { session_id: _session, ...rest } = args;
  return JSON.stringify(canonical(rest));
}

function habitsProfile(rounds) {
  const groups = [
    ['queries', '事实查询'], ['previews', '模拟预览'], ['management', '经营操作'],
    ['settlement', '结束回合'], ['memo', '备忘录'], ['other', '其他工具'],
  ].map(([key, label]) => ({ key, label, count: 0, events: [] }));
  const byKey = new Map(groups.map(g => [g.key, g]));
  let totalCalls = 0, failed = 0, unknown = 0, responseCount = 0;
  const memos = [], followups = [];
  for (const round of rounds) {
    for (const [index, attempt] of round.attempts.entries()) {
      const calls = flatten(round, attempt, index);
      responseCount += attempt.notes.filter(n => n.type === 'model').length;
      for (const [position, event] of calls.entries()) {
        totalCalls++;
        if (event.success === null) unknown++;
        if (event.success === false) {
          failed++;
          const next = calls[position + 1] || null;
          const kind = !next ? 'none' : next.name !== event.name ? 'tool'
            : stableArgs(next.arguments) === stableArgs(event.arguments) ? 'same' : 'arguments';
          followups.push({ event, next, kind });
        }
        const key = QUERY_TOOLS.has(event.name) ? 'queries' : PREVIEW_TOOLS.has(event.name) ? 'previews'
          : event.economic ? 'management' : event.name === 'end_turn' ? 'settlement' : event.name === 'write_memo' ? 'memo' : 'other';
        const group = byKey.get(key);
        group.count++;
        group.events.push(event);
        if (event.name === 'write_memo' && event.success === true) memos.push(event);
      }
    }
  }
  return { totalCalls, failed, unknown, responseCount, groups, memos, followups };
}

function resourceProfile(completed) {
  const points = completed.map(round => ({ turn: round.number, gold: numeric(round.selected.after?.gold),
    xp: numeric(round.selected.after?.experience_pool), freeEquipment: equipmentSnapshot(round) }));
  const peak = key => points.filter(p => p[key] !== null).sort((a, b) => b[key] - a[key] || a.turn - b.turn)[0] || null;
  let streak = null, longestXp = null;
  for (const point of points) {
    if (point.xp === null || point.xp <= 0) { streak = null; continue; }
    streak = streak && point.turn === streak.end + 1
      ? { start: streak.start, end: point.turn, length: streak.length + 1 }
      : { start: point.turn, end: point.turn, length: 1 };
    if (!longestXp || streak.length > longestXp.length) longestXp = streak;
  }
  return { points, peakGold: peak('gold'), peakXp: peak('xp'), longestXp, last: points.at(-1) || null };
}

function insightsFor(profile) {
  const { training, investment, equipment, habits, resources, coverage } = profile;
  const incomplete = '尚无已完成回合，不生成经营结论。';
  const allRounds = [...profile.replay.rounds.values()];
  const completed = allRounds.filter(r => r.selected.completed);
  const roundEvidence = (completed.length ? completed : allRounds).map(r => eventRef(r.number));
  const top = training.recipients[0];
  const trainingText = !coverage.completed ? incomplete
    : training.unknownAmounts && !training.knownAmount ? '已记录经验分配，但分配量未知，暂不判断培养重心。'
    : !training.calls ? '已完成回合中未记录到成功的经验分配。'
    : !top && training.unresolvedTargets ? `${training.knownAmount} 点已知分配经验的接收对象无法识别。`
    : top && training.knownAmount > 0 ? `${top.label} 获得 ${percent(top.amount, training.knownAmount)} 的已知分配经验，已识别 ${training.recipients.length} 位接收者。`
    : '已记录的分配量为零，暂未形成可比较的培养投入。';
  const first = investment.events[0];
  const investmentText = !coverage.completed ? incomplete : first
    ? `最早的招募、制作或升级记录在第 ${first.turn} 回合：${COST_TOOLS[first.name]}。已知总支出 ${investment.knownGold} 金币${investment.unknownCosts ? `，另有 ${investment.unknownCosts} 笔金额未知` : ''}。`
    : '已完成回合中未记录到招募、制作或公会升级支出。';
  const equipmentText = !coverage.completed ? incomplete
    : `成功制作 ${equipment.crafted} 次、穿戴 ${equipment.equipped} 次、卸下 ${equipment.unequipped} 次；可确认替换已有装备 ${equipment.swaps.length} 次。`;
  const query = habits.groups.find(g => g.key === 'queries');
  const habitsText = habits.totalCalls
    ? `全部已记录尝试中，事实查询占 ${percent(query.count, habits.totalCalls)} 的工具调用；已确认失败 ${habits.failed} 次，成功写入备忘录 ${habits.memos.length} 次。`
    : '没有可分析的工具调用记录。';
  const resourcesText = !resources.last ? incomplete
    : `最后已完成的第 ${resources.last.turn} 回合末，金币 ${resources.last.gold ?? '未知'}，经验池 ${resources.last.xp ?? '未知'}。余额本身不代表浪费。`;
  return [
    { key: 'training', title: '培养路线', text: trainingText, events: top?.events || (training.events.length ? training.events : roundEvidence) },
    { key: 'investment', title: '投资节奏', text: investmentText, events: first ? [first] : roundEvidence },
    { key: 'equipment', title: '装备使用', text: equipmentText, events: equipment.swaps.length ? equipment.swaps : equipment.equips.length ? equipment.equips : roundEvidence },
    { key: 'habits', title: '行动习惯', text: habitsText, events: habits.followups.length || habits.memos.length ? habits.followups.map(f => f.event).concat(habits.memos) : roundEvidence },
    { key: 'resources', title: '资源利用', text: resourcesText, events: resources.last ? [eventRef(resources.last.turn)] : roundEvidence },
  ];
}

export function buildProfile(normalized) {
  const rounds = [...normalized.rounds.values()];
  const completed = rounds.filter(r => r.selected.completed);
  const events = completed.flatMap(round => flatten(round, round.selected, round.attempts.indexOf(round.selected)));
  const successful = events.filter(e => e.success === true);
  const profile = {
    meta: normalized.meta, replay: normalized,
    coverage: {
      completed: completed.length, maxTurns: normalized.meta.maxTurns, records: rounds.length,
      attempts: rounds.reduce((sum, r) => sum + r.attempts.length, 0),
      failedAttempts: rounds.reduce((sum, r) => sum + r.attempts.filter(a => a.status === 'failed').length, 0),
      missingSteps: completed.filter(r => !r.selected.hasSteps).length,
      unknownEconomicOutcomes: events.filter(e => e.economic && e.success === null).length,
    },
    training: trainingProfile(successful),
    investment: investmentProfile(successful, completed, normalized.meta.maxTurns),
    equipment: equipmentProfile(successful, completed),
    habits: habitsProfile(rounds),
    resources: resourceProfile(completed),
  };
  profile.insights = insightsFor(profile);
  return profile;
}
