# 项目约定

尽量使用中文回复。

## 评分与平衡

- 终局强度、职业平衡、配装与技能配置选择、模型比较统一使用正式 `rank_score`。按当前 preset 的评分难度、波次、怪物抽样和全队最佳配对计算，不用单体分换算 rank。
- `ScoreReport.score` / `replay.score.score` 是已弃用的旧 0–100 分 Arena 汇总指标，容易封顶。`per_adventurer[].average_score` 是旧成员均分。它们仅为历史存档兼容保留，不用于调参、排名或前端展示，也不替代缺失的 `rank_score`。
- 原型等权的 100 分单体对位表也不是正式 rank。胜率、损伤和战报可用于诊断，但不能代替正式 rank 判断终局平衡。
- 历史运行只展示已记录的 rank。缺失就显示未知，不用旧分兜底，也不为展示静默重算历史成绩。
- 后续平衡分析从当前配置、实现和原始运行记录重新验证，不沿用旧分析报告、派生统计或搜索结果作为结论。历史运行须先核对其协议及数据版本。

评分入口、指标区别和对比要求见 [docs/scoring.md](docs/scoring.md)。涉及平衡时，先阅读 `.codex/skills/game-balance-tuning/SKILL.md`。
