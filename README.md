# Guild Manager Bench

一个用于评估 LLM 长期规划、资源分配与工具调用能力的回合制公会经营游戏与相关Bench工具。

模型需要在有限回合内招募冒险者、分配经验、打造装备、购买升级并安排战斗，最终根据队伍的终局战斗力获得评分。项目提供人工操作页面、LLM benchmark runner、运行存档、回放和排行榜。

**评分约定：终局平衡和模型比较统一使用正式 `rank_score`。旧 0–100 分 `score` 与成员 `average_score` 已弃用为评价依据，仅保留历史兼容，不用于前端评分展示。** 原型等权的单体分也不能替代 rank，详见 [评分口径](docs/scoring.md)。

开发版 `advanced` 的后期挑战、特殊核心和终极技能规则见 [固定挑战与终极印记](docs/endgame-challenges.md)。T30出现固定挑战，每种职业最多成功通关一个挑战，T35仅用于最终整备。冻结 `full v1` 不受影响。

开发版还支持[手动刷新怪物](docs/monster-refresh.md)：可锁定目标、免费预览下一候选，再支付金币刷新。基础价格配置在preset中，同回合每次实刷翻倍，不影响未来自然刷新。

## 核心能力

- YAML 驱动的确定性游戏规则和数据 preset
- 招募、成长、装备、合成、升级与自动战斗系统
- 适配 OpenAI-compatible Chat Completions、OpenAI Responses API、Anthropic Messages API 和 Google Gemini 原生 API
- 面向 LLM 的强类型工具协议与每回合工具预算
- 完整调用链存档、运行续跑、回放与终局评分
- 多 run 工作台：批次参数组合、独立进程并行、排队、观战、检查点续跑
- API 配置复用、系统凭证库、命令历史导入与模型目录查询
- FastAPI 接口、WebSocket 和可视化页面

## 快速开始

环境要求：

