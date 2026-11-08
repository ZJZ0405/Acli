# 契约测试矩阵

按 ID 实现；不适用的用例写明原因，不要静默跳过。

## E. 信封与退出码

| ID | 场景 | 操作 | 期望退出 | 期望 stdout | 期望 stderr |
|---|---|---|---|---|---|
| E1 | 列表成功 | `list --json` | 0 | `type:"result"`、`data` 为数组、`meta.schema_version` | 空 |
| E2 | 单资源成功 | `get <id> --json` | 0 | `type:"result"`、`data.id` | 空 |
| E3 | 资源不存在 | `get <missing> --json` | 3 | 空 | `type:"error"`、`code:"not_found"` |
| E4 | 未认证 | 清除 token 后读命令 | 4 | 空 | `code:"unauthenticated"` |
| E5 | 无权限 | 测试账号访问受限资源 | 5 | 空 | `code:"permission_denied"` |
| E6 | 未知 flag | `list --bogus` | 2 | 空 | `code:"unknown_flag"`、did-you-mean |
| E7 | 冲突 flag | `--quiet --verbose` | 2 | 空 | `code:"conflicting_flags"` |
| E8 | flag 不适用 | `get <id> --limit 1` | 2 | 空 | `code:"flag_not_applicable"` |
| E9 | 未知字段 | `list --json --fields nope` | 2 | 空 | `code:"unknown_field"` |
| E10 | 幂等键复用 | 同 key 不同请求体 | 6 | 空 | `code:"idempotency_key_reuse"` |
| E11 | 限流 | 假服务器返回 429 + Retry-After | 7 | 空 | `code:"rate_limited"`、`retry_after_seconds` |
| E12 | 超时 | 假服务器延迟超过 `--timeout` | 8 | 空 | `code:"timeout"` |
| E13 | 写结果未知 | 非幂等写断连 | 8 | 空 | `code:"outcome_unknown"`、`retryable:false` |
| E14 | 取消 | 取消操作/job | 9 | 空 | `code:"canceled"` |
| E15 | 部分失败 | 批量操作混合结果 | 10 | `type:"result"`、`meta.partial:true` | 汇总错误 |
| E16 | 服务不可用 | 假服务器 503/连接失败 | 11 | 空 | `code:"unavailable"`、`retryable:true` |
| E17 | 缺确认 | 非交互 delete 无 `--yes` | 12 | 空 | `code:"confirmation_required"` |
| E18 | 内部错误 | 注入未预期错误 | 1 | 空 | `code:"internal_error"` 或已知映射 |
| E19 | 幂等重放（同请求） | 同一 `--idempotency-key` + 相同请求体调用两次 | 0 | 两次返回同一资源，不重复创建 | 空或重放警告 |
| E20 | 非幂等写单次尝试 | 假服务器持续 503，无幂等键的 create | 11 | 空 | `code:"unavailable"`；服务端只收到 1 次写请求 |

## N. 非交互与交互

| ID | 场景 | 操作 | 期望 |
|---|---|---|---|
| N1 | 无 TTY 不挂起 | `list </dev/null` 且 stdout 为管道 | 在超时内结束，非 124 |
| N2 | 非交互缺 `--yes` | `delete <id>`，stdin 关闭 | 快速退出 12，stdout 空 |
| N3 | PTY 确认超时 | PTY 中运行 delete，不输入，`ACLI_PROMPT_TIMEOUT=1s` | 约 1 秒后退出 12 |
| N4 | PTY 回答 no | PTY 中输入 `n` | 退出 12，不执行删除 |
| N5 | PTY 回答 yes | PTY 中输入 `y` | 执行操作，退出 0/对应结果 |
| N6 | `--no-input` | `--no-input` 运行确认命令 | 不提示，退出 12 或按要求失败 |
| N7 | `--no-input` + 显式输入 | `--no-input --from-file -` | 正常读取 stdin 数据 |
| N8 | `--interactive` 无通道 | stdin 为管道且无 `/dev/tty` | `usage`，退出 2 |
| N9 | stdin 单一所有权 | `--from-file - --token-stdin` | `conflicting_flags`，退出 2 |
| N10 | 环境绕过确认 | `ACLI_YES=1 delete <id>` | 仍退出 12 |
| N11 | 确认先于负载 | 非交互 delete 且 stdin 有大量数据 | 不读取 payload，立即退出 12 |
| N12 | keyring 不可用 | 禁用 keyring 后运行需认证命令 | 2 秒内失败，退出 4，不弹窗 |

