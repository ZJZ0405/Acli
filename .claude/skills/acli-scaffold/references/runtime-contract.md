# 运行时契约（实现者版）

本文件把 `DESIGN.md` 的规则翻译成实现顺序和判定表。冲突时以 `DESIGN.md` 为准。

## 1. 执行管线与校验顺序

严格按以下顺序校验；顺序错误会导致错误的退出码或危险副作用。

1. **语法解析**：未知命令/动词 → `usage`；未知 flag → `unknown_flag`；均退出 2，并给出 did-you-mean。
2. **flag 冲突**：互斥组合 → `conflicting_flags`，退出 2。
3. **flag 适用性**：命令未声明的命令级 flag → `flag_not_applicable`，退出 2。
4. **取值校验**：枚举、整数范围、`--limit` 上限、`--fields` 字段名 → `usage` / `unknown_field`，退出 2。
5. **输出模式解析**：确定生效输出；`--all` 要求 ndjson。
6. **认证前置**：命令需要认证但无凭据 → `unauthenticated`，退出 4。
7. **dry-run 分支**：只生成计划，不执行写操作，不要求确认。
8. **确认门禁**：仅当 `requires_confirmation=true` 且非 dry-run 时，非交互缺 `--yes` → `confirmation_required`，退出 12；此步先于读取 `--from-file -` 等 stdin 负载。
9. **执行**：网络请求、重试、job 创建/等待。
10. **渲染与退出**：用生效输出模式渲染；返回退出码。

关键约束：

- 校验阶段零网络请求。
- 交互提示只发生在第 8 步，且只在允许交互时。
- 解析失败不得产生任何副作用。

## 2. 输出模式解析

```
若同时给出 --json 和 --output，且 --output != json → conflicting_flags
否则若给出 --json → json
否则若给出 --output → 该值
否则若 ACLI_OUTPUT 已设置 → 该值
否则若命令是 schema → json（唯一默认 JSON 例外）
否则 → human
```

- 取值只允许 `human` / `json` / `ndjson`；其他值 `usage`，退出 2。
- `--all` 的生效输出必须是 `ndjson`，否则退出 2。
- stdout 是否 TTY 不参与上述决策，只影响 human 渲染的装饰。

## 3. 信封序列化

成功：

```json
{"type":"result","data":{},"meta":{"schema_version":"1"}}
```

- `data` 必须存在；列表空结果为 `[]`。
- 单资源不包含 `count` / `has_more` / `next_cursor`。
- `meta.schema_version` 必填，字符串。
- `meta.request_id` 有则填；失败时尽量填。
- `meta.dry_run` 在 dry-run 时为 `true`。

错误（只写 stderr）：

```json
{"type":"error","error":{"code":"","message":"","hint":"","retryable":false,"request_id":"","details":{}},"meta":{"schema_version":"1"}}
```

警告（只写 stderr，不改变退出码）：

```json
{"type":"warning","warning":{"code":"","message":"","replaced_by":"","removal_version":""},"meta":{"schema_version":"1"}}
```

- 机器模式下 stderr 不允许裸文本；`--debug` 输出 `type:"debug"` 文档。
- 非流式非零退出：stdout 必须为空（`partial_failure` 例外）。
- `partial_failure`：stdout 输出 `type:"result"` + `meta.partial=true`，stderr 输出汇总错误，退出 10。

## 4. 错误码与退出码映射

实现一个集中函数，禁止在命令内直接决定退出码。

| 错误码 | 退出码 | retryable |
|---|---|---|
| `usage`, `unknown_flag`, `flag_not_applicable`, `unknown_field`, `conflicting_flags`, `invalid_cursor`, `cursor_expired` | 2 | false |
| `unsupported_platform`, `unsupported_server_version`, `internal_error`, `tls_error`, `job_failed` | 1 | false |
| `not_found` | 3 | false |
| `unauthenticated` | 4 | false |
| `permission_denied` | 5 | false |
| `conflict`, `idempotency_key_reuse` | 6 | false |
| `rate_limited` | 7 | true |
| `timeout` | 8 | 视操作 |
| `outcome_unknown` | 8 | false |
| `canceled` | 9 | false |
| `partial_failure` | 10 | false |
| `unavailable`, `network_error` | 11 | true |
| `confirmation_required` | 12 | false |
| `interrupted` | 130 | false |
| `terminated` | 143 | false |

