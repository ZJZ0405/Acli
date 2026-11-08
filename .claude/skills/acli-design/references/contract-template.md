# <Tool> 命令契约

> 状态：草案 / 已定稿
> 版本：<contract-version>
> 对应实现版本：<cli-version>
> 权威规范：`DESIGN.md`（冲突时以该文件为准）

## 1. 范围与非目标

### 1.1 目标

- <目标 1>
- <目标 2>

### 1.2 非目标

- <明确不做的事情>

### 1.3 受众与场景

| 受众 | 场景 | 成功标准 |
|---|---|---|
| 人类 | <场景> | <标准> |
| Agent | <场景> | <标准> |

## 2. 命令面

### 2.1 资源

| 资源 | 说明 | 生命周期 |
|---|---|---|
| `<resource>` | <说明> | create → update → delete |

### 2.2 命令矩阵

| path | kind | destructive | auth | confirm | dry-run | pagination | idempotency | jobs |
|---|---|---|---|---|---|---|---|---|
| `<resource> list` | read | false | true | false | — | cursor | none | false |
| `<resource> get` | read | false | true | false | — | none | none | false |
| `<resource> create` | write | false | true | false | client+server | none | key | false |
| `<resource> update` | write | false | true | false | client+server | none | by_id | false |
| `<resource> delete` | write | true | true | true | client+server | none | by_id | false |

每个命令还必须定义（进入 schema，不是可选项）：

| 项 | 要求 |
|---|---|
| `args` | 位置参数、类型、必填、默认值 |
| `flags` | 命令级 flag 的权威适用清单 |
| `output_schema` | 同步/job 用 `oneOf` 区分 |
| `error_codes` / `exit_codes` | 该命令可能返回的完整集合 |
| `requires_auth` / `requires_confirmation` | 布尔 |
| `destructive` / `supports_dry_run` / `dry_run_modes` | 布尔 + 模式数组 |
| `supports_jobs` / `default_wait` | 布尔 |
| `examples` | 至少一个可直接执行的完整命令 |
| `stability` / `deprecated` / `replaced_by` / `removal_version` | 兼容元数据 |

### 2.3 顶层命令

| path | 用途 | 需要认证 |
|---|---|---|
| `schema` | 机器发现 | false |
| `version` | 版本信息 | false |
| `auth` | 登录/登出/状态/刷新 | 视子命令 |
| `config` | 配置查看/设置 | false |
| `job` | 长任务查询/等待/取消 | true |
| `completion` | shell 补全 | false |
| `diagnostic` | 脱敏诊断包 | false |

## 3. 参数与 flag

### 3.1 全局 flag

沿用 Acli 全局 flag：`--output`、`--json`、`--quiet`、`--verbose`、`--debug`、`--no-color`、`--no-progress`、`--no-pager`、`--no-input`、`--interactive`、`--timeout`、`--config`、`--strict-config`、`--token-file`、`--token-stdin`、`--token`、`--log-file`。

### 3.2 命令级 flag

| 命令 | 额外 flag | 取值/默认 | 说明 |
|---|---|---|---|
| `list` | `--limit` | 默认 50，最大 <max> | 单页数量 |
| `list` | `--cursor` | 不透明字符串 | 翻页 |
| `list` | `--all` | 仅 ndjson | 拉取全部 |
| `create` | `--idempotency-key` | 字符串 | 幂等创建 |
| `create` | `--from-file` | 路径或 `-` | 非交互输入 |
| `delete` | `--filter` | <表达式> | 批量选择 |

冲突规则沿用 `DESIGN.md` §4.2：冲突 flag 一律退出 2。

## 4. 机器契约

### 4.1 信封

成功：

```json
{ "type": "result", "data": {}, "meta": { "schema_version": "1" } }
```

错误：

```json
{
  "type": "error",
  "error": { "code": "", "message": "", "hint": "", "retryable": false, "request_id": "", "details": {} },
  "meta": { "schema_version": "1" }
}
```

### 4.2 资源字段

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `id` | string | 是 | 稳定 ID |
| `name` | string | 是 | 名称 |
| `created_at` | string | 是 | RFC 3339 UTC |
| `updated_at` | string | 否 | RFC 3339 UTC |

字段一律 `snake_case`；空值显式 `null`；ID 为字符串。