## S. NDJSON 与流式

| ID | 场景 | 操作 | 期望 |
|---|---|---|---|
| S1 | 成功帧序 | `list --output=ndjson` | 若干 `item` + 恰好一个 `end` |
| S2 | 每行合法 | 同上 | 每行可被 `json.loads` |
| S3 | 失败无 `end` | 非法 cursor / 假服务器失败 | 非零退出，stdout 无 `end` |
| S4 | 流中途断连 | 假服务器返回 2 条后断开 | 无 `end`，stderr 错误，非零退出 |
| S5 | 部分失败 | 批量流中部分失败 | `end` 含 `meta.partial:true`，stderr 有汇总错误，退出 10 |
| S6 | `--max-bytes` | 小上限 | 条目边界截断，`end.meta.truncated:true`，退出 0 |
| S7 | EPIPE | `list --all --output=ndjson \| head -1` | CLI 退出 0，无 broken pipe 错误 |
| S8 | `--all` 约束 | `--all --output=json` / `--all --cursor x` | 退出 2，`conflicting_flags` |
| S9 | 流不污染 | 同上 | stdout 无进度/警告/错误文本 |
| S10 | 非幂等写重试 | 非幂等 create 遇到 503 | 不自动重试；服务端只收到 1 次写请求 |
| S11 | 读重试不重复 | 流式读取中途 503，重试 | 不重复输出已发出的 `item`；无法保证时失败并给出 `details.emitted_items` |

## P. 分页与字段

| ID | 场景 | 操作 | 期望 |
|---|---|---|---|
| P1 | meta 字段 | `list --json` | `count`、`has_more`、`next_cursor`、`schema_version` 存在 |
| P2 | count 语义 | `--limit 2`，总数据 >2 | `count <= 2`，不是总数 |
| P3 | 最后一页 | 翻到最后一页 | `next_cursor: null`，`has_more: false` |
| P4 | 空列表 | 空资源 | `data: []`，不是 `null` |
| P5 | cursor 绑定 | 用同一 cursor 改 `--fields`/过滤 | `invalid_cursor`，退出 2 |
| P6 | cursor 过期 | 使用过期 cursor | `cursor_expired`，退出 2 |
| P7 | 字段裁剪 | `--fields id,name` | `data` 每项恰好这些字段 |
| P8 | meta 不可裁 | `--fields id` | `meta` 与分页字段仍存在 |
| P9 | 未知字段 | `--fields nope` | 退出 2 |
| P10 | 稳定排序 | 同输入跑两次 | 顺序一致 |
| P11 | cursor 绑定主体/命令/过滤 | 用账号 A 的 cursor 在账号 B 或不同过滤条件下使用 | `invalid_cursor`，退出 2 |
| P12 | cursor 绑定 `--fields` | 同一 cursor 改变 `--fields` | `invalid_cursor`，退出 2 |

## SEC. 安全

| ID | 场景 | 操作 | 期望 |
|---|---|---|---|
| SEC1 | token 脱敏 | 用哨兵 token 跑 `--json --debug` | stdout/stderr 均不含哨兵 |
| SEC2 | `--token` 警告 | TTY 下使用 `--token` | stderr 有泄漏警告 |
| SEC3 | token 来源互斥 | 同时给两个来源 | 退出 2 |
| SEC4 | 项目配置安全 | 临时目录 `.acli.toml` 设 endpoint | 被忽略并警告，来源不是 project |
| SEC5 | 配置无凭据 | 配置写 token | 拒绝或忽略并警告 |
| SEC6 | dry-run 无副作用 | create/delete `--dry-run` | 资源状态不变 |
| SEC7 | dry-run 计划 | `--dry-run --json` | `data.plan.mode`、`meta.dry_run:true` |
| SEC8 | 批量审计 | 批量 `--yes` | stderr 有审计摘要 |
| SEC9 | 401/403 | 未认证 vs 无权限 | 退出 4 vs 5 |
| SEC10 | 错误 details | 触发错误 | 不含 token/Cookie/Authorization |

## SIG. 信号与进程

