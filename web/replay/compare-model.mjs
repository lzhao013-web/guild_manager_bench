// 对照页的只读数据投影。缺失字段保持未知，不重建游戏，也不计算评分。
export const ACTION_LABELS = {
  recruit_adventurer: '招募', dismiss_adventurer: '解散', allocate_experience: '经验分配',
  craft_equipment: '装备制作', purchase_upgrade: '公会升级', equip_item: '穿戴装备', unequip_item: '卸下装备',
};
const TOOL_LABELS = {
  ...ACTION_LABELS, end_turn: '回合结算', write_memo: '记录备忘', get_party: '查看队伍',
  get_monsters: '查看怪物', get_crafting: '查看配方', get_inventory: '查看库存',
  get_upgrades: '查看升级', get_recruitment: '查看候选', get_events: '查看事件',
  preview_battle: '战斗预览', preview_team_power: '评分预览',
};
const ENTITIES = {
  adventurer: ['adventurers', 'adventurer_id', '冒险者'], monster: ['monsters', 'monster_id', '怪物'],
  recipe: ['crafting_recipes', 'recipe_id', '配方'], upgrade: ['global_upgrades', 'upgrade_id', '升级'],
  recruit: ['recruit_candidates', 'candidate_id', '候选'], equipment: ['equipment_inventory', 'instance_id', '装备'],
};
const object = value => value !== null && typeof value === 'object' && !Array.isArray(value) ? value : null;
const list = value => Array.isArray(value) ? value : [];
export const numeric = value => typeof value === 'number' && Number.isFinite(value) ? value : null;
const known = value => value !== null && value !== undefined;
const difference = (a, b) => numeric(a) !== null && numeric(b) !== null ? a - b : null;
const canonical = value => JSON.stringify(value, function (_key, item) {
  return object(item) ? Object.fromEntries(Object.entries(item).sort(([a], [b]) => a.localeCompare(b))) : item;
});

function structuredResult(step) {
  if (object(step.result)) return step.result;
  if (object(step.content)) return step.content;
  if (typeof step.content === 'string' && step.content.trimStart().startsWith('{')) {
    try { return object(JSON.parse(step.content)); } catch { return null; }
  }
  return null;
}

function outcome(step, result) {
  if (step.ok === false || result?.ok === false || step.error || result?.error) return false;
  if (step.ok === true || result?.ok === true) return true;
  const content = typeof step.content === 'string' ? step.content.trimStart() : '';
  if (/^(?:失败|错误|FAIL\b|ERROR\b)/i.test(content)) return false;
  if (/^(?:成功|OK\b)/i.test(content)) return true;
  return null;
}

function updateRefs(refs, observation, saved = null) {
  for (const [category, [key, idKey]] of Object.entries(ENTITIES)) {
    const values = refs[category];
    for (const [id, number] of Object.entries(object(saved?.[category]) || {})) values.set(id, number);
    let next = Math.max(0, ...values.values());
    for (const item of list(observation?.[key])) {
      if (object(item) && typeof item[idKey] === 'string' && !values.has(item[idKey])) values.set(item[idKey], ++next);
    }
  }
}

function entity(category, value, refs, before, after) {
  const [key, idKey, label] = ENTITIES[category];
  const isRef = Number.isInteger(value) || (typeof value === 'string' && /^\d+$/.test(value));
  const id = isRef ? [...refs[category]].find(([, ref]) => ref === Number(value))?.[0] : value;
  const found = [...list(before?.[key]), ...list(after?.[key])].find(item => object(item) && known(id) && item[idKey] === id);
  return found && typeof found.name === 'string'
    ? { name: found.name, key: [found.template_id || found.archetype_id || '', found.name], item: found }
    : { name: `${label} #${value ?? '?'}`, key: null, item: null };
}

