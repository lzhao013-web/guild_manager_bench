# 评分口径

**终局平衡与模型比较只使用正式 `rank_score`。旧 0–100 分指标已弃用为评价依据。**

## 指标区别

| 字段或名称 | 含义 | 用途 |
| --- | --- | --- |
| `ScoreReport.rank_score` / `replay.score.rank_score` | 按 preset 配置的多档难度、每档波次和全队最佳配对计算的正式段位积分 | 终局平衡、配装和技能配置选择、模型比较、前端评分展示 |
| `rank_score_per_adventurer[].rank_score` | 正式全队 rank 的成员贡献 | 解释队伍内的分工，不视为职业脱离队友后的独立强度 |
| `ScoreReport.score` / `replay.score.score` | 旧 0–100 分 Arena 汇总分，使用 `difficulty_factors` 和 `waves`，容易封顶 | **已弃用为评价依据**，只保留字段兼容历史存档，不作前端评分展示 |
| `per_adventurer[].average_score` | 旧 Arena 匹配下的成员平均分，不是成员 rank 贡献 | 同上，不用于调参或比较职业强弱 |
| 原型等权的 100 分单体对位分 | 分析脚本对单体对位的自定义汇总，不保留正式抽样权重和全队配对 | 仅作机制诊断，不能换算为 rank 或作为终局平衡结论 |

单场战斗评分和各难度的中间 Arena 分仍是正式 rank 算法的组成部分。弃用的是把旧汇总分或单体汇总分当作最终指标，不是删除战斗评分算法。

## 正式计算入口

代码位于 `src/guild_manager_bench/bench/metrics.py`：

- 从游戏状态计算：`compute_rank_score` 或 `compute_rank_score_breakdown`。
- 从归档快照计算反事实：`rank_score_from_final_observation` 或 `rank_score_breakdown_from_final_observation`。
- `score_final_state` 同时返回正式 `rank_score` 和兼容旧字段 `score`。比较时明确读取 `rank_score`，不要选错字段。

使用当前 preset 的 `rank_min_diff`、`rank_max_diff`、`rank_step`、`rank_waves`、`wave_size` 和评分种子。保留正式怪物生成权重、按波次解锁、阶级抽样、资源模式、全队最佳配对与舍入。不得用旧 `score` 乘系数或原型等权单体分代替。

## 平衡对比要求

- 固定队友背景、等级、金币与材料上限，检查装备限制和共享核心。每版分别按正式 rank 选择针对配装与精细配置。
- 表格直接列出旧版 rank、新版 rank 和差值，注明这是全队分还是成员贡献。
- 胜率、损伤、法力消耗和战报用于解释变化。它们不是正式终局指标。
- 受控构筑、反事实重算、缩小波次的筛选实验和完整模型运行分开标注。有限搜索不证明最优，离线分差不等于已测得的模型区分度。
- 历史记录缺少 `rank_score` 时显示未知，不回退到旧 `score`，不因展示而静默重算历史成绩。显式重算与反事实分析不写回原运行。
