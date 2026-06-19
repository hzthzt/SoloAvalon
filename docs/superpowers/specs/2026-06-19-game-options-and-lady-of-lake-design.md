# 多人数开局、可选项开关与湖中仙女设计

## 背景

当前 SoloAvalon 已在规则层保存 5-10 人任务人数和阵营人数常量，提示词配置中也有推荐身份组合、详细身份提示和可选机制说明。但实际建局链路仍固定为 5 人局：API 没有接收人数和开关，`GameService` 固定调用 `create_five_player_game()`，前端开局页固定 4 个 AI，任务人数和投票人数也有 5 人硬编码。

本次需求要求支持更多人数对局，并把相关可选项做成开关。在已确认范围内，除了 5-10 人标准局和 prompt 详细身份提示外，还要完整实现“湖中仙女”的规则流程和前端操作。

湖中仙女规则按常见阿瓦隆扩展实现：开局 token 给首任队长右手边玩家；第 2、3、4 次任务结算后，若对局尚未进入刺杀或结束，持有者查看一名从未持有过湖女 token 的玩家阵营，随后 token 转交给被查看者。规则参考：

- [Dized: Lady of the Lake](https://rules.dized.com/game/rZluqS52QmGdpoVxcmVLtg/wbfNtuNzT0SolAX3IFvcag/lady-of-the-lake)
- [Avalon Online Wiki: Lady of the Lake](https://avalon-game.com/wiki/expansions/lady/)

## 目标

- 开局支持选择 5-10 人局。
- 后端按人数生成推荐身份组合和标准任务配置，仍作为唯一规则裁判。
- 前端 AI 名称和模型覆盖配置随人数动态扩展到 `player_count - 1` 个 AI。
- 可选项以每局开关保存，至少支持：
  - `lady_of_lake`：仅 8-10 人局可开启，完整实现规则、状态、AI 决策、前端操作和事件复盘。
  - `tristan_isolde`：仅 9-10 人局可开启，把崔斯坦/伊索尔德作为成对身份加入角色池。
  - `role_tip_detail`：只影响 AI prompt 中当前身份的详细打法提示。
- AI prompt 只接收当前玩家合法可见信息，包括自己通过湖女查到的阵营，不接收其他玩家的湖女私有结果。
- 历史对局恢复能重放多人数、可选项和湖女事件。

## 非目标

- 不做自由身份编辑器；玩家不能任意选择每个身份数量。
- 不实现多真人或联机房间。
- 不把 prompt 配置文件作为运行时规则配置源；规则使用后端领域层常量。
- 不把湖女查验结果公开给所有玩家。
- 不改变真实模型配置保存方式。

## 方案选择

推荐方案是在后端领域模型中新增通用建局函数和每局选项状态，API 只接收人数和开关，前端只负责提交选择和渲染后端返回状态。这样规则、状态恢复、AI prompt 和日志导出都依赖同一个权威状态。

备选方案是直接读取 `prompt_templates.json` 的 `recommended_role_setups` 和 `optional_mechanics` 作为规则来源。它减少重复配置，但会把 prompt 文案配置变成规则配置，后续容易把模型说明和规则裁判混在一起。

另一个备选方案是做完整自定义房间编辑器。它能力更强，但会引入身份合法性校验、组合推荐、UI 纠错和更多边界分支，超出本次需求。

## 领域模型

新增每局选项：

- `GameOption.LADY_OF_LAKE`
- `GameOption.TRISTAN_ISOLDE`
- `GameOption.ROLE_TIP_DETAIL`

`GameState` 增加：

- `enabled_options: frozenset[GameOption]`
- `lady_of_lake_holder_player_id: str | None`
- `lady_of_lake_previous_holder_ids: tuple[str, ...]`
- `lady_of_lake_inspections: tuple[LadyOfLakeInspection, ...]`

`LadyOfLakeInspection` 包含：

- `viewer_player_id`
- `target_player_id`
- `target_faction`
- `round_number`

`Phase` 新增 `LADY_OF_LAKE`。该阶段只允许当前湖女持有者行动，行动完成后转回下一轮 `TEAM_PROPOSAL`。

`PrivateView` 增加 `lady_of_lake_known_factions: dict[str, Faction]`。只有执行过查验的玩家能在自己的私有视角中看到对应目标阵营。

## 建局规则

新增 `create_game()`，参数包括 `player_count`、`enabled_options`、`seed`、`human_name`、`ai_names`。保留 `create_five_player_game()` 作为兼容封装，默认调用 `create_game(player_count=5)`。

基础身份组合按规则层常量固化：

| 人数 | 好人 | 恶方 |
| --- | --- | --- |
| 5 | Merlin, Percival, Loyal Servant | Assassin, Morgana |
| 6 | Merlin, Percival, Loyal Servant, Loyal Servant | Assassin, Morgana |
| 7 | Merlin, Percival, Loyal Servant, Loyal Servant | Assassin, Morgana, Mordred |
| 8 | Merlin, Percival, Loyal Servant, Loyal Servant, Loyal Servant | Assassin, Morgana, Mordred |
| 9 | Merlin, Percival, Loyal Servant, Loyal Servant, Loyal Servant, Loyal Servant | Assassin, Morgana, Mordred |
| 10 | Merlin, Percival, Loyal Servant, Loyal Servant, Loyal Servant, Loyal Servant | Assassin, Morgana, Mordred, Oberon |

`tristan_isolde` 开关只允许 9-10 人局启用。启用后，角色池中前两个 `Loyal Servant` 替换为 `Tristan` 和 `Isolde`。5-8 人局不允许启用该开关；若请求绕过前端提交，后端返回 400。

`lady_of_lake` 开关只允许 8-10 人局启用，默认关闭。启用时初始持有者为座位最后一位玩家，也就是首任队长右手边玩家；`lady_of_lake_previous_holder_ids` 初始包含该玩家。5-7 人局不允许启用该开关；若请求绕过前端提交，后端返回 400。

## 湖中仙女流程

任务结算仍先由 `finalize_quest()` 判定任务成功、失败、胜负和下一轮。若同时满足以下条件，则进入 `LADY_OF_LAKE` 阶段：

- 本局启用 `lady_of_lake`。
- 已结算任务轮次为第 2、3 或 4 轮。
- 任务结算后对局没有进入 `ASSASSINATION` 或 `COMPLETE`。

湖女阶段的合法目标：

- 不能是当前持有者自己。
- 不能是任何曾持有过湖女 token 的玩家。
- 必须是本局玩家。

执行查验后：

- 后端记录一条 `LadyOfLakeInspection`。
- `lady_of_lake_holder_player_id` 改为目标玩家。
- 目标玩家追加到 `lady_of_lake_previous_holder_ids`。
- 阶段进入下一轮 `TEAM_PROPOSAL`。
- 队长、任务结果、连续拒绝次数等结算状态不被湖女额外修改。

如果任一任务结算后好人已经成功 3 次进入刺杀，或恶方已经失败任务 3 次结束对局，则不再触发湖女阶段。

## 事件与可见性

新增事件 `lady_of_lake_used`：

公开 payload：

```json
{
  "viewer_player_id": "player_7",
  "target_player_id": "player_2",
  "next_holder_player_id": "player_2",
  "round_number": 2
}
```

私有 payload：

```json
{
  "target_faction": "good"
}
```

普通事件列表和实时信息流只展示“谁使用湖女查看了谁”，不展示阵营。日志页在 `include_private=true` 时可以看到私有 payload。状态恢复需要依赖该私有 payload 重建 `lady_of_lake_inspections`，因此不能删除私有事件。

`game_created` 公开 payload 增加 `enabled_options` 和任务配置摘要，便于日志复盘理解对局规则。

## API

`CreateGameRequest` 新增：

- `player_count: int | None`，默认 5，合法范围 5-10。
- `enabled_options: list[str] | None`，未知开关拒绝；`lady_of_lake` 仅接受 8-10 人局，`tristan_isolde` 仅接受 9-10 人局。

`GameService.create_game()` 接收同名参数，创建规则状态后再应用 AI 模型配置。AI 覆盖继续按前端 AI 顺序传入，但后端按随机落座后的非真人玩家顺序绑定，避免与 `player_id` 固定假设耦合。

`GameState` 响应新增：

- `player_count`
- `missions: [{"round_number", "team_size", "fail_cards_required"}]`
- `enabled_options`
- `lady_of_lake_holder_player_id`
- `lady_of_lake_previous_holder_ids`
- `lady_of_lake_eligible_target_ids`
- `lady_of_lake_known_factions`

真人是湖女持有者且阶段为 `lady_of_lake` 时，`next_human_action` 返回 `use_lady_of_lake`。真人提交动作：

```json
{
  "action_type": "use_lady_of_lake",
  "target_player_id": "player_2"
}
```

## AI 与 Prompt

AI 自动推进新增 `lady_of_lake` 分支。若当前持有者是 AI，服务层调用 `AiPlayer.use_lady_of_lake()`，模型只需要选择合法目标并给出私下判断摘要。

`ContextBuilder` 基于 `state.enabled_options` 构建稳定前缀：

- `lady_of_lake` 开启时，系统配置中包含湖女机制说明。
- `tristan_isolde` 开启且角色池实际包含这两个身份时，角色配置中包含相应身份说明。
- `role_tip_detail` 开启时，玩家视角中追加当前身份的详细提示；关闭时不追加。

`PrivateView` 的湖女结果进入当前玩家额外信息，例如“你通过湖中仙女确认：玩家2 为好人阵营”。这段信息只给执行过查验的玩家。

`prompt_templates.json` 新增 `use_lady_of_lake` action prompt 和事件模板。已有 `optional_mechanics` 可继续作为文案来源，但启用与否以每局 `enabled_options` 为准。

## 前端

开局面板新增：

- 人数选择：5-10。
- 可选项开关：湖中仙女、崔斯坦/伊索尔德、详细身份提示。
- `lady_of_lake` 在 5-7 人局禁用；若请求绕过前端提交，后端仍返回 400。
- `tristan_isolde` 在 5-8 人局禁用；若请求绕过前端提交，后端仍返回 400。

AI 配置：

- `aiNames` 和 `aiProfileOverrides` 长度随 `player_count - 1` 调整。
- 创建请求传 `player_count`、`enabled_options`、动态 AI 名称和模型覆盖。

对局渲染：

- `requiredTeamSize` 改从 `game.missions[game.current_round - 1].team_size` 读取。
- 状态栏投票人数从 `game.players.length` 读取。
- 顶部副标题从固定 “Local 5-player Avalon” 改成当前人数。
- 角色标签补齐 Percival、Morgana、Mordred、Oberon、Tristan、Isolde。

湖女操作：

- 当 `next_human_action === "use_lady_of_lake"` 时显示目标下拉。
- 下拉只显示后端返回的 `lady_of_lake_eligible_target_ids` 对应玩家；最终仍以后端校验为准。
- 提交后展示当前真人已知湖女结果，信息仅来自 `lady_of_lake_known_factions`。
- 信息流和日志复盘增加湖女使用记录；普通复盘不显示阵营，私有日志显示阵营。

## 存储与恢复

`games` 表新增 `enabled_options` JSON 文本列，旧数据迁移为空数组。保存新对局时写入每局开关。

恢复状态时：

- 从玩家数量选择对应 `STANDARD_MISSION_CONFIGS`。
- 从 `games.enabled_options` 重建 `GameState.enabled_options`。
- 若启用湖女，按初始规则设置初始持有者和历史持有者。
- 按事件流重放 `team_proposed`、`speech`、`vote_cast`、`vote_result`、`quest_action_submitted`、`quest_result`、`lady_of_lake_used` 和 `assassination`。

旧 5 人对局没有 `enabled_options` 时按空开关恢复，继续兼容。

## 测试

后端最小测试：

- 规则层：5-10 人建局人数、阵营数、任务配置和角色集合正确。
- 规则层：5-7 人启用 `lady_of_lake` 被拒绝，8-10 人可启用。
- 规则层：5-8 人启用 `tristan_isolde` 被拒绝，9-10 人启用后替换两个忠臣。
- 规则层：湖女在第 2、3、4 次任务后进入 `LADY_OF_LAKE`，胜负或刺杀后不触发。
- 规则层：湖女目标不能是自己，不能是曾持有 token 的玩家。
- 规则层：查验结果只进入查看者私有视角。
- 服务层：真人湖女动作、AI 湖女动作、事件 payload 和状态恢复。
- API：`CreateGameRequest` 规范化人数和开关，非法人数与非法开关报错。
- AI：prompt 开关按每局状态生效，未启用的机制不出现在 prompt。

前端验证：

- `npm run build` 通过。
- 开局人数变更后 AI 配置数量同步。
- 任务队伍人数、投票人数和角色标签使用后端返回数据。
- 湖女操作面板能提交目标并显示真人私有结果。

## 兼容性

现有 `create_five_player_game()`、旧 5 人局测试和旧数据库应保持可用。新增 `player_count` 和 `enabled_options` 都有默认值，旧前端请求仍创建 5 人无扩展机制对局。

对外行为变化集中在新建局请求和 `GameState` 响应新增字段。已有字段语义保持不变。