function actionDescription(name, args, resolve) {
  const label = TOOL_LABELS[name] || name;
  const target = (category, field) => resolve(category, args[field]);
  let subjects = [], detail = '';
  if (name === 'recruit_adventurer') subjects = [target('recruit', 'candidate_id')];
  if (name === 'dismiss_adventurer') subjects = [target('adventurer', 'adventurer_id')];
  if (name === 'craft_equipment') subjects = [target('recipe', 'recipe_id')];
  if (name === 'purchase_upgrade') subjects = [target('upgrade', 'upgrade_id')];
  if (name === 'allocate_experience') {
    subjects = [target('adventurer', 'adventurer_id')];
    detail = `${args.amount ?? '?'} 经验`;
  }
  if (name === 'equip_item') subjects = [target('adventurer', 'adventurer_id'), target('equipment', 'equipment_instance_id')];
  if (name === 'unequip_item') {
    subjects = [target('adventurer', 'adventurer_id')];
    detail = { weapon: '武器', armor: '护甲', accessory: '饰品' }[args.slot] || String(args.slot ?? '未知槽位');
  }
  if (name === 'end_turn' && Array.isArray(args.hunts)) detail = `安排 ${args.hunts.length} 场讨伐`;
  if (name === 'write_memo' && typeof args.content === 'string') detail = args.content.slice(0, 120);
  const title = [label, subjects.map(s => s.name).join(' / '), detail].filter(Boolean).join(' · ');
  const signature = subjects.length && subjects.every(s => s.key !== null)
    ? canonical([name, subjects.map(s => s.key), args.amount ?? null, args.slot ?? null]) : null;
  return { title, signature };
}

function changesBetween(before, after) {
  if (!before || !after) return [];
  const changes = [];
  function add(label, previous, current) {
    if (numeric(previous) !== null && numeric(current) !== null && previous !== current) changes.push(`${label} ${previous} → ${current} (${current - previous >= 0 ? '+' : ''}${current - previous})`);
  }
  add('金币', before.gold, after.gold);
  add('经验池', before.experience_pool, after.experience_pool);
  add('队伍人数', before.party_size, after.party_size);
  add('人数上限', before.party_size_limit, after.party_size_limit);
  if (object(before.materials) && object(after.materials)) {
    for (const key of new Set([...Object.keys(before.materials), ...Object.keys(after.materials)])) add(key, before.materials[key] ?? 0, after.materials[key] ?? 0);
  }
  const previousMembers = new Map(list(before.adventurers).map(a => [a.adventurer_id, a]));
  for (const member of list(after.adventurers)) {
    const previous = previousMembers.get(member.adventurer_id);
    if (!previous) continue;
    add(`${member.name} 等级`, previous.level, member.level);
    for (const [key, label] of [['attack', '攻击'], ['defense', '防御'], ['hp', '最大生命']]) add(`${member.name} ${label}`, previous.effective_stats?.[key], member.effective_stats?.[key]);
    add(`${member.name} 生命`, previous.resources?.current_hp, member.resources?.current_hp);
  }
  return changes;
}

function battleData(result, resolve) {
  const source = result?.turn_result?.battles ?? result?.event?.battles;
  if (!Array.isArray(source)) return null;
  return source.filter(object).map(battle => {
    const hero = resolve('adventurer', battle.adventurer_id);
    const monster = resolve('monster', battle.monster_id);
    const heroName = battle.adventurer_name || hero.name;
    const monsterName = battle.monster_name || monster.name;
    return {
      hero: heroName, monster: monsterName, won: typeof battle.won === 'boolean' ? battle.won : null,
      gold: numeric(battle.reward?.gold), experience: numeric(battle.reward?.experience),
      materials: object(battle.reward?.materials),
      signature: (hero.key || battle.adventurer_name) && (monster.key || battle.monster_name)
        ? canonical([heroName, monsterName]) : null,
    };
  });
}