HTTP/传输层映射：

| 情况 | 错误码 |
|---|---|
| 400 参数/校验 | `usage`（或服务端语义化错误码映射到已知码） |
| 401 | `unauthenticated` |
| 403 | `permission_denied` |
| 404 | `not_found` |
| 409 | `conflict` |
| 429 | `rate_limited` + `retry_after_seconds` |
| 5xx | `unavailable` |
| DNS/连接失败 | `network_error` 或 `unavailable` |
| TLS/证书 | `tls_error` |
| 客户端超时 | `timeout`；非幂等写且结果未知 → `outcome_unknown` |

- 新增服务端错误码时，映射到语义最接近的既有码；无法映射才用 `internal_error` 或新增码。
- 未知错误码的读取方会回退到退出码和 `retryable`，所以这两个必须正确。

## 5. 重试策略

| 操作 | 最大重试次数 |
|---|---|
| 读操作（list/get） | 2 次 |
| 按 ID 的 update/delete | 2 次 |
| 带幂等键的 create | 2 次 |
| 其他写操作 | 0 次 |

- 只重试 `retryable=true`。
- 指数退避 + 抖动；总时长不得超过 `--timeout`。
- 429/503 优先遵循 `Retry-After`；若等待会超出剩余预算，立即返回 `rate_limited` / `unavailable` 并带 `retry_after_seconds`。
- 读操作重试不得重复输出已发出的 NDJSON 条目；无法保证时直接失败并报告 `details.emitted_items`。
- 非幂等写超时/断连且结果未知 → `outcome_unknown`，不自动重试。

## 6. NDJSON 写入

- 每个 stdout 行一个完整 JSON 对象。
- 数据帧：`{"type":"item","data":...}`。
- 终结帧：`{"type":"end","meta":{...}}`，`meta` 至少含 `schema_version`；列表含 `count`/`has_more`/`next_cursor`/`truncated`。
- 成功路径必须恰好发送一个 `end`。
- 失败路径不得发送 `end`；错误写 stderr，非零退出。
- `partial_failure` 发送 `meta.partial=true` 的 `end`，退出 10，并在 stderr 输出汇总错误。
- `--max-bytes` 在条目边界停止，`truncated=true`，退出 0。
- EPIPE：停止当前页和 `--all` 后续请求，不发送 `end`，不写错误，退出 0。
  - Go：`signal.Ignore(syscall.SIGPIPE)`，把写错误 `errors.Is(err, syscall.EPIPE)` 视为正常结束。
  - Python：捕获 `BrokenPipeError`，关闭 stdout 前先 `dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())`，退出 0。
- 每行写完立即 flush，避免块缓冲导致 agent 读不到流。

## 7. 人类渲染

- 非 TTY 时关闭 ANSI、进度、分页器；保留相同文本结构和字段。
- 颜色约定：`NO_COLOR` 优先，其次 `FORCE_COLOR` / `CLICOLOR_FORCE`，否则自动检测。
- 表格按 wcwidth 计算列宽；窄于 80 列降级为纵向 `key: value`。
- 状态必须带文字或符号：`x error`、`! warning`、`ok success`。
- 错误三段式：

```
Error: <message>.
Cause: <details/cause>.
Hint:  <可直接执行的命令>.
Request ID: <id>
```

- 列表截断给出可复制的翻页命令。
- `get` 默认摘要，`--full` 显示全部字段。
- `--quiet` 只抑制信息性诊断，不抑制错误、警告、弃用提示和确认。

## 8. 交互门禁

```
允许交互 = stdin 是 TTY
       且未设置 --no-input
       且未设置 ACLI_NO_INPUT=1
```

- `--interactive` 强制允许；与 `--no-input` 冲突，退出 2；没有可用输入通道时报 `usage`，退出 2。
- 提示和答案优先使用 `/dev/tty`；仅当 `/dev/tty` 不可用且 stdin 是 TTY 时回退 stdin。
- 提示写 stderr 或 `/dev/tty`；stdout 永不出现提示。
- 确认硬超时默认 30 秒（`ACLI_PROMPT_TIMEOUT` 可调）；EOF 或超时视为「否」。
- 非交互写操作缺 `--yes`：仅当 `requires_confirmation=true` 且非 dry-run 时立即退出 12，不读取 stdin 负载。
- `--yes` 只能来自 argv；忽略 `ACLI_YES` 和配置中的 `yes`。
- 批量操作先显示总数和样例；`--yes` 执行时向 stderr 输出审计摘要。

