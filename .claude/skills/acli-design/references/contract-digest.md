# Acli 契约速查

本文件是 `DESIGN.md` 的工作摘要，用于设计阶段快速查阅。冲突时以仓库根目录 `DESIGN.md` 为准。

## 1. 核心模型

```
argv → parser → validator → planner → executor → Result
                                                 ├── HumanRenderer   (TTY / 人类输出)
                                                 └── MachineRenderer (JSON / NDJSON)
```

- 内核不直接打印；命令实现返回结构化 `Result`。
- 内核不读 TTY 做业务决策；TTY 只影响渲染和交互许可。
- 显式 flag 优先于自动检测；agent 可能运行在 PTY 中。
- 交互提示不能写 stdout。

## 2. 输出模式

- 默认 `--output=human`，即使 stdout 被管道重定向也不静默转 JSON。
- `--output=human|json|ndjson`；`--json` 是 `--output=json` 的简写。
- 唯一例外：`schema` 默认 JSON，`schema --output=human` 输出摘要。
- stdout 只放结果数据；stderr 放进度、警告、错误、提示。
- 机器模式下 stderr 的每条诊断也是带 `type` 的 JSON 文档。

## 3. 机器文档信封

成功：

```json
{
  "type": "result",
  "data": {},
  "meta": { "schema_version": "1" }
}
```

错误：

```json
{
  "type": "error",
  "error": {
    "code": "not_found",
    "message": "project 'prj_999' not found",
    "hint": "Run `acli project list` to see available projects.",
    "retryable": false,
    "request_id": "req_abc123",
    "details": {}
  },
  "meta": { "schema_version": "1" }
}
```

警告：

```json
{
  "type": "warning",
  "warning": {
    "code": "deprecated_flag",
    "message": "flag --old is deprecated",
    "replaced_by": "--new",
    "removal_version": "0.3.0"
  },
  "meta": { "schema_version": "1" }
}
```

规则：

- 成功、错误、警告共享同一 `schema_version` 版本空间；`schema_version` 是字符串。
- `meta.request_id` 可选，失败时建议提供；`meta.dry_run` 在 dry-run 时为 `true`。
- 读取方忽略未知对象字段；未知 `type` 的 stderr 文档可忽略；未知 `error.code` 回退到退出码和 `retryable`。
- 非流式模式下非零退出时 stdout 必须为空；`partial_failure` 是唯一例外。
- 堆栈只在 `--debug` 下出现，且必须脱敏。

## 4. NDJSON

```json
{"type":"item","data":{"id":"prj_123","name":"demo"}}
{"type":"item","data":{"id":"prj_456","name":"test"}}
{"type":"end","meta":{"schema_version":"1","count":2,"has_more":false,"next_cursor":null,"truncated":false}}
```

- 每行一个完整 JSON 对象；`item` 是数据帧，`end` 是终结帧。
- 硬失败或来源中断时不得发送 `end`；批量部分失败例外，见下方 `meta.partial=true` 的 `end`。读取方以缺失 `end` 判断数据不完整。
- 进度、警告、错误只写 stderr。
- stdout 上的 EPIPE（如 `| head`）视为正常结束：停止后续页请求，不写错误，退出 0。
- `--max-bytes` 在条目边界截断，发送 `truncated:true` 的 `end` 帧，退出 0。
- `--all` 只允许与 `ndjson` 组合；`--all` 与 `--cursor`、`--max-bytes` 互斥。
- 批量部分失败发送 `meta.partial=true` 的 `end` 帧，在 stderr 输出汇总错误，退出 10。

## 5. 列表与分页

| 字段 | 语义 |
|---|---|
| `meta.count` | **当前页**条目数，不是匹配总数 |
| `meta.has_more` | 是否还有下一页 |
| `meta.next_cursor` | 下一页游标；最后一页为 `null`；字段始终存在 |
| `meta.truncated` | 是否因 `--max-bytes` 或上限被截断 |
| `meta.omitted_items` | 被截断条目数；未知时为 `null` |
| `meta.partial` | 批量操作是否部分成功 |
| `meta.dry_run` | 是否为 dry-run 结果 |
| `meta.idempotent_replay` | 重试路径上是否按幂等成功处理 |