function normalizeAttempt(turn, index) {
  const refs = Object.fromEntries(Object.keys(ENTITIES).map(key => [key, new Map()]));
  const before = object(turn.observation_before);
  let current = before, after = null, battles = null;
  updateRefs(refs, before);
  const counts = Object.fromEntries(Object.keys(ACTION_LABELS).map(key => [key, 0]));
  const actions = [], notes = [];
  let failedTools = 0, unknownTools = 0;
  for (const [stepIndex, step] of list(turn.steps).entries()) {
    if (!object(step)) continue;
    if (step.type === 'assistant') {
      notes.push({ type: 'model', text: typeof step.content === 'string' ? step.content : '', reasoning: typeof step.reasoning_content === 'string' ? step.reasoning_content : '' });
      continue;
    }
    if (step.type === 'retry_prompt') {
      notes.push({ type: 'retry', text: String(step.content ?? '') });
      continue;
    }
    if (step.type !== 'tool_result') continue;
    const result = structuredResult(step);
    const success = outcome(step, result);
    const snapshot = object(step.observation_after);
    updateRefs(refs, current, result?._llm_refs);
    const resolve = (category, id) => entity(category, id, refs, current, snapshot);
    const args = object(step.arguments) || {};
    const name = String(step.name || '未记录工具名称');
    const action = {
      name, index: stepIndex, success, ...actionDescription(name, args, resolve),
      changes: success === true ? changesBetween(current, snapshot) : [],
      arguments: args, content: typeof step.content === 'string' ? step.content : JSON.stringify(step.content ?? ''),
      error: String(result?.error || step.error || ''), economic: Object.hasOwn(ACTION_LABELS, name),
    };
    if (success === false) failedTools++;
    if (success === null) unknownTools++;
    if (action.economic && success === true) counts[name]++;
    actions.push(action);
    if (name === 'end_turn' && success === true) {
      battles = battleData(result, resolve);
      after = snapshot;
    }
    if ((action.economic || name === 'end_turn') && success !== false) {
      // 中间写操作缺少快照时，不能把后续的累计变化归到下一次操作。
      current = snapshot;
      updateRefs(refs, snapshot);
    }
  }
  const completed = turn.status === 'completed';
  return {
    index, status: turn.status || 'incomplete', completed, before, hasSteps: Array.isArray(turn.steps),
    after: completed ? after : current, score: completed ? numeric(turn.rank_score) : null,
    counts, actions, notes, battles, failedTools, unknownTools, failureReason: turn.failure_reason || null,
  };
}

function metadata(replay, source) {
  const observed = replay.turns.find(t => object(t.observation_before))?.observation_before || replay.final_observation;
  const config = object(replay.config) || {};
  const data = object(replay.data) || {};
  const agent = replay.agent?.config;
  return {
    model: agent?.model || source || '未命名模型', source, session: replay.session_id || '未记录会话',
    provider: replay.agent?.type || '未记录 provider', status: replay.status || '未记录',
    preset: data.preset ?? null, hash: data.data_hash ?? null,
    gameSeed: data.game_seed ?? observed?.seed ?? config.game_seed ?? null,
    scoringSeed: data.scoring_seed ?? observed?.scoring?.seed ?? replay.score?.seed ?? config.scoring_seed ?? null,
    scoring: object(observed?.scoring) && Object.keys(observed.scoring).length ? observed.scoring : null,
    maxTurns: numeric(observed?.max_turns),
    budget: numeric(config.max_tool_calls_per_turn), objective: config.objective ?? null,
    reasoning: agent?.reasoning_effort ?? agent?.effort ?? null,
  };
}

export function normalizeReplay(replay, source = '') {
  if (!object(replay) || replay.kind !== 'llm_replay' || !Array.isArray(replay.turns)) throw new Error('请选择 LLM 跑局的 replay.json，不支持统计报告或手动回放。');
  if (known(replay.schema_version) && replay.schema_version !== 1) throw new Error(`不支持的 replay 版本：${replay.schema_version}`);
  const rounds = new Map();
  for (const [index, turn] of replay.turns.entries()) {
    if (!object(turn) || !Number.isInteger(turn.turn) || turn.turn < 1 || (known(turn.steps) && !Array.isArray(turn.steps))) throw new Error(`第 ${index + 1} 条回合记录格式不正确。`);
    const round = rounds.get(turn.turn) || { number: turn.turn, attempts: [] };
    round.attempts.push(normalizeAttempt(turn, index));
    rounds.set(turn.turn, round);
  }
  const ordered = new Map([...rounds].sort(([a], [b]) => a - b));
  for (const round of ordered.values()) round.selected = round.attempts.findLast(a => a.completed) || round.attempts.at(-1);
  for (const [number, round] of ordered) {
    const selected = round.selected;
    if (selected.completed && !selected.after) {
      const next = ordered.get(number + 1)?.selected.before;
      const final = replay.final_observation;
      selected.after = next || (replay.status === 'completed' && final?.turn === number + 1 ? object(final) : null);
    }
    round.scoreDelta = difference(selected.score, ordered.get(number - 1)?.selected.score);
  }
  return { meta: metadata(replay, source), rounds: ordered };
}