确认模板：

```
Delete project 'demo' (prj_123)? This cannot be undone. [y/N]
Use --dry-run to preview.
```

## 9. 认证

- token 来源互斥：`--token-file`、`--token-stdin`、`--token` 任意两个同时出现 → 退出 2。
- 优先级：显式 token 来源 > `ACLI_TOKEN_FILE` > `ACLI_TOKEN` > keyring > 凭据文件。
- 非交互收到 401：退出 4，不自动交互式刷新；hint 指向 `acli auth refresh` 或注入新 token。
- 多页读取中途 401 且当前页尚未输出：同一主体下允许刷新一次并用同一 cursor 重发；当前页已输出条目后不得自动刷新重发。
- keyring 探测超时 2 秒，不触发系统解锁弹窗。
- `auth status --json` 输出状态、主体、过期时间、存储后端，不输出 token。
- 脱敏范围：stdout、stderr、`details`、日志、`--debug`、错误消息。

## 10. 配置

优先级（高到低）：flag > `ACLI_*` > 项目配置 > 用户配置 > 默认。

- 追踪每个生效值的来源；`config list --json` 输出 `value` + `source`。
- 向上发现 `.acli.toml`，在 git worktree 根目录或用户主目录停止。
- 项目配置中发现 `endpoint`、`proxy`、`ca`、`log_file`、自定义 header → 忽略并警告；`--strict-config` 时报错。
- 未知键默认警告并忽略；`--strict-config` 时报错。
- 配置写入使用临时文件 + rename + 锁；不得静默修改。
- 配置中禁止明文 token、密码、Cookie。

## 11. 幂等与 job

幂等：

- `update`/`delete` 按 ID 幂等。
- 首次请求 `not_found` → 退出 3。
- 重试路径上 `not_found` → 视为成功，stderr 警告，`meta.idempotent_replay=true`。
- `create` 使用 `--idempotency-key`；同 key 同请求体返回首次结果；同 key 不同请求体 → `idempotency_key_reuse`，退出 6。

job：

- `--no-wait` 返回 `{job_id, status, created_at}`。
- `job wait`：成功 0；失败原错误码或 `job_failed`（1）；取消 9；等待超时 8（不取消 job）。
- `job cancel` 幂等；对已终结 job 返回明确结果。
- 等待超过 2 秒且 stderr 是 TTY 时输出单行心跳。
- 等待中收到信号：默认只停止等待，打印 job id 和后续命令；`--cancel-on-interrupt` 时取消 job 并退出 130/143。

## 12. 信号与并发

| 信号 | 退出码 | 行为 |
|---|---|---|
| SIGINT | 130 | 停止当前操作；等待 job 时默认不取消远端任务 |
| SIGTERM | 143 | 同上 |
| SIGPIPE（stdout） | 0 | 视为下游正常结束 |
| SIGPIPE（其他 fd） | 141 | 异常管道关闭 |

- 已完整发出的非幂等写请求：最多 `min(剩余 --timeout, 5s)` 宽限期等待确定响应。
- 未收到确定响应 → `interrupted`/`terminated` + `details.outcome_unknown=true`，`retryable=false`。
- 请求尚未发出 → 直接退出 130/143，不标注 `outcome_unknown`。
- 信号处理必须清理临时文件、释放锁；不得留下半写入配置。

## 13. 启动与诊断

- 启动阶段零网络请求、零提示、零更新检查。
- 默认不写日志文件；`--log-file` 才写。
- 日志包含时间、级别、命令、`request_id`；强制脱敏。
- `--debug` 输出请求 ID、重定向链路、重试原因；仍强制脱敏。

## 14. 服务端 API 版本协商

- 业务请求发送 `Acli-Api-Version`。
- 响应必须返回 `server_api_version`。
- `version --json` 声明 `supported_server_api_versions`，并与实际支持区间一致。
- 响应缺失版本或版本超出区间：业务命令以 `unsupported_server_version` 退出 1，`retryable=false`，hint 指向升级 CLI 或更换 endpoint。
- `schema` / `version` / `help` 不依赖服务端版本，仍可用。
- 未知枚举和新增字段按前向兼容降级；未知枚举不得映射为已知值。