### 4.3 列表与分页

- `--limit` 默认 50，最大 <max>。
- `meta.count` 是当前页条目数；`meta.has_more` 表示是否还有下一页。
- `meta.next_cursor` 始终存在，最后一页为 `null`。
- cursor 绑定 endpoint、认证主体、命令、过滤条件、排序和 `--fields`。

### 4.4 错误与退出码

复用的错误码：`usage`、`unknown_flag`、`flag_not_applicable`、`unknown_field`、`conflicting_flags`、`invalid_cursor`、`cursor_expired`、`unsupported_platform`、`unsupported_server_version`、`internal_error`、`tls_error`、`job_failed`、`not_found`、`unauthenticated`、`permission_denied`、`conflict`、`idempotency_key_reuse`、`rate_limited`、`timeout`、`outcome_unknown`、`canceled`、`partial_failure`、`unavailable`、`network_error`、`confirmation_required`、`interrupted`、`terminated`。

新增错误码（如有）：

| 错误码 | 退出码 | retryable | 说明 |
|---|---|---|---|
| `<code>` | `<exit>` | `<bool>` | <说明> |

### 4.5 NDJSON

用于 `list --output=ndjson` 和 `--all`：每行 `item`，成功结束发送 `end`；硬失败或来源中断不发送 `end`；批量部分失败发送 `meta.partial=true` 的 `end`，在 stderr 输出汇总错误，退出 10。EPIPE 退出 0。

## 5. 交互与安全

- 交互判定、`/dev/tty`、提示超时、`--yes` 门禁沿用 `DESIGN.md` §4.6。
- 破坏性命令确认模板：

```
Delete <resource> '<name>' (<id>)? This cannot be undone. [y/N]
Use --dry-run to preview.
```

- 批量操作先显示总数和前若干个样例；`--yes` 执行时输出审计摘要。
- `--dry-run` 不产生副作用，计划对象包含 `mode`。
- 输出默认脱敏 token、密码、Cookie 和密钥。

## 6. 认证与配置

- 认证命令与 token 来源沿用 `DESIGN.md` §4.7。
- 配置优先级：flag > `ACLI_*` > 项目配置 > 用户配置 > 默认。
- 项目配置不得覆盖 `endpoint`、`proxy`、`ca`、`log_file`、自定义 header。
- `config list --json` 显示生效值与来源。

## 7. 长任务与幂等

- 支持 job 的命令：<列表>。
- `--no-wait` 返回 `{job_id, status, created_at}`。
- `job wait` 退出码：成功 0；失败原错误码或 1；取消 9；等待超时 8。
- 创建命令的幂等键作用域：凭据 + endpoint + 命令 + key；保留期 <duration>。
- 非幂等写超时 → `outcome_unknown`，不自动重试。

## 8. 发现面

- `--help` 与 `schema` 由同一命令清单生成。
- `schema` 默认输出索引；`schema <resource> <verb>` 输出详情。
- 每个命令在 schema 中包含：args、flags、output_schema、error_codes、exit_codes、requires_auth、requires_confirmation、destructive、supports_dry_run、dry_run_modes、supports_jobs、default_wait、examples、stability、deprecated、replaced_by、removal_version。
- schema 整体还必须包含全局 flag、环境变量映射、退出码表和错误码表。
- 安全敏感配置键（`endpoint`、`proxy`、`ca`、`log_file`、自定义 header）标记 `project_scope: false`。
- 每个命令至少一个可直接复制执行的示例。

## 9. 兼容与弃用

- `schema_version`：`"1"`。
- 同一版本内只做增量变更；不删除/重命名字段、flag、命令，不改变类型或语义。
- 弃用先 stderr 警告，保留至少一个 minor 版本。
- CI 对已发布 schema 做破坏性 diff 检查。

## 10. 待决问题

| 问题 | 选项 | 影响 | 负责人 |
|---|---|---|---|
| <问题> | <选项> | <影响> | <人> |

## 11. 验收

逐条通过 `DESIGN.md` §7 的检查，并为每个命令补充：

- [ ] 非交互不挂起。
- [ ] `--json` 符合本契约。
- [ ] 错误退出码与 `retryable` 正确。
- [ ] `--dry-run` 无副作用。
- [ ] token 不出现在任何输出中。
- [ ] schema 与 `--help` 一致。