function conditionsFor(a, b) {
  return [
    ['数据哈希', 'hash'], ['游戏种子', 'gameSeed'], ['评分种子', 'scoringSeed'],
    ['评分配置', 'scoring'], ['总回合数', 'maxTurns'], ['工具预算', 'budget'], ['经营目标', 'objective'],
  ].map(([label, key]) => {
    const left = a[key], right = b[key];
    return { label, left, right, state: !known(left) || !known(right) ? 'unknown' : canonical(left) === canonical(right) ? 'same' : 'different' };
  });
}

function decisionDifferences(a, b) {
  const differences = [];
  for (const [name, label] of Object.entries(ACTION_LABELS)) {
    if ([...a.actions, ...b.actions].some(action => action.name === name && action.success === null)) continue;
    if (a.counts[name] !== b.counts[name]) {
      differences.push(`${label}：A ${a.counts[name]} 次 / B ${b.counts[name]} 次`);
      continue;
    }
    const left = a.actions.filter(s => s.name === name && s.success === true);
    const right = b.actions.filter(s => s.name === name && s.success === true);
    if (left.length && [...left, ...right].every(s => s.signature !== null)) {
      if (canonical(left.map(s => s.signature).sort()) !== canonical(right.map(s => s.signature).sort())) differences.push(`${label}的对象或数量不同`);
    }
  }
  if (a.battles !== null && b.battles !== null) {
    if (a.battles.length !== b.battles.length) differences.push(`讨伐场数：A ${a.battles.length} 场 / B ${b.battles.length} 场`);
    else if ([...a.battles, ...b.battles].every(battle => battle.signature !== null) && canonical(a.battles.map(b => b.signature).sort()) !== canonical(b.battles.map(b => b.signature).sort())) differences.push('讨伐的出战成员或目标不同');
  }
  return differences;
}

export function compareReplays(left, right) {
  const conditions = conditionsFor(left.meta, right.meta);
  const incompatible = conditions.some(c => c.state === 'different');
  const numbers = [...new Set([...left.rounds.keys(), ...right.rounds.keys()])].sort((a, b) => a - b);
  const rounds = numbers.map(number => {
    const a = left.rounds.get(number) || null, b = right.rounds.get(number) || null;
    const completed = Boolean(a?.selected.completed && b?.selected.completed);
    const decisionComparable = completed && a.selected.hasSteps && b.selected.hasSteps;
    const gap = completed ? difference(a.selected.score, b.selected.score) : null;
    const previousA = left.rounds.get(number - 1)?.selected, previousB = right.rounds.get(number - 1)?.selected;
    const previousGap = previousA?.completed && previousB?.completed ? difference(previousA.score, previousB.score) : null;
    return {
      number, left: a, right: b, gap,
      gapGrowth: gap !== null && previousGap !== null ? Math.abs(gap) - Math.abs(previousGap) : null,
      decisionComparable, differences: decisionComparable ? decisionDifferences(a.selected, b.selected) : [],
      hasFailure: [a, b].some(side => side?.attempts.some(attempt => attempt.status === 'failed' || attempt.failedTools > 0 || attempt.battles?.some(battle => battle.won === false))),
    };
  });
  const widening = incompatible ? null : rounds.filter(r => r.gapGrowth > 0).sort((a, b) => b.gapGrowth - a.gapGrowth || a.number - b.number)[0]?.number ?? null;
  return {
    left, right, conditions, incompatible, rounds,
    highlights: {
      widening, divergence: rounds.find(r => r.differences.length)?.number ?? null,
      failure: rounds.find(r => r.hasFailure)?.number ?? null,
    },
  };
}
