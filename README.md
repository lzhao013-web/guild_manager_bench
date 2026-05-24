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

## 开发约定

- 游戏核心保持纯净，外部调用、页面和自动运行放在 `runtime/`、`api/`、`bench/`。
- 数据尽量放在 YAML 中，代码只负责规则和结算。
- 代码注释和文档注释使用中文。
- 修改核心规则后需要补充或更新测试。

## 许可证

暂未选择许可证。发布到公开 GitHub 前请根据用途添加合适的 `LICENSE` 文件。

