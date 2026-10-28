# 排行榜构建

当前排行榜由 `web/leaderboard/selection.json` 决定收录范围。默认打开 advanced，full 历史数据放在另一张榜。

```powershell
uv run guild-manager build-leaderboard --selection web/leaderboard/selection.json
uv run guild-manager serve-leaderboard
```

独立服务地址是 <http://127.0.0.1:8080/>。主服务中访问 `/leaderboard/`，使用同一份数据。

## 筛选运行

在清单对应榜单的 `sources` 中添加或删除条目，然后重新构建：

```json
{
  "schema_version": 1,
  "default_board": {"preset": "advanced", "version": "current"},
  "boards": [
    {
      "preset": "advanced",
      "version": "current",
      "label": "当前数据",
      "sources": [
        {"replay": "../../runs/llm/<存档目录>/replay.json"}
      ]
    },
    {
      "preset": "full",
      "version": "history",
      "label": "历史数据",
      "sources": [
        {"published": "full_history.json"}
      ]
    }
  ]
}
```

路径相对于清单文件。`replay` 指向一份已完成、有成绩的回放；`published` 导入旧榜已发布的运行明细，只取与该榜 preset 相同的记录。

- 清单是完整输入，不会自动扫描其他目录，也不会从上一次输出补回未选运行。
- 不按 `protocol_status` 筛选或分栏，包括跨版本续跑。发布者自行决定运行是否适合放在同一榜中。
- 原始协议、数据哈希和种子保留在运行详情中。清单里的 `version` 是榜单标识，不会覆盖运行原始的 `benchmark_version`。
- 同一 session 只计一次；若多个来源包含同一 session，清单中靠后的来源优先。
- 同一模型按记录的思考强度分组，未记录的强度不作推断。旧 full 的模型标签保持原样。
- 删除清单条目只影响发布结果，不删除原始存档。输入不存在、未完成或 preset 放错时，构建失败并保留原输出。
- 本次保留的 full 来源是 `full_history.json`，包含原榜97条 full 记录。不修改冻结 full 文件，不重新计算旧成绩。

构建结果写入 `web/leaderboard/leaderboard_data.json`，格式为 schema v3。可用 `--output` 指定其他输出文件。增量解析缓存为输出目录下的 `.selection_cache.json`，`--full-rebuild` 忽略缓存，但仍严格按清单收录。

## Advanced 明细

- 运行标识使用存档目录名，不把所有 `replay.json` 都显示成“replay”。
- 已购天赋按记录的分支分组，展示金币、经验、材料、前置、互斥及效果。这里是终局观测中的节点成本，不冒充历史实际支出。
- 角色详情保留优先配置和停用名单，标记自动回复，印记槽位、材料和技能效果使用中文。状态持续按持有者行动次数解释。
- 经营摘要展示挑战进度、首杀回合与出战者、印记去向，以及刷新预览、实刷和已记录费用。完整事件及其余工具可以展开查看。
- 经营事件只统计已完成的主线回合；调用总数仍覆盖记录中的尝试。旧版单独预览工具和带 `preview` 参数的刷新都会识别。
- 实际支出优先读取官方工具返回的变化段。部分旧回放有调用ID复用造成的中间快照／参数错配，不能据相同余额认定刷新免费。没有明确正文或可靠前后快照时标为未记录，不用节点标价推算。
- 旧full没有的新字段不从当前游戏数据补齐。以上仅为记录展示，不模拟战斗、不重算分数，原有经营汇总沿用既有口径。

## 展开层级

1. 模型详情只显示汇总指标和运行列表。
2. 单次运行默认收起，摘要保留日期、段位积分、累计模型耗时和已有的挑战进度。展开后可直接看战果、经营摘要和队伍贡献。
3. 天赋、经营事件、完整工具调用、累计战绩、运行配置与指纹分别按需展开。天赋分支和工具明细不再继续嵌套。

展开状态只保留在当前页面：排序、筛选、关闭后重开都会保留，切换榜单或刷新页面后重置。每次运行显示自己的配置，不用最新一局的指纹代表所有运行。展开／折叠不请求模型、不重新评分、不修改存档。

## 目录扫描模式

不传 `--selection` 时，沿用原来的目录扫描构建：

```powershell
uv run guild-manager build-leaderboard --data-dir web/leaderboard/data --run-dir runs/<目录> --output runs/leaderboard-preview.json
```

这个模式按 preset 和原始协议版本分组、过滤跨版本运行，并保留该输出中已经发布的历史明细。它不适用于用清单精确增删当前发布榜；`--selection` 不能与 `--data-dir` 或 `--run-dir` 同时使用。
