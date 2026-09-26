// 构造的展示样例。分数用于测试 UI，不代表真实模型或真实评分结果。
export function observation(turn, gold = 100, extra = {}) {
  return {
    turn, max_turns: 3, gold, experience_pool: 80, party_size: 1, party_size_limit: 3,
    seed: 42, scoring: { seed: 99, waves: 10, resource_mode: 'full' },
    adventurers: [{ adventurer_id: 'hero-a', template_id: 'warrior', name: '先锋', level: 1,
      resources: { current_hp: 100, current_mp: 10 }, effective_stats: { hp: 100, attack: 20 }, equipment: [] }],
    equipment_inventory: [], global_upgrades: [], materials: {},
    recruit_candidates: [{ candidate_id: 'candidate-a', template_id: 'mage', name: '法师', recruit_gold: 30 }],
    monsters: [{ monster_id: 'monster-a', archetype_id: 'wolf', name: '森林狼', tier: 1 }],
    crafting_recipes: [{ recipe_id: 'sword', name: '铁剑配方', output_name: '铁剑' }],
    ...extra,
  };
}
export function tool(name, args = {}, extra = {}) {
  return { type: 'tool_result', name, arguments: args, content: `成功 ${name}`, result: { ok: true }, ...extra };
}
export function turn(number, score, steps = []) {
  return { turn: number, status: 'completed', observation_before: observation(number), rank_score: score,
    steps: [...steps, tool('end_turn', { hunts: [] }, {
      result: { ok: true, turn_result: { battles: [] } }, observation_after: observation(number + 1),
    })] };
}
export function replay(turns, extra = {}) {
  return { schema_version: 1, kind: 'llm_replay', status: 'completed', session_id: 'test-session',
    agent: { type: 'OpenAIChatCompletionsAgent', config: { model: '测试模型' } },
    data: { preset: 'default', data_hash: 'same-hash', game_seed: 42, scoring_seed: 99 },
    config: { max_tool_calls_per_turn: 20, objective: '原始目标' }, turns,
    final_observation: observation(4), ...extra };
}

export function browserReplays() {
  const a = replay([turn(1, 100), turn(2, 200), turn(3, 220)]);
  const b = replay([turn(1, 95), turn(2, 125), turn(3, 210)]);
  a.agent.config.model = '样例模型 · 扩张路线';
  b.agent.config.model = '样例模型 · 集中培养';
  a.session_id = 'synthetic-a';
  b.session_id = 'synthetic-b';
  const recruited = observation(1, 70, { party_size: 2 });
  recruited.adventurers.push({ adventurer_id: 'hero-mage', name: '法师', template_id: 'mage', level: 1,
    resources: { current_hp: 65, current_mp: 35 }, effective_stats: { hp: 65, attack: 25 }, equipment: [] });
  const trained = observation(1, 100, { experience_pool: 0 });
  trained.adventurers[0].level = 2;
  trained.adventurers[0].effective_stats.attack = 28;
  a.turns[0].steps.unshift(tool('recruit_adventurer', { candidate_id: 1 }, { observation_after: recruited, content: '成功 recruit_adventurer：招募 法师\n金币 100 -> 70' }));
  b.turns[0].steps.unshift(tool('allocate_experience', { adventurer_id: 1, amount: 80 }, { observation_after: trained, content: '成功 allocate_experience：分配 80 经验给 先锋' }));
  for (const [record, obs] of [[a.turns[0], recruited], [b.turns[0], trained]]) {
    record.steps.unshift({ type: 'assistant', content: '这是构造的模型正文，用于验证回放显示。', reasoning_content: '这是构造的 API 推理摘要。' });
    const end = record.steps.at(-1);
    end.arguments.hunts = [{ adventurer_id: 1, monster_id: 1 }];
    end.result.turn_result.battles = [{ adventurer_id: 'hero-a', adventurer_name: '先锋', monster_id: 'monster-a', monster_name: '森林狼', won: true, reward: { gold: 12, experience: 24, materials: { leather: 1 } } }];
    end.observation_after = { ...structuredClone(obs), turn: 2, gold: obs.gold + 12, experience_pool: obs.experience_pool + 24 };
  }
  a.turns[1].observation_before = structuredClone(a.turns[0].steps.at(-1).observation_after);
  b.turns[1].observation_before = structuredClone(b.turns[0].steps.at(-1).observation_after);
  b.turns.splice(1, 0, { turn: 2, status: 'failed', failure_reason: 'empty_response_limit', observation_before: observation(2), steps: [tool('equip_item', { adventurer_id: 1, equipment_instance_id: 'missing' }, { result: { ok: false, error: '未找到装备实例' }, content: '失败 equip_item：未找到装备实例' })] });
  return { a, b };
}