- 列表默认 `--limit 50`；空列表返回 `data: []`。
- 游标不透明，绑定 endpoint、认证主体、命令、过滤条件、排序和 `--fields`。
- 游标过期返回 `cursor_expired`；不匹配返回 `invalid_cursor`。
- `--fields` 只裁剪 `data` 顶层字段，不能裁掉 `meta`。

## 6. 退出码

| 退出码 | 名称 | 含义 | 默认可重试 |
|---|---|---|---|
| 0 | `success` | 成功 | — |
| 1 | `error` | 未知或内部错误 | 否 |
| 2 | `usage` | 参数/用法错误、冲突 flag、非法 cursor、未知字段 | 否 |
| 3 | `not_found` | 资源不存在 | 否 |
| 4 | `unauthenticated` | 未认证或凭证失效 | 否 |
| 5 | `permission_denied` | 已认证但无权限 | 否 |
| 6 | `conflict` | 已存在、状态冲突、并发冲突、幂等键复用 | 否 |
| 7 | `rate_limited` | 触发限流 | 是 |
| 8 | `timeout` | 操作超时；写操作可能为 `outcome_unknown` | 视操作 |
| 9 | `canceled` | 操作被取消 | 否 |
| 10 | `partial_failure` | 批量操作部分失败 | 否 |
| 11 | `unavailable` | 5xx、连接失败、DNS 失败 | 是 |
| 12 | `confirmation_required` | 非交互模式缺少 `--yes` | 否 |
| 130 | `interrupted` | 被 SIGINT 中断 | 否 |
| 143 | `terminated` | 被 SIGTERM 终止 | 否 |

- `error.retryable` 是重试判断的唯一权威；退出码表只是默认值。
- 不允许用 0 表示「有警告但完成」；警告只写 stderr。
- 非流式 `partial_failure` 输出分项结果和汇总错误，退出 10。
- `job wait`：`succeeded` → 0；`failed` → 原错误码或 1；`canceled` → 9；等待超时 → 8（不取消 job）。
- stdout 被下游关闭 → 0；其他 fd 的 SIGPIPE → 141。

## 7. 错误码

| 错误码 | 退出码 | 默认 retryable |
|---|---|---|
| `usage` / `unknown_flag` / `flag_not_applicable` / `unknown_field` / `conflicting_flags` / `invalid_cursor` / `cursor_expired` | 2 | 否 |
| `unsupported_platform` / `unsupported_server_version` / `internal_error` / `tls_error` / `job_failed` | 1 | 否 |
| `not_found` | 3 | 否 |
| `unauthenticated` | 4 | 否 |
| `permission_denied` | 5 | 否 |
| `conflict` / `idempotency_key_reuse` | 6 | 否 |
| `rate_limited` | 7 | 是 |
| `timeout` | 8 | 视操作 |
| `outcome_unknown` | 8 | 否 |
| `canceled` | 9 | 否 |
| `partial_failure` | 10 | 否 |
| `unavailable` / `network_error` | 11 | 是 |
| `confirmation_required` | 12 | 否 |
| `interrupted` | 130 | 否 |
| `terminated` | 143 | 否 |

错误对象字段：`code`（必填）、`message`（必填）、`hint`、`retryable`（必填）、`request_id`、`retry_after_seconds`、`details`。

## 8. 交互与确认

