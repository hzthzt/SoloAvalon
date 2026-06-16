# 文件型模型配置设计

日期：2026-06-16

## 1. 目标

将 SoloAvalon 的模型 API 配置从 SQLite 改为本地明文配置文件。模型配置不再写入数据库，默认写入 `config/llm_profiles.json`。`config/` 必须被 git 忽略，避免真实 API Key 被提交。

保留现有前端和 API 的使用方式：用户仍可在模型配置页面创建、编辑、删除、测试 OpenAI-compatible Chat Completions 配置；公开返回值只展示脱敏后的 API Key。

## 2. 范围

必须实现：

- 新增文件型模型配置仓储，默认路径为仓库根目录下的 `config/llm_profiles.json`。
- 支持用 `SOLOAVALON_LLM_CONFIG` 覆盖配置文件路径，便于测试和多环境本地运行。
- `.gitignore` 增加 `config/`。
- 提供不含密钥的示例配置或 README 示例，说明真实配置应放入被忽略的 `config/` 目录。
- 模型配置 CRUD 不再写入 SQLite。
- SQLite 只保存对局与玩家绑定的 profile id 字符串，不保存 profile 内容，也不再用外键约束这些 id 必须存在于 `llm_profiles`。
- 公开 API 响应仍只包含 `api_key_masked`，不返回完整 `api_key`。
- 更新已有 profile 时，如果请求中的 `api_key` 为空，保留文件中已有密钥。
- `max_tokens` 不再作为模型配置项，也不出现在文件、API 请求或前端表单中。后端如需限制模型输出长度，使用内部默认值或省略该请求字段。
- 模型测试失败时继续脱敏错误信息中的密钥。
- 旧 SQLite schema 中指向 `llm_profiles` 的外键需要迁移移除，避免旧数据库阻止文件配置中的 profile id。

暂不实现：

- 加密本地配置文件。
- 从旧 SQLite 自动迁移真实 API Key 到 JSON 文件。
- 多用户或云端配置同步。

## 3. 推荐方案

采用完整文件型配置方案。整个模型配置对象保存在 JSON 文件中，SQLite 不再保存 `name`、`base_url`、`api_key`、`model`、`temperature` 或 `timeout`。`max_tokens` 不是用户配置项。

配置文件结构：

```json
{
  "profiles": [
    {
      "id": "deepseek",
      "name": "DeepSeek",
      "base_url": "https://api.deepseek.com/v1",
      "api_key": "replace-with-your-local-key",
      "model": "deepseek-chat",
      "temperature": 0.7,
      "timeout": 30.0,
      "created_at": "2026-06-16T00:00:00Z",
      "updated_at": "2026-06-16T00:00:00Z"
    }
  ]
}
```

空文件或不存在的文件表示没有配置。创建第一个 profile 时自动创建父目录和 JSON 文件。

## 4. 数据流

### 4.1 列出配置

`GET /api/llm-profiles` 调用文件型仓储读取 `config/llm_profiles.json`。仓储返回 `LlmProfile` 对象，API 层用 `to_public_dict()` 过滤完整密钥。

### 4.2 创建配置

`POST /api/llm-profiles` 校验 payload 后写入 JSON 文件。写入使用临时文件替换，避免半写入导致配置损坏。

### 4.3 更新配置

`PUT /api/llm-profiles/{profile_id}` 先读取现有 profile。若 `api_key` 为空字符串，API 层沿用现有密钥，再交给仓储更新文件。`created_at` 保持不变，`updated_at` 更新为当前 UTC 时间。

### 4.4 删除配置

`DELETE /api/llm-profiles/{profile_id}` 从 JSON 文件删除对应 profile。已有对局中保存的 profile id 不会被清理；后续 AI 决策解析不到该配置时走现有 fallback。

### 4.5 AI 决策解析

`GameService` 和 `LlmProfileRepository.resolve_profile_for_player()` 继续按玩家覆盖优先于对局默认配置的规则解析 profile id，但最终 profile 内容来自 JSON 文件。配置缺失时抛出 `ValueError`，现有 AI 决策链路捕获失败并回落到 fallback。

## 5. SQLite 变更

新 schema 不再创建 `llm_profiles` 表，也不再在 `games.default_llm_profile_id`、`players.llm_profile_id`、`ai_decisions.llm_profile_id` 上声明指向 `llm_profiles` 的外键。

为了兼容已有本地数据库，初始化数据库时需要检测旧 schema 是否包含这些外键。如果存在，重建受影响表并复制现有数据：

- `games`
- `players`
- `ai_decisions`

迁移后可以删除旧 `llm_profiles` 表，从数据库中清除历史模型配置和 API Key。旧 API Key 不自动搬到 JSON 文件；用户需要重新在模型配置页面或配置文件中填写。

## 6. 文件安全边界

- `config/` 加入 `.gitignore`。
- 真实配置只放在 `config/llm_profiles.json`。
- 示例配置不能包含真实 API Key。
- README 明确说明不要提交真实 `config/` 目录。
- API、前端和错误返回继续只展示脱敏 key。
- 对局日志、导出和 AI 决策记录继续只保存 profile id 和模型名称，不保存完整 API Key。

## 7. 测试策略

先写失败测试，再实现：

- 文件型仓储在文件不存在时返回空列表。
- 创建 profile 会写入 JSON 文件，且 SQLite 中不会出现真实 API Key。
- 列出和读取 profile 能从 JSON 恢复完整内部对象。
- 更新 profile 时空 `api_key` 保留旧密钥。
- 删除 profile 会从 JSON 文件移除。
- 损坏 JSON 返回明确错误，不静默覆盖。
- 旧 SQLite schema 迁移后，保存带 `default_llm_profile_id` 的对局不再需要 `llm_profiles` 表中存在同名记录。
- API CRUD 仍保持公开响应脱敏。
- API 和前端模型配置不再要求或展示 `max_tokens`。

验证命令：

```powershell
.venv\Scripts\python.exe -m unittest tests.storage.test_llm_profile_repository tests.api.test_llm_profiles_api -v
.venv\Scripts\python.exe -m unittest discover -s tests -v
```

## 8. 验收标准

- 新增、编辑、删除模型配置后，真实 API Key 只出现在 `config/llm_profiles.json` 中。
- `soloavalon.sqlite3` 不再新增或保留模型配置内容。
- `config/` 被 git 忽略。
- 现有前端模型配置页面保留新增、编辑、删除、测试交互，但移除 `max_tokens` 输入。
- 用文件中的 profile id 创建新对局可以正常触发 AI 调用。
- 缺失或删除 profile 时游戏仍可 fallback，不因配置问题中断整局。