| ID | 场景 | 操作 | 期望 |
|---|---|---|---|
| SIG1 | SIGINT | 向运行中进程发 SIGINT | 退出 130，临时文件/锁清理 |
| SIG2 | SIGTERM | 发 SIGTERM | 退出 143 |
| SIG3 | 在途非幂等写 | 请求发出后发信号，服务器不响应 | 130/143，`details.outcome_unknown:true`，`retryable:false` |
| SIG4 | 等待 job 信号 | `job wait` 中 SIGINT | 只停止等待，打印 job id，job 未取消 |
| SIG5 | 取消 job | `--cancel-on-interrupt` + SIGINT | 取消远端 job，退出 130 |
| SIG6 | 非 stdout SIGPIPE | 关闭其他 fd 的管道 | 退出 141 |

## D. 发现与兼容

| ID | 场景 | 操作 | 期望 |
|---|---|---|---|
| D1 | schema 默认 | `schema` 不带 `--output` | stdout 是 JSON 信封 |
| D2 | 默认索引 | `schema` | `data` 是命令索引，不含全部详情 |
| D3 | 详情字段 | `schema <resource> <verb>` | 含 args/flags/output_schema/error_codes/exit_codes |
| D4 | help 一致 | 遍历 schema path 执行 `--help` | 全部成功，flag/示例一致 |
| D5 | version | `version --json` | cli_version/commit/platform/schema 版本 |
| D6 | 未知命令 | `acli bogus` | 退出 2，did-you-mean |
| D7 | 弃用警告 | 使用弃用 flag，加 `--quiet` | stderr 仍有警告 |
| D8 | 服务端版本 | 假服务器返回不支持版本 | `unsupported_server_version`，退出 1 |

## AUTH. 认证

| ID | 场景 | 操作 | 期望 |
|---|---|---|---|
| A1 | 非交互 login | `auth login` 且 stdin 非 TTY | 退出 4，不启动浏览器、不等待 |
| A2 | 登录降级 | `--device-code` / `--no-browser` | 按流程成功或明确失败，不挂起 |
| A3 | status 脱敏 | `auth status --json` | 状态/主体/过期/存储后端，无 token |
| A4 | logout 幂等 | 连续两次 logout | 均成功，不报未知错误 |
| A5 | keyring 不可用 | 禁用 keyring | 2 秒内退出 4，不弹系统解锁 |
| A6 | 非交互 401 | 假服务器返回 401 | 退出 4，不自动交互刷新 |
| A7 | 多页 401 | 当前页未输出时允许刷新一次；已输出则不刷新重发 | 符合 `DESIGN.md` §4.7 |

## PROC. 启动、配置与平台

| ID | 场景 | 操作 | 期望 |
|---|---|---|---|
| PR1 | 启动零网络 | 代理指向死地址运行 `version` | 快速成功，不受代理影响 |
| PR2 | 配置优先级 | flag / env / project / user 同时设置 | 高优先级胜出 |
| PR3 | 配置来源 | `config list --json` | 每个生效值带来源 |
| PR4 | 项目配置安全键 | 项目 `.acli.toml` 设 endpoint/proxy/CA/header/log_file | 忽略并警告，来源不是 project |
| PR5 | 原生 Windows | Windows 运行 `version` | 退出 1，`unsupported_platform` |
| PR6 | 配置原子写 | 并发 `config set` | 文件不损坏，冲突有明确错误 |

## H. 人类输出

| ID | 场景 | 操作 | 期望 |
|---|---|---|---|
| H1 | 非 TTY 无色 | 管道/`NO_COLOR=1` | 无 ANSI |
| H2 | FORCE_COLOR | TTY + `FORCE_COLOR=1` | 有颜色 |
| H3 | 窄终端 | `COLUMNS=40` | 降级为可读布局，不截断 ID |
| H4 | CJK 对齐 | 含 CJK 名称的列表 | 列对齐 |
| H5 | 错误三段式 | 触发错误 | Error/Cause/Hint 可读，hint 可执行 |
| H6 | quiet 边界 | `--quiet` + 错误/警告 | 错误和警告不被抑制 |
| H7 | get 摘要 | `get` vs `get --full` | 默认摘要，`--full` 全字段 |
| H8 | 管道保持 human | `list \| head` | 仍是人类文本，不静默切 JSON |

## 实现顺序

1. E1–E3、N1–N3、S1、S3、S7、P1–P4、SEC1、SEC6。
2. 再补 E4–E20、N4–N11、S4–S11、P5–P12、AUTH、PROC。
3. 最后补 SIG、D、H 和故障注入。
4. 矩阵中未实现的项在测试报告中列出。