- 允许交互 = stdin 是 TTY 且未设置 `--no-input` 且未设置 `ACLI_NO_INPUT=1`。
- stdout 是否是 TTY 不影响交互判定。
- `--interactive` 强制允许交互；与 `--no-input` 冲突，退出 2。
- 存在 `/dev/tty` 时，提示和答案都走 `/dev/tty`，不占用 stdin。
- 提示固定写 stderr 或 `/dev/tty`；stdout 永不出现提示。
- 只有 `requires_confirmation=true` 且非 `--dry-run` 的写操作需要确认；`--dry-run` 不要求确认。
- 确认提示硬超时默认 30 秒；答案通道 EOF 或超时一律视为「否」，退出 12。
- 非交互、`requires_confirmation=true`、非 dry-run 且无 `--yes` 时，确认门禁先于显式 stdin 负载消费。
- `--yes` 只跳过确认，不跳过校验、权限检查或 dry-run 语义。
- `--yes` 只能来自 argv；`ACLI_YES` 和配置文件中的 `yes` 无效。
- `--dry-run` 不要求确认。

## 9. 认证

- 命令：`auth login [--no-browser] [--device-code]`、`auth logout`、`auth status [--json]`、`auth refresh`。
- 显式 token 来源 `--token-file` / `--token-stdin` / `--token` 三者互斥。
- 非冲突来源优先级：显式 token 来源 > `ACLI_TOKEN_FILE` > `ACLI_TOKEN` > keyring > 凭据文件。
- 非交互模式收到 401：退出 4，不自动尝试交互式刷新。
- keyring 探测有 2 秒超时；不可用时立即失败，不触发系统解锁弹窗。
- token、Cookie、Authorization header 在所有输出、日志和 `--debug` 中强制脱敏。

## 10. 幂等、重试与长任务

- `list` / `get` 天然幂等。
- `update` / `delete` 按资源 ID 幂等；重试路径上的 `not_found` 视为成功，并设置 `meta.idempotent_replay=true`。
- `create` 支持 `--idempotency-key`；作用域为凭据 + endpoint + 命令 + key；服务端至少保留 24 小时。
- 相同 key + 不同请求体 → `idempotency_key_reuse`，退出 6。
- 只对 `retryable=true` 自动重试。默认重试次数：读操作最多 2 次；按 ID 的 update/delete 最多 2 次；带幂等键的 create 最多 2 次；其他写操作 0 次。
- 非幂等写超时或连接中断且结果未知 → `outcome_unknown`，不自动重试。
- 预计超过 30 秒的操作可以支持 job；命令在 schema 中声明 `supports_jobs` 和 `default_wait`。
- `--no-wait` 返回 `job_id` / `status` / `created_at`；`job get/wait/cancel/list` 提供完整生命周期。
- `--wait` 默认行为由命令的 `default_wait` 决定。

## 11. 配置

优先级（高到低）：flag > `ACLI_*` 环境变量 > 项目 `.acli.toml` > 用户配置 > 内置默认。

- 自动发现：从当前目录向上查找 `.acli.toml`，在 git worktree 根目录或用户主目录停止。
- 未知配置键默认警告并忽略；`--strict-config` 时报错。
- **安全敏感键**（`endpoint`、`proxy`、`ca`、`log_file`、自定义 header）不得来自向上发现的项目配置；这些键在 schema 中标记 `project_scope: false`。
- 配置文件中禁止存储明文 token、密码、Cookie。
- `acli config list --json` 输出生效值及来源。

## 12. 版本与兼容

- CLI 使用 SemVer；`schema_version` 是字符串，当前为 `"1"`。
- 同一 `schema_version` 内只能新增命令、flag、JSON 字段、错误码、NDJSON 帧类型；不得删除、重命名、改类型或改语义。
- 弃用先 stderr 警告，保留至少一个 minor 版本，下一个 major 移除。
- `--quiet` 不得抑制弃用警告。
- schema 为命令、flag、字段、错误码提供 `deprecated`、`replaced_by`、`removal_version`、`stability`。

## 13. 相关章节

- 设计原则：`DESIGN.md` §3
- 命令与 flag：§4.1–§4.2
- 输出、退出码、错误、交互、认证、幂等、schema、分页、配置：§4.3–§4.11
- 行为矩阵：§5
- 验收清单：§7
- 待决问题：§8
