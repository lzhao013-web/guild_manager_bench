# Guild Manager Bench

公会经营策略游戏核心与可视化工具。项目目标是提供一个规则明确、状态可观察、可自动运行的回合制经营环境，用于评估长期规划、资源分配和行动决策能力。

## 当前功能

- 1v1 自动战斗，包含行动条、主动技能、被动技能、战斗事件记录。
- 回合制主流程：刷新怪物、回合内操作、提交交战列表、结算奖励并进入下一回合。
- 装备系统：右手、左手、双手、鞋子、头盔、护甲、饰品槽位。
- 合成系统：消耗金币和材料生成装备实例。
- 全局加成系统：消耗金币解锁属性和技能加成。
- 等级成长：经验池分配、升级、属性成长。
- YAML 数据加载：冒险者、怪物、装备、配方、全局加成、游戏规则。
- 可视化页面：人工操作、观看同一会话、操作轨迹、战斗记录、升级信息和装备槽位。
- 运行层：统一的 session/action/event 协议，支持自动运行和事件日志。

## 项目结构

```text
data/                         游戏数据 YAML 和 presets
  presets/default/            默认游戏配置 preset
src/guild_manager_bench/
  game/                       纯游戏核心
  runtime/                    会话、观察、事件、动作编解码
  api/                        FastAPI 接口和 WebSocket
  bench/                      自动运行和记录工具
    llm/                      LLM benchmark 工具协议
  cli.py                      命令行入口
tests/                        单元测试
web/static/                   静态可视化页面
```

`game/` 目录只包含游戏规则和结算逻辑，不依赖 API、页面或自动操作者。

## 环境要求

- Python 3.11+
- uv

## 安装与测试

```powershell
uv sync --all-groups
uv run pytest tests -q
```

## 启动可视化页面

```powershell
uv run guild-manager serve --host 127.0.0.1 --port 8000
```

打开：

```text
http://127.0.0.1:8000/
```

观看同一会话：

```text
http://127.0.0.1:8000/?session=<session_id>&watch=1
```

## 数据 Preset

推荐把可比较的游戏配置放在完整目录 preset 中：

```text
data/presets/<preset_name>/
  game.yaml
  adventurers.yaml
  monsters.yaml
  equipment.yaml
  crafting_recipes.yaml
  global_upgrades.yaml
```

当前内置 `data/presets/default/`。启动时可以选择 preset：

```powershell
uv run guild-manager serve --preset default --host 127.0.0.1 --port 8000
```

不传 `--preset` 时默认读取 `data/presets/default/`。LLM 留档会在 `replay.json` 顶层记录 `data.preset`、`data.data_dir` 和 `data.data_hash`；续跑时如果旧 replay 记录了 hash 且当前数据不一致，会拒绝恢复，避免用不同规则继续同一局。

Preset 可以在 `game.yaml` 中控制 LLM 专用工具暴露：

```yaml
llm:
  expose_battle_preview: false
```

开启后，LLM harness 会额外注册 `preview_battle`。该工具每次只能预览一名冒险者和一个怪物的 1v1 战斗，不改变游戏状态，但会消耗一次非 `end_turn` 工具预算。

Preset 也可以在 `game.yaml` 中配置终局评分。默认评分器会在游戏结束后生成固定 Arena 波次，模拟大量 1v1 战斗，并按队伍在每波中的最优分配聚合为 0-100 分：

```yaml
scoring:
  mode: endgame_arena
  seed: 20260526
  waves: 1000
  wave_size: 6
  difficulty_factors: [8, 10, 12, 14]
  resource_mode: full
  aggregation: best_assignment
```

评分结果会写入 `run.score` 和 `replay.json` 顶层 `score` 字段。

等级经验规则仍在 `game.yaml` 的 `experience` 中配置；其中 `stat_growth_per_level` 是默认成长。冒险者可以在 `adventurers.yaml` 中用同名字段覆盖默认成长，用于区分职业定位。冒险者还可以配置 `level_skill_unlocks`，达到指定等级后自动解锁该职业自己的技能：

```yaml
adventurers:
  - id: vanguard
    name: 先锋
    stats:
      hp: 120
      mp: 10
      attack: 16
      defense: 9
      speed: 9
      recovery: 6
    stat_growth_per_level:
      hp: 18
      mp: 1
      attack: 2
      defense: 4
      speed: 1
      recovery: 2
    level_skill_unlocks:
      - level: 3
        skills:
          - id: bulwark_rally
            name: 壁垒集结
            kind: active
            priority: 165
            mp_cost: 4
            once_per_battle: true
            condition:
              type: self_hp_pct_lte
              value: 0.7
            effects:
              - type: apply_status
                target: self
                status:
                  id: fortified
                  name: 坚守
                  duration: 2
                  polarity: positive
                  stack_mode: refresh
                  effects:
                    - type: stat_bonus
                      stat: defense
                      value: 5
                      target: self
```

