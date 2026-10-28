# 手动刷新怪物

开发版 `advanced` 支持付费刷新当轮普通怪物。`default`、`full` 默认关闭，冻结 `full v1` 不变。

## preset 配置

基础价格位于 `data/presets/advanced/game.yaml`：

```yaml
rules:
  monster_refresh:
    base_prices:
      - {from_turn: 1, gold: 10}
      - {from_turn: 8, gold: 20}
      - {from_turn: 18, gold: 40}
      - {from_turn: 30, gold: 80}
```

每条从 `from_turn` 开始生效，直到下一条生效。回合必须严格递增且从1开始，金币必须为正整数。省略 `monster_refresh` 或设置空 `base_prices` 列表会关闭功能。

本回合第n次实际刷新费用为 `折后基础价 × 2^(n-1)`。先合并天赋折扣，向上取整，再按次数翻倍。下一回合次数归零，没有额外的每日次数上限。费用只消耗金币，锁定不另收费，也不按被替换位置数量打折。下表未计天赋折扣。

| 回合 | 第1次 | 第2次 | 第3次 |
| --- | ---: | ---: | ---: |
| T1—T7 | 10 | 20 | 40 |
| T8—T17 | 20 | 40 | 80 |
| T18—T29 | 40 | 80 | 160 |
| T30—T34 | 80 | 160 | 320 |

最终整备回合及游戏结束后不可刷新。若preset没有最终整备规则，最后一轮经营仍按正常规则处理。

## LLM 工具

提供两个工具，预览与付费执行分开。两者都只需传 `locked_monster_ids`，使用当前普通怪物的数字编号，空列表表示不保留任何普通目标。

### `preview_monster_refresh(locked_monster_ids)`：仅预览

```json
{"locked_monster_ids": []}
```

- 不扣金币，不推进刷新次数，不改变真实怪池，不写入会话事件。
- 即使当前金币不足，也能查看费用和候选。
- 消耗一次普通工具调用，不消耗 `preview_battle` 的每回合额度。
- 同一回合、同一实刷次数、同一位置的下一候选固定。重复预览不会重抽，改变锁定组合也不会改变其他位置的候选。
- 返回 `simulation_only=true`、费用、是否有足够金币以及候选属性、技能、奖励。
- `refresh:T…:R…:slot…` 仅作预览参考，不能用于锁定、战斗预览或讨伐。预览不会分配真实怪物数字编号。

### `refresh_monsters(locked_monster_ids)`：实际刷新

```json
{"locked_monster_ids": []}
```

实际支付当前费用并提交刷新。工具返回真实怪池、新目标的数字ID和下一次费用。

- 若锁定名单和实刷次数未变，结果与预览一致。
- 锁定目标的身份和属性保持不变，其他普通位置各替换一个目标，总位置数量不变。
- 固定挑战自动保留，无需填入锁定列表，不会重新生成已击败挑战。
- 锁定仅作用于当前回合。它不让普通怪物跨回合保留。
- 至少要替换一个普通位置。全部锁定、重复ID、过期ID、预览引用或金币不足都会失败，不改变真实状态。
- 被替换目标的旧数字ID失效。同一回合不复用旧编号，保留目标的编号不变。
- 不额外提供讨伐名额或HP/MP恢复。每名冒险者、每个真实怪物仍最多出战一次。

刷新预览与实刷各占一次普通工具调用。可以不预览而直接实刷，或看过候选后选择不付费。实际付费前不能跳过当前候选查看再下一次。

真实刷新也是准备动作，可用于 `preview_team_power` 的操作方案试算。方案使用 `refresh_monsters`，参数同真实工具，不允许嵌套 `preview_monster_refresh`。方案返回预计资源与属性变化，终局阶段额外返回正式评分，不返回可操作的虚拟怪池。合法方案可通过 `execute_plan` 提交，遵循[方案工具](tactical-tools.md)的余额门槛和凭据失效规则。刷新费用可能影响方案后续购买，刷新本身不会让经营讨伐奖励提前进入库存。

## 未来预测、存档与回放

手动刷新使用独立随机序列，不改变后续回合的自然怪池、招募候选或 `get_forecast` 结果。未来预测展示的是自然刷新，玩家在未来回合再次手动刷新会改变当轮实际怪池。固定挑战未来是否存在，仍取决于此前是否已击败。

观察中的 `monster_refresh` 包含价格表、当轮已实刷次数、下一次费用及可用状态。存档保存次数和真实怪池，恢复后下一次候选保持一致。实际刷新记作准备事件，LLM步骤状态、断点恢复和回放重建都会重放该动作。刷新前已保存的战斗预览日志继续保留，内容不会更新为新目标。

本功能形成新的开发协议快照，不回写历史运行或成绩。旧运行必须继续使用其原始协议。

## HTTP 接口

- 预览：`POST /api/sessions/{session_id}/preview-monster-refresh`，请求体为 `{"locked_monster_ids": []}`。
- 实刷：`POST /api/sessions/{session_id}/actions`，请求体为 `{"type": "refresh_monsters", "locked_monster_ids": []}`。

HTTP锁定列表使用会话观察中的字符串 `monster_id`，LLM工具使用本轮数字ID。HTTP预览也不会改变真实状态或会话事件。

## 验证

主要回归覆盖 `tests/test_monster_refresh.py`，包括价格边界、preset自定义价格、预览隔离、锁定一致性、失败原子性、过期编号、挑战保留、自然刷新独立性、保存恢复、HTTP接口和回放重建。

```text
uv run python -m pytest tests/test_monster_refresh.py -q --basetemp runs/.pytest-refresh-target
```
