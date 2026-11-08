# 契约审计清单

每条按「通过标准」判定。失败级别是默认值；结合上下文可上调，但不得下调 Blocker。

## A. 非交互与阻塞（NB）

| ID | 检查项 | 通过标准 | 失败级别 |
|---|---|---|---|
| NB-1 | 无 TTY 不挂起 | stdin 关闭、stdout 为管道时，所有命令在 `timeout` 内结束 | Blocker |
| NB-2 | PTY 确认超时 | 在 PTY 中运行需确认命令且不输入，提示超时内退出 12 | Blocker |
| NB-3 | `--no-input` 生效 | 任何命令都不读取提示；显式 `--from-file -` / `--token-stdin` 仍可读 | High |
| NB-4 | `--no-input` 与 `--interactive` 冲突 | `conflicting_flags`，退出 2 | Medium |
| NB-5 | keyring 不阻塞 | 不可用时 2 秒内失败，不弹系统解锁 | High |
| NB-6 | 启动零副作用 | 启动阶段无网络、无登录/更新提示 | High |
| NB-7 | 提示通道 | 提示与答案优先 `/dev/tty`；stdin 为管道时不从管道读答案 | Blocker |
| NB-8 | stdin 单一所有权 | `--from-file -`、`--token-stdin`、`--token-file -` 互斥，退出 2 | High |
| NB-9 | 确认先于负载读取 | `requires_confirmation=true` 且非 dry-run、非交互无 `--yes` 时立即退出 12，不读取 stdin payload | Blocker |

## B. 输出与解析（OUT）

| ID | 检查项 | 通过标准 | 失败级别 |
|---|---|---|---|
| OUT-1 | JSON 成功信封 | `type:"result"`、`data`、`meta.schema_version` | High |
| OUT-2 | 错误信封 | `type:"error"`、`error.code`、`retryable`、`meta.schema_version`，写 stderr | High |
| OUT-3 | 非流式 stdout 为空 | 非零退出时 stdout 为空（`partial_failure` 除外） | Blocker |
| OUT-4 | NDJSON 行合法性 | 每行是独立合法 JSON 对象 | High |
| OUT-5 | NDJSON 终结帧 | 成功恰好一个 `end`；硬失败无 `end`；部分失败有 `meta.partial=true` 的 `end`，stderr 有汇总错误，退出 10 | High |
| OUT-6 | stdout/stderr 分离 | stdout 只有数据；进度/警告/错误/提示只在 stderr 或 `/dev/tty` | Blocker |
| OUT-7 | 机器模式 stderr 可解析 | 每条诊断是带 `type` 的 JSON，不混裸文本 | High |
| OUT-8 | `--fields` | 只返回指定顶层字段；未知字段退出 2；`meta` 不可裁 | High |
| OUT-9 | 列表 meta | `count` 是当前页；`has_more` 正确；`next_cursor` 始终存在 | High |
| OUT-10 | 空列表 | `data: []`，不是 `null` | Medium |
| OUT-11 | 时间与 ID | RFC 3339 UTC；ID 为字符串；空值 `null` | Medium |
| OUT-12 | 稳定排序 | 相同输入输出顺序一致，不依赖 locale | Medium |
| OUT-13 | 截断可见 | `next_cursor` 非空或 `meta.truncated=true`，不静默截断 | High |
| OUT-14 | 管道行为稳定 | 非 TTY 不把 human 静默切成 JSON | Blocker |
| OUT-15 | EPIPE | `| head` 时停止后续请求，不写错误，退出 0 | High |
| OUT-16 | `--all` 约束 | 仅与 ndjson 组合；与 cursor/max-bytes/json/human 冲突时报错 | High |

## C. 退出码与错误（ERR）

| ID | 检查项 | 通过标准 | 失败级别 |
|---|---|---|---|
| ERR-1 | 退出码稳定 | 符合契约表；同类失败映射一致 | High |
| ERR-2 | `retryable` 正确 | 按操作语义计算，不只依赖退出码 | High |
| ERR-3 | 非幂等写超时 | `outcome_unknown`，`retryable=false`，不自动重试 | Blocker |
| ERR-4 | 429/503 | 遵循 `Retry-After`；超预算快速失败并带 `retry_after_seconds` | Medium |
| ERR-5 | `job wait` | 失败返回原错误码或 `job_failed`（1），不返回 0 | High |
| ERR-6 | `partial_failure` | 分项结果 + 汇总错误，退出 10 | High |
| ERR-7 | 幂等重放 | 重试路径 delete 的 `not_found` 视为成功并标注 `meta.idempotent_replay` | Medium |
| ERR-8 | 服务端版本 | 超出支持区间 → `unsupported_server_version`，退出 1 | Medium |
| ERR-9 | hint 可执行 | hint 中的命令可直接复制运行 | Low |
| ERR-10 | did-you-mean | 未知命令/flag 给出建议并退出 2 | Low |

## D. 安全与凭据（SEC）

