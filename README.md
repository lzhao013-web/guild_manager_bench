# Guild Manager Bench

一个用于评估 LLM 长期规划、资源分配与工具调用能力的回合制公会经营游戏与相关Bench工具。

模型需要在有限回合内招募冒险者、分配经验、打造装备、购买升级并安排战斗，最终根据队伍的终局战斗力获得评分。项目提供人工操作页面、LLM benchmark runner、运行存档、回放和排行榜。

## 核心能力

- YAML 驱动的确定性游戏规则和数据 preset
- 招募、成长、装备、合成、升级与自动战斗系统
- 适配OpenAI-compatible 和 Anthropic 模型
- 面向 LLM 的强类型工具协议与每回合工具预算
- 完整调用链存档、运行续跑、回放与终局评分
- FastAPI 接口、WebSocket 和可视化页面

## 快速开始

环境要求：

- Python 3.11+
- [uv](https://docs.astral.sh/uv/)

安装依赖并运行测试：

```powershell
uv sync --all-groups
uv run pytest tests -q
```

启动可视化服务：

```powershell
uv run guild-manager serve --preset full
```

服务启动后可访问：

- 游戏页面：<http://127.0.0.1:8000/>
- API 文档：<http://127.0.0.1:8000/docs>
- 回放页面：<http://127.0.0.1:8000/replay/>
- 排行榜：<http://127.0.0.1:8000/leaderboard/>

## GitHub Pages 部署

仓库包含 GitHub Pages workflow：`.github/workflows/pages.yml`。当 `main` 分支里的
`web/**` 或部署脚本变化时，会执行：

```powershell
python scripts/build_pages_site.py --output _site
```

生成的静态站点会发布到 GitHub Pages：

- `/`：主操作页面的静态前端
- `/replay/`：回放查看器前端
- `/leaderboard/`：排行榜
- `/assets/`：职业与怪物图标资源

注意：GitHub Pages 只能托管静态文件，不能运行 FastAPI、WebSocket 或 LLM 调用。
因此 `/` 和 `/replay/` 中依赖 `/api`、`/ws` 的实时功能仍需要通过
`uv run guild-manager serve --preset full` 启动本地服务，或另行部署后端服务。

## 运行 LLM Benchmark

在项目根目录创建 `.env`，配置所使用的模型服务：

```dotenv
# OpenAI 或 OpenAI-compatible API
OPENAI_MODEL=your-model-name
OPENAI_API_KEY=your-api-key
OPENAI_BASE_URL=https://api.openai.com/v1
```

运行一局：

```powershell
uv run guild-manager run --preset full
```

使用 Anthropic Messages API：

```dotenv
ANTHROPIC_MODEL=your-model-name
ANTHROPIC_API_KEY=your-api-key
ANTHROPIC_BASE_URL=https://api.anthropic.com
# 可选：启用 adaptive thinking，并设置思考强度
ANTHROPIC_THINKING=true
ANTHROPIC_EFFORT=high
```

```powershell
uv run guild-manager run --provider anthropic --preset full
uv run guild-manager run --provider anthropic --thinking --thinking-effort high --preset full
```

常用选项：

```powershell
# 指定游戏种子与评分用的随机种子,默认为42/2026529
uv run guild-manager run --preset default --game-seed 42 --scoring-seed 20260529

# 从未完成的存档继续运行
uv run guild-manager run --preset default --resume runs/llm/RUN_DIRECTORY

# 查看全部选项
uv run guild-manager run --help
```

每次运行默认存档到 `runs/llm/<timestamp>_<session_id>/`：

- `trace.jsonl`：模型请求、响应、工具调用和工具返回的完整调用链
- `replay.json`：用于恢复、回放和评分的精简运行记录

## 数据 Preset

项目内置两个 preset：

- `default`：8 回合，适合快速调试和比较
- `full`：35 回合，适合完整 benchmark

游戏数据位于 `data/presets/<preset_name>/`，主要包含：

```text
game.yaml               回合、初始资源、LLM 工具和评分规则
adventurers.yaml        冒险者职业与成长
monsters.yaml           怪物数据
monster_tiers.yaml      怪物等级配置
equipment.yaml          装备模板
crafting_recipes.yaml   合成配方
global_upgrades.yaml    全局升级
skills.yaml             技能定义
```

## 回放与排行榜

启动主服务后，可以在 `/replay/` 加载本地 `replay.json`，或浏览 `runs/llm` 下的运行存档。

将待统计的 replay JSON 放入 `web/leaderboard/data/`，然后构建并查看排行榜：

```powershell
uv run guild-manager build-leaderboard
uv run guild-manager serve-leaderboard
```

基准方法评估可在保存汇总结果时，同时为排行榜写出每个 seed 的
`baseline_replay`：

```python
save_eval_results(
    results,
    "results/baselines.json",
    config=config,
    leaderboard_dir="web/leaderboard/data",
)
```

排行榜默认地址为 <http://127.0.0.1:8080/>。

## 项目结构

```text
data/presets/                    游戏数据与规则配置
src/guild_manager_bench/game/    纯游戏核心与结算逻辑
src/guild_manager_bench/runtime/ 会话、观察、事件与回放
src/guild_manager_bench/bench/   LLM runner、评分与排行榜
src/guild_manager_bench/api/     FastAPI 与 WebSocket 接口
web/                             游戏、回放和排行榜页面
tests/                           自动化测试
```

`game/` 不依赖 API、页面或模型服务。修改核心规则时，应同步更新测试。

## 鸣谢

- [Backpacker Guild](https://store.steampowered.com/app/2824000/Backpacker_Guild/) 提供了本 bench 游戏主体的初始灵感——这不是一个成熟、好玩的游戏，但在恰当的时间出现在了我面前。
- [Orion-zhen](https://github.com/Orion-zhen) 与 [stacklands-bench](https://github.com/Orion-zhen/stacklands-bench)🔒——在与 Orion-zhen 合作开发 stacklands-bench 的过程中积攒了很多想法和教训，这些想法很大程度上决定了本 bench 的设计思路。

## License

[AGPL-3.0](LICENSE)
