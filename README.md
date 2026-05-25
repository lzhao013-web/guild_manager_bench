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
data/                         游戏数据 YAML
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

```python
from guild_manager_bench.bench.llm import GuildManagerTools

tools = GuildManagerTools.from_data_dir("data")
session = tools.start_session()
session_id = session["session_id"]

observation = tools.get_observation(session_id)

tools.craft_equipment(session_id, "iron_sword_recipe")
tools.equip_item(session_id, "vanguard", "eq_0001")
tools.end_turn(
    session_id,
    [{"adventurer_id": "vanguard", "monster_id": "turn_1_monster_1"}],
)
```

可注册给 agent 的工具 schema：

```python
from guild_manager_bench.bench.llm import TurnToolHarness, tool_schemas

schemas = tool_schemas()
harness = TurnToolHarness(tools, session_id, max_tool_calls=20)
result = harness.call_tool("get_observation")
```

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
    data_dir="data",
    config=LlmRunConfig(max_tool_calls_per_turn=20),
)
```

OpenAI-compatible Chat Completions 接口可以直接使用内置适配器：

```python
from guild_manager_bench.bench.llm import OpenAIChatCompletionsAgent, run_llm_game

agent = OpenAIChatCompletionsAgent.from_env(model="your-model-name")
run = run_llm_game(agent, data_dir="data")
```

默认会读取进程环境变量，也会解析项目根目录 `.env`。显式传入的参数或可视化页面中填写的值优先级最高，其次是进程环境变量，最后是 `.env`：

- `OPENAI_API_KEY`
- `OPENAI_BASE_URL`，默认 `https://api.openai.com/v1`
- `OPENAI_MODEL`，也可以通过 `from_env(model=...)` 显式传入
- `OPENAI_COMPAT_API_KEY`
- `OPENAI_COMPAT_BASE_URL`
- `OPENAI_COMPAT_MODEL`

可视化页面的 `LLM` 标签页提供真实模型调试界面，会通过 `/ws/llm/debug` 展示每回合上下文、流式模型输出、工具调用、工具返回和调试事件。

Runner 规则：

- 非法游戏动作会把 `ok=false` 和 `error` 返回给模型。
- 非 `end_turn` 工具调用受 `max_tool_calls_per_turn` 限制。
- 预算耗尽后只允许 `end_turn`。
- 模型连续不调用工具、`end_turn` 连续非法或预算耗尽后仍不结束回合，都会使本局 `status` 变为 `failed`。

Harness 内部方法：

- `start_session`

可注册给 agent 的工具：

- `get_observation`
- `craft_equipment`
- `purchase_upgrade`
- `allocate_experience`
- `equip_item`
- `unequip_item`
- `end_turn`
- `get_events`

## 开发约定

- 游戏核心保持纯净，外部调用、页面和自动运行放在 `runtime/`、`api/`、`bench/`。
- 数据尽量放在 YAML 中，代码只负责规则和结算。
- 代码注释和文档注释使用中文。
- 修改核心规则后需要补充或更新测试。

## 许可证

暂未选择许可证。发布到公开 GitHub 前请根据用途添加合适的 `LICENSE` 文件。
