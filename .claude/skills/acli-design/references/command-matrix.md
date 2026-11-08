# 命令矩阵与默认决策

## 1. 命令属性

为每个命令填写以下属性。除 `pagination`、`idempotency`、`output_shape` 是设计期属性外，其余会同时进入 `schema` 和 `--help`，必须与实现一致。

| 属性 | 取值 | 说明 |
|---|---|---|
| `path` | argv 数组，如 `["project","create"]` | 不用点号路径 |
| `summary` | 一句话 | 人类可读，稳定 |
| `kind` | `read` / `write` | 写操作需要确认、dry-run、幂等决策 |
| `destructive` | `true` / `false` | 删除、覆盖、不可逆操作 |
| `requires_auth` | `true` / `false` | `schema` / `version` / `help` 不要求 |
| `requires_confirmation` | `true` / `false` | 默认只对破坏性写操作为 `true` |
| `dry_run_modes` | `[]` / `["client"]` / `["server"]` / `["client","server"]` | 写操作必须有非空值 |
| `supports_jobs` | `true` / `false` | 预计 >30s 或后端异步 |
| `default_wait` | `true` / `false` | 仅 `supports_jobs=true` 时有意义 |
| `pagination` | `none` / `cursor` | 无界列表必须用 cursor |
| `idempotency` | `none` / `by_id` / `key` | `create` 用 `key`，`update`/`delete` 用 `by_id` |
| `output_shape` | `object` / `list` / `sync_or_job` / `plan` | 与 `output_schema` 一致 |

## 2. 标准动词的默认属性

| 动词 | kind | destructive | requires_confirmation | dry_run_modes | pagination | idempotency | jobs |
|---|---|---|---|---|---|---|---|
| `list` | read | false | false | 无 | cursor | none | 通常 false |
| `get` | read | false | false | 无 | none | none | false |
| `create` | write | false | false | client+server（服务端支持时） | none | key | 可选 |
| `update` | write | 视字段 | 视是否破坏性 | client+server | none | by_id | 可选 |
| `delete` | write | true | true | client+server | none | by_id | 可选 |

默认值不是强制要求；偏离时在契约中写明理由。

## 3. flag 适用性

命令必须在 schema 的 `flags` 中列出自己接受的命令级 flag。未列出的命令级 flag 收到时报 `flag_not_applicable`，退出 2。`--yes` 适用于所有 `kind=write` 的命令，与 `requires_confirmation` 无关；只读命令收到时同样报 `flag_not_applicable`。

| flag | list | get | create | update | delete | 说明 |
|---|---|---|---|---|---|---|
| `--limit` | 是 | 否 | 否 | 否 | 否 | 单页数量，默认 50 |
| `--cursor` | 是 | 否 | 否 | 否 | 否 | 不透明分页游标 |
| `--all` | 是 | 否 | 否 | 否 | 否 | 只与 ndjson 组合 |
| `--fields` | 是 | 是 | 否 | 否 | 否 | 顶层字段裁剪 |
| `--max-bytes` | 是 | 否 | 否 | 否 | 否 | 机器输出截断 |
| `--full` | 否 | 是 | 否 | 否 | 否 | 返回全部字段 |
| `--dry-run` | 否 | 否 | 是 | 是 | 是 | 写操作 |
| `--yes` | 否 | 否 | 是 | 是 | 是 | 所有写命令；只来自 argv |
| `--idempotency-key` | 否 | 否 | 是 | 否 | 否 | 创建类操作 |
| `--wait` / `--no-wait` | 否 | 否 | 视 job | 视 job | 视 job | 仅支持 job 的命令 |
| `--from-file` | 否 | 否 | 视输入 | 视输入 | 否 | `--edit` 的非交互等价 |
| `--filter` | 视批量 | 否 | 否 | 否 | 视批量 | 仅批量命令 |
| `--cancel-on-interrupt` | 否 | 否 | 否 | 否 | 否 | 仅等待 job 的命令 |

## 4. 默认决策规则

### 分页

- 列表结果无界或可能超过 100 条 → 必须 `pagination=cursor`。
- 默认 `--limit 50`；最大值写入 schema。
- 空列表返回 `data: []`；`meta.next_cursor` 最后一页为 `null` 但字段存在。

### dry-run

- 所有写命令必须有 `--dry-run`。
- 服务端支持预演 → `dry_run_modes=["client","server"]`，默认 server。
- 服务端不支持 → `["client"]`；显式 `--dry-run=server` 报 `usage`，退出 2。
- 批量 dry-run 先显示总数和前若干个样例。

### 确认

- `destructive=true` → `requires_confirmation=true`。
- 批量销毁、影响多个资源 → 即使单个操作不确认，批量也要摘要确认。
- 只有 `requires_confirmation=true` 且非 `--dry-run` 的命令，在非交互缺少 `--yes` 时才退出 12；`--dry-run` 不要求确认。

### job

- 后端本身异步，或同步 p95 超过 30 秒 → `supports_jobs=true`。
- 命令决定 `default_wait`；显式 `--wait` / `--no-wait` 覆盖。
- `--no-wait` 返回 `{job_id, status, created_at}`。
- 必须同时提供 `job get/wait/cancel/list`。

### 幂等

- `create` → `idempotency=key`；定义 key 作用域和保留期。
- `update` / `delete` → `by_id`；重试路径上的 `not_found` 视为成功并标注 `meta.idempotent_replay`。
- 非幂等写操作不得自动重试；超时且结果未知 → `outcome_unknown`。

## 5. 示例矩阵

| path | kind | destructive | auth | confirm | dry-run | pagination | idempotency | jobs |
|---|---|---|---|---|---|---|---|---|
| `project list` | read | false | true | false | — | cursor | none | false |
| `project get` | read | false | true | false | — | none | none | false |
| `project create` | write | false | true | false | client+server | none | key | false |
| `project update` | write | false | true | false | client+server | none | by_id | false |
| `project delete` | write | true | true | true | client+server | none | by_id | false |
| `project export` | write | false | true | false | client+server | none | key | true |
| `job wait` | read | false | true | false | — | none | none | false |

## 6. 命令面自检

- [ ] 没有 agent 专用命令或隐藏别名。
- [ ] 每个命令只使用标准动词，或能说明为何必须新增动词。
- [ ] 每个交互能力都有非交互等价物。
- [ ] 每个写命令都有 `--dry-run`。
- [ ] 每个破坏性命令都有确认与 `--yes` 门禁。
- [ ] 每个无界列表都有分页。
- [ ] 每个可能超过 30 秒的操作都有 job 或明确的超时行为。
- [ ] 每个命令的 `output_schema` 能区分同步结果与 job 结果。
- [ ] 每个命令至少有一个可执行示例。