- Python 3.11+
- [uv](https://docs.astral.sh/uv/)
- Node.js 22.20+（构建 Web 工作台，纯 CLI 运行不需要）

安装依赖并运行测试：

```powershell
uv sync --all-groups
uv run pytest tests -q
```

构建工作台并启动可视化服务：

```powershell
npm ci
npm run build
uv run guild-manager serve --preset full
```

服务启动后可访问：

- 运行工作台：<http://127.0.0.1:8000/>
- 人工操作：<http://127.0.0.1:8000/legacy/?mode=manual>
- API 文档：<http://127.0.0.1:8000/docs>
- 回放页面：<http://127.0.0.1:8000/replay/>
- 排行榜：<http://127.0.0.1:8000/leaderboard/>

工作台支持总览、最多四路观战、专注详情、批次与无凭证模板。关页或刷新不停止任务，关闭服务后需手动续跑。使用方法、停止语义和开发命令见 [运行工作台](docs/workbench.md)。常用连接、密钥保存和模型查询见 [API 配置与模型目录](docs/api-profiles.md)。服务仅用于可信本机环境，不应直接暴露到公网。

## 运行 LLM Benchmark

在项目根目录创建 `.env`，配置所使用的模型服务：

```dotenv
# OpenAI 或 OpenAI-compatible API
OPENAI_MODEL=your-model-name
OPENAI_API_KEY=your-api-key
OPENAI_BASE_URL=https://api.openai.com/v1
```

运行一局（默认使用 OpenAI-compatible Chat Completions）：

```powershell
uv run guild-manager run --preset full
```

使用 OpenAI Responses API（调用 `/v1/responses`，支持函数调用、流式事件和 reasoning 配置）：

```powershell
uv run guild-manager run --provider openai-responses --preset full
uv run guild-manager run --provider openai-responses --reasoning-effort high --preset full
```

Responses 适配器优先读取 `OPENAI_RESPONSES_MODEL`、`OPENAI_RESPONSES_API_KEY`、
`OPENAI_RESPONSES_BASE_URL`，未设置时回退到上面的 `OPENAI_*` 变量。
还可使用 `OPENAI_RESPONSES_MAX_OUTPUT_TOKENS` 和
`OPENAI_RESPONSES_REASONING_SUMMARY`。

使用 Anthropic Messages API：

```dotenv
ANTHROPIC_MODEL=your-model-name
ANTHROPIC_API_KEY=your-api-key
ANTHROPIC_BASE_URL=https://api.anthropic.com
# 可选：启用 adaptive thinking，并设置思考强度
ANTHROPIC_THINKING=true
ANTHROPIC_EFFORT=high
# 可选：单次响应输出上限，不设置时为 65536
ANTHROPIC_MAX_TOKENS=65536
```

```powershell
uv run guild-manager run --provider anthropic --preset full
uv run guild-manager run --provider anthropic --thinking --reasoning-effort high --preset full
uv run guild-manager run --provider anthropic --thinking --reasoning-effort high --max-tokens 32768 --preset full
```

使用 Google Gemini 原生 API（支持工具调用、流式响应及原生思考签名回传，无需 Node 或额外 SDK）：

```dotenv
GOOGLE_MODEL=gemini-2.5-flash
GOOGLE_API_KEY=your-api-key
GOOGLE_BASE_URL=https://generativelanguage.googleapis.com/v1beta
# 可选：单次响应输出上限和超时秒数
GOOGLE_MAX_TOKENS=32768
GOOGLE_TIMEOUT=600
```

```powershell
uv run guild-manager run --provider google --preset full
uv run guild-manager run --provider google --model gemini-2.5-flash --no-stream --preset full
```

API Key 也可通过 `GEMINI_API_KEY` 或 `GOOGLE_GENERATIVE_AI_API_KEY` 提供。
配置按显式参数、进程环境变量、`.env` 文件的顺序读取。
`GOOGLE_BASE_URL` 应包含版本路径（默认 `/v1beta`），不要使用 OpenAI-compatible 的 `/openai` 路径。
网页调试页也提供 Google Gemini 选项和快速预设。
Gemini 的输出 Token 统计包含 API 返回的响应 Token 和思考 Token，原生用量保留在响应记录中。

### 统一推理强度

所有 provider 使用 `--reasoning-effort`，网页也使用同一个 `reasoning_effort` 字段。
不设置时沿用 provider 对应的环境配置，均未设置则不下发强度。

| Provider | 可选级别 | API 字段 |
| --- | --- | --- |
| `openai` | `none / minimal / low / medium / high / xhigh / ultra / max` | `reasoning_effort` |
| `openai-responses` | 同上 | `reasoning.effort` |
| `anthropic` | `low / medium / high / max` | `output_config.effort` |
| `google` | `minimal / low / medium / high` | `generationConfig.thinkingConfig.thinkingLevel`，发送大写枚举 |

具体支持的级别仍取决于模型。Google 的等级模式用于 Gemini 3 或更新模型，不自动转换为旧模型的 Token 预算。
Google 还可通过 `GOOGLE_REASONING_EFFORT` 设置，Anthropic 继续读取 `ANTHROPIC_EFFORT` / `ANTHROPIC_REASONING_EFFORT`。
旧的 `--thinking-effort` 保留为兼容别名，不能与 `--reasoning-effort` 同时使用。续跑命令统一输出 `--reasoning-effort`。
`--thinking` / `--no-thinking` 仍只控制 Anthropic adaptive thinking，不与推理强度绑定。

完全使用命令行运行 OpenCode Go 的 Gemini 示例，不需要配置环境变量：

```powershell
uv run guild-manager run --provider google --base-url https://opencode.ai/inference/go/v1 --model gemini-4-argon-contributor --api-key "YOUR_GO_API_KEY" --reasoning-effort high --preset full
```

Go 的模型 ID 与权限以账户实际可用项为准，该网关尚未实连验证。Go 主要面向编码代理，请确认服务规则允许你的评测流量。
明文 API Key 会进入终端历史，注意保管。

`--max-tokens` 对四个 provider 通用（`openai-responses` 映射为 `max_output_tokens`，`google` 映射为 `maxOutputTokens`），
未通过参数或环境变量设置时，Anthropic 使用 65536，其他 provider 不下发该字段。注意 Anthropic 的 `thinking`
与 `tool_use` 共用这一份输出预算：预算过小时思考会挤掉工具调用，表现为
`stop_reason=max_tokens` 的空响应和 `empty_response` 重试，思考强度越高反而越容易丢动作。

常用选项：

```powershell
# 指定游戏种子与评分用的随机种子,默认为42/2026529
uv run guild-manager run --preset default --game-seed 42 --scoring-seed 20260529

# 从未完成的存档继续运行
uv run guild-manager run --preset default --resume runs/llm/RUN_DIRECTORY

# 查看全部选项
uv run guild-manager run --help
```

### 命令行观战与诊断

默认输出是公会经营战报，不需要添加选项：

- 启动面板显示模型、实际生效的随机种子、数据 preset、推理设置和工具预算。
- 交互终端底部显示等待模型、接收输出、执行工具、评分和写入存档的阶段与耗时。历史战报保留在滚动区。
- 经营操作显示角色和装备名称、资源消耗与属性变化。讨伐战报显示实际对阵、胜负和奖励。
- 回合结算显示金币与经验池的净变化，以及评分计算完成后的 Rank Score 和分数变化。
- 最终报告包含阵容、评分贡献、逐回合评分、实际讨伐与终局 Arena 表现、经营收支、模型用量和存档位置。

按需展开信息：

```powershell
# 展开每回合队伍、讨伐目标、工具参数与工具返回
uv run guild-manager run --preset full --verbose

# 展示模型实际返回的正文，不推断或补写决策理由
uv run guild-manager run --show-model-text

# 展示 API 实际提供的推理文本或摘要，未提供时不显示
uv run guild-manager run --show-reasoning

# 展开请求、响应、工具事件与异常堆栈，不逐个打印流式片段
uv run guild-manager run --debug

# 只显示最终报告
uv run guild-manager run --quiet

# stdout 只输出一个 JSON 对象，适合脚本和文件保存
uv run guild-manager run --json > run-summary.json

# 禁用颜色和文字样式，也支持设置 NO_COLOR 环境变量
uv run guild-manager run --no-color
```

`--verbose`、`--debug`、`--quiet` 和 `--json` 互斥。模型文本展示选项不能与
`--quiet` 或 `--json` 同时使用。默认模式和 `--verbose` 都不展示模型正文或推理文本，
除非显式开启。`--debug` 包含完整请求和响应内容，日志可能包含敏感提示词或模型数据，分享前请检查。
输出重定向到文件时自动禁用动画和 ANSI 样式。`--no-stream` 只控制 API 请求方式，不控制展示详细程度。

三档并行批次的监控：

```powershell
# 替换为实际批次目录
$batch = "runs/你的批次目录"

# 三局总览和详情卡片
uv run python -X utf8 scripts/run_gpt61_advanced.py watch "$batch"

# 保留总览，展开 high 的装备、最近5条操作、实战和评分变化
uv run python -X utf8 scripts/run_gpt61_advanced.py watch "$batch" --effort high

# 原有简表，不读取详情日志
uv run python -X utf8 scripts/run_gpt61_advanced.py watch "$batch" --compact

# 静态输出完整详情，适合小窗口滚动查看或重定向到文件
uv run python -X utf8 scripts/run_gpt61_advanced.py watch "$batch" --effort medium --once
```

增强监控显示当前回合、工具预算、最近评分变化、队伍HP/MP与装备概况、材料、最近操作、新工具使用次数、实战奖励和失败重试。默认不展示模型正文、推理或备忘录内容。窄窗口建议使用 `--effort`，被终端高度截断时可用 `--once` 静态输出查看完整内容。`--effort` 与 `--compact` 互斥。

监控只读已有进度文件并增量读取 `trace.jsonl`，不重新调用模型或模拟战斗，不修改工作进程及源码快照。正在运行的批次无需重开，只需重新打开监控。日志尚未读完或暂不可读时会明确标注，已读记录不会重复累计。详情统计以已记录的工具结果为准，包含失败调用，`end_turn` 计入工具总数但不占调用预算。实战的“未胜”可能包括平局，重试通知不包含 provider 内部的HTTP重试。

### 批次曲线比较

```powershell
# 实时比较三局的战力、金币余额和未分配经验池，约每2秒刷新
uv run python -X utf8 scripts/run_gpt61_advanced.py curves "$batch"

# 只展开战力曲线，单次输出便于滚动查看
uv run python -X utf8 scripts/run_gpt61_advanced.py curves "$batch" --metric power --once

# 改为累计讨伐金币、经验收入，避免把已投入培养的资源误读为收益较低
uv run python -X utf8 scripts/run_gpt61_advanced.py curves "$batch" --resource-mode income

# 导出当前三项曲线的SVG，可用浏览器打开并悬停查看每回合数值
uv run python -X utf8 scripts/run_gpt61_advanced.py curves "$batch" --resource-mode income --output "$batch/curves-income.svg"
```

`--metric` 可选 `all`、`power`、`gold` 或 `experience`，默认三项。`--resource-mode balance` 为默认口径，使用每回合 `end_turn` 结算及恢复后的金币余额和未分配经验池。`income` 为累计实际讨伐奖励，含当时已生效的天赋加成，不扣除购买或培养支出，也不把解雇返还的经验计作收入。

横轴按游戏回合对齐，同一指标下所有run共用纵轴。终端以 `m`、`h`、`x` 区分三档，同格交叠显示 `*`。SVG使用颜色区分，悬停实测点可查看准确数值。SVG是导出时的静态快照，`--output` 写完即退出，不持续更新。

曲线只读取正式 `replay.json` 中已完成回合。战力直接使用已记录的 `rank_score`，不重新评分。正在执行的回合及续跑时被回滚的操作不入图，缺失值保持空缺且断线，不补零、不外推。续跑重写历史时可能缺少结算快照，此时使用下一回合准备前的初始快照，两者资源余额相同。两类快照均缺失时余额未知，不用回合中途的最新资源代替。缺少奖励明细或缺少中间回合时，后续累计收入未知。回放暂不可读时保留上次成功读取的曲线并显示警告。查询与导出不修改运行进程、游戏快照或原始存档。

统计口径：

- 工具预算只计算非 `end_turn` 调用，工具调用总数包含 `end_turn`。失败的回合尝试不算已完成回合。
- 游戏中的实际讨伐胜率与终局 Arena 模拟评分胜率分开展示。
- Token 以 API 返回的 usage 为准，并显示提供 usage 的响应数。缺失用量不标成测得的零。
  缓存用量仅在 API 提供时显示，不跨 provider 推算缓存命中率。
- 续跑后的模型、Token 和游戏统计包含已恢复的历史记录。进程耗时与重试通知数只属于本次执行。
  重试通知不包含 provider 内部的 HTTP 重试。
- 金币支出和经验分配量来自操作前后的状态快照。旧存档缺少快照时显示无法统计，不推算支出。

运行失败或按 `Ctrl+C` 中断时，会显示失败阶段和已创建的存档位置。
存在成功写入的存档时，还会提供续跑命令。继续运行时须保留原 API 凭证，
含认证信息的服务地址不会写入续跑命令，需要通过原环境变量重新提供。
成功退出码为 `0`，运行失败为 `1`，命令参数错误为 `2`，中断为 `130`。
`--json` 模式下，运行失败和中断也输出 JSON，不混入人类可读报告。

JSON 包含 `schema_version`、`status`、`score`、`stats`、`final_observation`、
`score_history`、`usage_coverage`、`cache_usage` 和 `invocation` 等字段。
`invocation` 标明本次进程耗时和续跑信息，`stats` 是累计统计。
JSON 不包含完整模型调用链，排查请求细节请使用 `--debug` 或存档中的 `trace.jsonl`。

### 运行存档

每次运行默认存档到 `runs/llm/<timestamp>_<session_id>/`：

- `trace.jsonl`：模型请求、响应、工具调用和工具返回的完整调用链
- `replay.json`：用于恢复、回放和评分的精简运行记录

## 数据 Preset

项目内置三个 preset：

- `default`：8 回合，适合快速调试和比较
- `full`：35 回合，正式 LLM 运行固定使用独立的 `full/v1` 评测包
- `advanced`：35 回合，进阶天赋树。25 个节点消耗金币、材料和公共经验，三种终阶专精互斥，并提供制作折扣和经验投资路线

运行进阶版：

```powershell
uv run guild-manager run --preset advanced --archive-dir runs/advanced
uv run guild-manager serve --preset advanced
```

`advanced` 已接入 T12 起开放、整局共享 6 点额度的付费材料兑换，不新增 preset。规则只写入工具说明，回合摘要只加状态行，不增加系统提示词教程。沿用现有协议指纹区分变更前后的版本，整体平衡仍待验证。规则及限制见[材料兑换设计](docs/material-exchange-design.md)。

完整节点、成本和结算规则见 [Advanced 天赋树](docs/talent-tree-expansion-plan.md)。full 的提示词、工具、引擎、评分器和数据已冻结，advanced 的后续改动不会改变 full 正式运行。新的存档记录协议与配置指纹，排行榜按 preset 与版本分组。缺少协议证据的旧存档标记为 `legacy-*`，不自动归入 `v1`。续跑必须使用相同协议，旧局请使用其原始代码快照。详见 [评测协议与版本](docs/benchmark-protocols.md)。

advanced 还提供按需查询的[战斗记录与未来刷新工具](docs/information-tools.md)，以及[方案试算、执行与技能配置](docs/tactical-tools.md)。开发版 `default`、`advanced` 全回合可试算准备方案，终局前不计算战力。试算扣1次，凭据执行免费，但提交时剩余预算必须不少于方案步数。查询使用普通工具调用额度，不自动把完整日志或未来刷新表加入回合提示词。神官、瘟疫法师与七种印记的法力门槛、行动窗口和默认优先级见[职业培养与印记配置](docs/advanced-combat-builds.md)，仅作用于开发版 `advanced`。

开发版 `default`、`advanced` 的 `write_memo` 维护一份局内持久备忘。未调用时保留原文，每次写入整份覆盖，`content=""` 清空。正文最多 500 字符，回合提示词标注最后修改回合，写入仍占一次工具预算。续跑和分支重跑从已完成的历史前缀恢复备忘，不继承失败尝试中的更新。此变更由协议指纹区分版本，冻结的 `full/v1` 仍沿用原有的一回合备忘。

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

```powershell
# 先编辑 selection.json 选择要发布的运行，再构建。
uv run guild-manager build-leaderboard --selection web/leaderboard/selection.json
uv run guild-manager serve-leaderboard
```

排行榜默认显示 advanced，可切换 full 历史数据。收录由清单决定，不按运行的协议状态过滤；advanced 与 full 不混排。模型按记录的思考强度区分，原始版本信息留在运行详情中，不重算分数。删掉清单中的条目后重建，不会从旧输出或缓存补回该条目，也不会删除原始存档。`--full-rebuild` 可忽略解析缓存。

原来的目录扫描方式仍可用：`build-leaderboard --run-dir <目录> --output <另一个输出文件>`，按 preset 与协议版本分组，并保留该输出中已有的历史明细。两种来源方式不能同时使用。清单格式和筛选方法见 [排行榜构建](docs/leaderboard.md)。

启动主服务后，可以在 `/replay/` 加载本地 `replay.json`，或浏览 `runs/llm` 下的运行存档。

### 行动级对照复盘

运行 `uv run guild-manager serve` 后，点击操作面板或回放页面中的“对照复盘”，
也可直接访问 <http://127.0.0.1:8000/replay/compare.html>。

1. 在 A、B 两侧各选择一份服务端存档，或打开本地 `replay.json`。支持混合选择，本地文件不上传。
2. 按真实回合编号同步查看经营操作、金币与经验池变化、队伍人数、实际讨伐和 Rank Score。
3. 使用“分差扩大最多”“首个行动分歧”“首个失败节点”跳转，或使用回合目录、下拉框和左右方向键导航。
4. 展开操作条目查看原始参数与返回。查询、预览、备忘、模型正文和 API 推理文本另行折叠展示。

对照规则：

- 仅支持 `llm_replay`，不接受 CLI 的统计 JSON、手动回放或基线摘要。
- 同一回合的多次尝试放在一起，主记录优先选最后一次已完成尝试，否则选最后一次未完成记录。
  其他尝试可以展开查看，不重复计入主记录的经营操作统计。
- 行动分歧比较已确认成功的操作类别、对象和数量，以及实际讨伐安排。
  不比较查询顺序，不推断模型动机，也不将评分变化归因于某个操作。
- “分差扩大最多”比较相邻两个真实回合的绝对分差增量，不跨越评分缺失的回合。
- 数据哈希、种子、评分配置、回合数、工具预算或经营目标不同会提示条件差异，并停用分差扩大推荐。
  未记录的条件显示未知，不视为相同。
- 缺少评分、战斗结果或状态快照时保持未知，不填零，不沿用其他回合的分数。
- 对照页只读取记录，不调用模型，不重建存档，也不补算或写回评分。

服务端存档可通过 URL 恢复选择，例如：
`/replay/compare.html?left=RUN_A&right=RUN_B&turn=6`。
本地文件不能通过 URL 传递，刷新后需重新选择。
“打开单局播放器”会在新页面定位到对应回合，单局播放器沿用原有的加载与数据补全行为。

### 模型经营画像

访问 <http://127.0.0.1:8000/replay/profile.html>，选择一份服务端存档或本地 `replay.json` 即可生成画像。
操作面板和单局回放页提供“经营画像”入口。对照复盘页的 A、B 两侧也有“查看经营画像”按钮，
直接分析已加载的数据，本地文件无需再次选择或上传。

画像展示五个方面：

- **培养路线**：各成员获得的分配经验及占比。同名成员按实际 ID 区分，无法识别的对象单列。
- **投资节奏**：招募、制作、公会升级的首次发生回合、已知金币支出，以及按总回合数等分的阶段分布。
- **装备使用**：制作、穿戴、卸下和可确认的替换次数，以及最后已完成回合的未穿戴库存。
- **行动习惯**：查询、预览、经营、结算、备忘等工具调用分布。失败后分别列出相同参数重试、调整参数、改用其他工具或未见后续调用。
- **资源利用**：回合末金币和经验池的记录曲线、已记录峰值，以及连续多个回合末仍有经验余额的区间。

每条摘要和图表都提供回合或操作依据。点击 `T回合 · #操作` 可在当前页面查看对应尝试、
参数、原始返回和状态变化，本地文件也支持。服务端记录还可跳到单局播放器。
独立页面支持 `/replay/profile.html?run=RUN_ID&turn=6`，对照页内的画像支持关闭按钮和 `Esc`。

统计与解释边界：

- 这是**本局画像**，不代表模型在其他种子或参数下的稳定能力，不生成额外能力评分，也不调用模型编写点评。
- 经营投入仅统计各回合最后一次已完成尝试中的成功操作。失败或中断尝试不重复并入经营投入，
  但仍进入行动习惯统计，并保留原始依据。未完成跑局可以查看已有记录，不会伪装成完整跑局。
- 经验按已确认成功调用的金额统计，旧记录缺少金额时仅在连续快照可证明的情况下读取经验池差值。
  金币支出只使用操作前后的连续快照，不用最终余额或目录价格推算。未知金额单独计数。
- 经验占比只使用已知金额。装备归属未知不算未穿戴，首次穿戴不算替换已有装备。
- 投资分段只是展示方式，不是新的游戏阶段。缺失回合或资源快照会中断连续余额区间。
- “失败后调整参数”不等于“问题已解决”，“写入备忘录”不等于“遵守计划”，资源余额也不直接表示浪费。
- 页面只读取记录，不修改存档、游戏状态或评分。

### 排行榜

按 [排行榜构建](docs/leaderboard.md) 编辑收录清单，然后构建并查看：

```powershell
uv run guild-manager build-leaderboard --selection web/leaderboard/selection.json
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

## 前端验证

对照复盘和经营画像的数据投影测试使用 Node.js 22+ 自带的测试运行器：

```powershell
node --test tests/frontend/*.test.mjs
```

浏览器冒烟测试覆盖对照复盘和经营画像，使用构造记录、真实序列化协议和本地测试服务，不访问真实存档或模型 API：

```powershell
uv run --with playwright==1.63.0 python -m playwright install chromium
uv run --with playwright==1.63.0 python tests/frontend/smoke_compare.py
# Windows 已安装 Edge 时，也可直接指定浏览器
uv run --with playwright==1.63.0 python tests/frontend/smoke_compare.py --channel msedge
```

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