## 技能数据

技能可以挂在冒险者、装备和全局升级上。`kind: passive` 只支持属性修正效果；`kind: active` 支持伤害、治疗、MP 恢复和自损等战斗效果。

支持的条件：

- `always`
- `self_hp_pct_lte` / `self_hp_pct_gte`
- `target_hp_pct_lte` / `target_hp_pct_gte`
- `self_mp_pct_lte` / `self_mp_pct_gte`
- `target_mp_pct_lte` / `target_mp_pct_gte`
- `action_index_lte` / `action_index_gte`
- `all` / `any`

支持的主动效果：

- `damage_multiplier`
- `damage_bonus`
- `true_damage`
- `heal`
- `heal_percent`
- `mp_restore`
- `self_damage`
- `apply_status`

支持的被动效果：

- `stat_bonus`
- `stat_multiplier`

`apply_status` 用于施加单场战斗内状态。状态可以是负面状态，例如中毒、灼伤、破甲；也可以是正面状态，例如再生、鼓舞、专注。状态的 `duration` 表示未来多少次持有者行动内生效；伤害、治疗和 MP 恢复类状态会在持有者行动开始时结算，属性修正类状态会在持续期间影响持有者属性。

```yaml
effects:
  - type: apply_status
    target: target
    status:
      id: poison
      name: 中毒
      duration: 2
      polarity: negative
      stack_mode: refresh
      effects:
        - type: true_damage
          value: 3
```

## HTTP 接口概览

创建会话：

```http
POST /api/sessions
```

读取会话：

```http
GET /api/sessions/{session_id}
```

提交动作：

```http
POST /api/sessions/{session_id}/actions
```

动作示例：

```json
{"type": "craft", "recipe_id": "iron_sword_recipe"}
```

```json
{"type": "equip", "adventurer_id": "vanguard", "equipment_instance_id": "eq_0001"}
```

```json
{
  "type": "end_turn",
  "hunts": [
    {"adventurer_id": "vanguard", "monster_id": "turn_1_monster_1"}
  ]
}
```

实时观看：

```text
ws://127.0.0.1:8000/ws/sessions/{session_id}
```

## LLM Benchmark 工具协议

LLM benchmark 使用 `bench/llm` 下的纯 Python 强类型工具层。工具层只提供状态查询和动作执行，不包含合法动作枚举、推荐、估值或自动配队逻辑；回合预算、提示词和模型调用循环应放在 harness 中。
动作类工具返回精简变更摘要，不会自动返回完整状态；模型需要详细信息时应按模块主动调用 `get_party`、`get_monsters`、`get_crafting`、`get_inventory` 或 `get_upgrades`。

```python
from guild_manager_bench.bench.llm import GuildManagerTools

tools = GuildManagerTools.from_data_dir("data/presets/default")
session = tools.start_session()
session_id = session["session_id"]

party = tools.get_party(session_id)
monsters = tools.get_monsters(session_id)

tools.craft_equipment(session_id, "iron_sword_recipe")
tools.equip_item(session_id, "vanguard", "eq_0001")
tools.end_turn(
    session_id,
    [{"adventurer_id": "vanguard", "monster_id": "turn_1_monster_1"}],
)
```

可注册给 agent 的工具 schema：

```python
from guild_manager_bench.bench.llm import TurnToolHarness

harness = TurnToolHarness(tools, session_id, max_tool_calls=20)
schemas = harness.tool_schemas()
result = harness.call_tool("get_party")
```

`TurnToolHarness` 暴露给 LLM 的工具参数使用当前回合可见列表左侧的数字 id；harness 会在内部映射回真实 `adventurer_id`、`monster_id`、`recipe_id`、`upgrade_id` 和 `equipment_instance_id`。

Harness 还提供 `write_memo(content)`。模型可以用它记录任意重要文字；这些备忘会保存在当前 LLM run 中，并在下回合开始的 prompt 里以“备忘录”形式出现。调用该工具会消耗一次非 `end_turn` 工具预算。

完整跑局使用模型无关的 runner。模型适配器只需要实现 `respond(messages, tools)`，返回 `LlmAgentResponse`：