| ID | 检查项 | 通过标准 | 失败级别 |
|---|---|---|---|
| SEC-1 | 输出脱敏 | token/Cookie/Authorization 不出现在 stdout/stderr/details | Blocker |
| SEC-2 | 日志脱敏 | 日志和 `--debug` 中无密钥 | Blocker |
| SEC-3 | `--token` 警告 | TTY 下提示会进入 shell history / 进程列表 | Medium |
| SEC-4 | token 来源互斥 | 任意两个显式 token 来源同时出现 → 退出 2 | High |
| SEC-5 | token 优先级 | 符合显式来源 > `ACLI_TOKEN_FILE` > `ACLI_TOKEN` > keyring > 文件 | Medium |
| SEC-6 | 401/403 区分 | 未认证 4，无权限 5 | High |
| SEC-7 | 401 处理 | 非交互不自动交互式刷新；多页刷新规则正确 | Medium |
| SEC-8 | 项目配置安全 | `.acli.toml` 不能覆盖 endpoint/proxy/CA/log_file/header | Blocker |
| SEC-9 | 配置文件无明文凭据 | 配置中不存 token/password/Cookie | High |
| SEC-10 | `--yes` 来源 | 只来自 argv；`ACLI_YES` 和配置 `yes` 无效 | Blocker |
| SEC-11 | dry-run 无副作用 | 不产生任何写操作；计划包含 `mode` | High |
| SEC-12 | 批量审计 | `--yes` 批量执行时 stderr 有审计摘要；先显示总数和样例 | Medium |
| SEC-13 | 非交互登录 | `auth login` 非交互不启动浏览器、不等待输入，退出 4 | High |
| SEC-14 | auth status 脱敏 | `auth status --json` 输出状态/主体/过期/后端，不输出 token | High |
| SEC-15 | logout 幂等 | 未登录或重复 logout 仍成功，不报未知错误 | Medium |

## E. 人类体验（HUX）

| ID | 检查项 | 通过标准 | 失败级别 |
|---|---|---|---|
| HUX-1 | 无 ANSI 非 TTY | 管道/`NO_COLOR=1` 无转义序列 | Medium |
| HUX-2 | 强制颜色 | `FORCE_COLOR=1` 在 TTY 下生效 | Low |
| HUX-3 | 状态可读 | 不只靠颜色；错误/警告/破坏性状态有文字或符号 | Medium |
| HUX-4 | 窄终端 | 40/80 列下降级为可读布局 | Low |
| HUX-5 | CJK 对齐 | 宽字符表格列对齐 | Low |
| HUX-6 | 进度可控 | `--no-progress`、`TERM=dumb` 下无动画 | Low |
| HUX-7 | `--quiet` 边界 | 不抑制错误、警告、弃用提示、确认 | High |
| HUX-8 | 错误三段式 | Error / Cause / Hint (+ Request ID) | Low |
| HUX-9 | `get` 摘要 | 默认摘要，`--full` 全字段 | Low |
| HUX-10 | 截断提示 | 列表截断时给出可复制的翻页命令 | Low |

## F. 进程与平台（PROC）

| ID | 检查项 | 通过标准 | 失败级别 |
|---|---|---|---|
| PROC-1 | SIGINT | 退出 130，清理临时文件和锁 | High |
| PROC-2 | SIGTERM | 退出 143，清理临时文件和锁 | High |
| PROC-3 | 在途写信号 | 非幂等写有 5 秒宽限；未知结果标注 `outcome_unknown=true` | Blocker |
| PROC-4 | 等待 job 信号 | 默认只停止等待，打印 job id 和后续命令；`--cancel-on-interrupt` 才取消 | Medium |
| PROC-5 | 配置原子写 | 临时文件 + rename + 锁；并发不损坏 | High |
| PROC-6 | token 并发刷新 | 进程间锁，不互相覆盖 | Medium |
| PROC-7 | 平台支持 | 原生 Windows 明确失败；WSL 按 Linux 语义 | Low |
| PROC-8 | 启动无网络 | 启动阶段不连接网络 | High |

## G. 发现与兼容（DISC）

| ID | 检查项 | 通过标准 | 失败级别 |
|---|---|---|---|
| DISC-1 | schema 存在 | 默认 JSON，信封正确 | High |
| DISC-2 | schema 完整 | 含 args/flags/output_schema/error_codes/exit_codes/requires_auth/requires_confirmation/destructive/supports_dry_run/dry_run_modes/supports_jobs/default_wait/examples/stability/deprecated/replaced_by/removal_version；安全敏感键标 `project_scope:false`；整体含全局 flag、环境变量映射、退出码表、错误码表 | High |
| DISC-3 | help/schema 一致 | 命令、flag、示例一致 | High |
| DISC-4 | 示例可执行 | 每个命令至少一个可复制示例 | Medium |
| DISC-5 | 默认索引 | `schema` 默认只输出索引，不一次性倾倒全部 | Medium |
| DISC-6 | 弃用元数据 | deprecated/replaced_by/removal_version/stability | Low |
| DISC-7 | schema diff 门禁 | CI 对已发布 schema 做破坏性 diff 检查 | Medium |
| DISC-8 | 版本协商 | `Acli-Api-Version` / `server_api_version` 校验 | Medium |
| DISC-9 | `version --json` | CLI 版本、commit、平台、schema 版本、服务端支持区间 | Medium |