```python
from guild_manager_bench.bench.llm import (
    LlmAgentResponse,
    LlmRunConfig,
    LlmToolCall,
    run_llm_game,
)


class Agent:
    def respond(self, *, messages, tools):
        return LlmAgentResponse(
            tool_calls=[LlmToolCall("end_turn", {"hunts": []})],
        )


run = run_llm_game(
    Agent(),
    data_dir="data/presets/default",
    config=LlmRunConfig(max_tool_calls_per_turn=20),
)
```

OpenAI-compatible Chat Completions 接口可以直接使用内置适配器：

```python
from guild_manager_bench.bench.llm import OpenAIChatCompletionsAgent, run_llm_game

agent = OpenAIChatCompletionsAgent.from_env(model="your-model-name")
run = run_llm_game(agent, data_dir="data/presets/default")
```

默认会读取进程环境变量，也会解析项目根目录 `.env`。显式传入的参数或可视化页面中填写的值优先级最高，其次是进程环境变量，最后是 `.env`：

- `OPENAI_API_KEY`
- `OPENAI_BASE_URL`，默认 `https://api.openai.com/v1`
- `OPENAI_MODEL`，也可以通过 `from_env(model=...)` 显式传入
- `OPENAI_COMPAT_API_KEY`
- `OPENAI_COMPAT_BASE_URL`
- `OPENAI_COMPAT_MODEL`

可视化页面的 `LLM` 标签页提供真实模型调试界面，会通过 `/ws/llm/debug` 展示每回合上下文、流式模型输出、工具调用、工具返回和调试事件。

启动 LLM 调试时可以填写 `游戏 Seed` 和 `评分 Seed`，它们只覆盖本次 run，不会改写 YAML。留空时使用当前 preset 的 `rules.seed` 和 `scoring.seed`。归档会记录实际使用的 `data.game_seed` 和 `data.scoring_seed`；续跑时如果 seed 不一致会拒绝恢复。

LLM run 默认会自动留档到 `runs/llm/<timestamp>_<session_id>/`，该目录已被 `.gitignore` 忽略。run 开始时就会创建目录，过程中增量写入两份文件：

- `trace.jsonl`：完整调用链路，每个事件追加一行，包含模型请求 messages/tools、最终模型响应 raw payload、assistant metadata、工具调用参数和工具返回；流式输出只记录最终聚合结果，不逐 chunk 留档。
- `replay.json`：精简回放流程，原子覆盖更新，保留 turn prompt、assistant 输出、tool call/result 和 retry prompt，足以复原 LLM 的游戏操作过程。顶层 `status` 会从 `running` 更新到 `completed`、`failed` 或 `interrupted`；完成后还会写入终局 `score`。

如需关闭留档，可在代码里传入 `LlmRunConfig(archive_dir=None)`。

可视化页面的 `LLM` 标签页可以直接刷新并加载 `runs/llm` 下的归档，也可以手动打开本地 `replay.json`，用于重放 LLM 的回合级操作流程。对于 `interrupted` 的归档，可以点击“继续运行”；系统会重放已确认成功的变更型工具调用恢复游戏状态，并继续追加写入原 `trace.jsonl` 和原 `replay.json`。

Runner 规则：

- 非法游戏动作会把 `ok=false` 和 `error` 返回给模型。
- 非 `end_turn` 工具调用受 `max_tool_calls_per_turn` 限制。
- 预算耗尽后只允许 `end_turn`。
- 模型连续不调用工具、`end_turn` 连续非法或预算耗尽后仍不结束回合，都会使本局 `status` 变为 `failed`。

Harness 内部方法：

- `start_session`

可注册给 agent 的工具：

- `get_party`
- `get_monsters`
- `get_crafting`
- `get_inventory`
- `get_upgrades`
- `write_memo`
- `craft_equipment`
- `purchase_upgrade`
- `allocate_experience`
- `equip_item`
- `unequip_item`
- `end_turn`
- `get_events`
- `preview_battle`：仅在 preset 的 `llm.expose_battle_preview: true` 时开放。

## 开发约定

- 游戏核心保持纯净，外部调用、页面和自动运行放在 `runtime/`、`api/`、`bench/`。
- 数据尽量放在 YAML 中，代码只负责规则和结算。
- 代码注释和文档注释使用中文。
- 修改核心规则后需要补充或更新测试。

## 许可证

暂未选择许可证。发布到公开 GitHub 前请根据用途添加合适的 `LICENSE` 文件。
