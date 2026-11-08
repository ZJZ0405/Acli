# 设计自审：红线与警告

在契约定稿前逐条检查。标为「红线」的问题必须在实现前修复。

## 红线

| # | 反模式 | 为什么危险 | 修复 |
|---|---|---|---|
| R1 | 非 TTY 时把 human 静默切换成 JSON | 人类管道行为突变；agent 依赖隐式行为 | 默认 human；机器格式只由显式 flag/env 选择 |
| R2 | 确认提示写 stdout | `\| jq` / `> file` 被污染 | 提示只写 stderr 或 `/dev/tty` |
| R3 | 非交互下等待确认或 stdin | agent 永久挂起 | `requires_confirmation=true` 且非 dry-run 时，无 `--yes` 立即退出 12；提示有硬超时 |
| R4 | `--yes` 来自环境变量或配置文件 | 无法审计的静默绕过 | `--yes` 只接受 argv |
| R5 | 非幂等写操作自动重试 | 重复创建/重复扣费 | 只有 `retryable=true` 且操作语义允许时才重试：读、按 ID update/delete、带幂等键 create 最多 2 次；其他写操作 0 次；结果未知用 `outcome_unknown` |
| R6 | 超时后假装成功或普通失败 | 用户不知道写是否生效 | `outcome_unknown` + `retryable=false` + 核对 hint |
| R7 | 批量部分失败返回 0 | 脚本误判全部成功 | 退出 10 + 分项结果 + 汇总错误 |
| R8 | 错误只有人类文案，没有稳定 `code` | agent 无法分支 | 每个错误有 snake_case `code` 和退出码 |
| R9 | token / Cookie / Authorization 进入输出、日志或 `--debug` | 凭据泄漏 | 默认脱敏；`details` 不含密钥 |
| R10 | 项目 `.acli.toml` 可覆盖 endpoint/proxy/CA/log_file/自定义 header | 恶意仓库劫持流量与凭据 | 这些键只能来自 flag、env、用户配置或显式 `--config`；schema 标记 `project_scope:false` |
| R11 | 为 agent 增加专用命令或隐藏别名 | 两套语义漂移 | 同一命令、同一参数、两种渲染 |
| R12 | 无界列表默认全量输出 | 上下文耗尽、内存和性能问题 | 默认分页；`--all` 需显式且只配 ndjson |
| R13 | 用退出码推断可重试性 | 同一退出码在不同操作上语义不同 | `error.retryable` 是唯一权威 |
| R14 | 确认提示无限等待或忽略 EOF | PTY 场景挂起 | EOF/超时视为否；退出 12 |

## 警告

| # | 反模式 | 影响 | 修复 |
|---|---|---|---|
| W1 | 使用非标准动词（如 `remove`、`show`、`add`） | 命令面不可枚举，agent 难发现 | 映射到 `delete`、`get`、`create` |
| W2 | 写命令没有 `--dry-run` | 无法安全预演 | 所有写命令支持；至少 client 模式 |
| W3 | 缺少 `schema` 或 schema 由手写维护 | 发现面与实现漂移 | 从同一命令清单生成 help 和 schema |
| W4 | 列表 `count` 表示总数、`next_cursor` 可缺失 | 分页分支不可靠 | `count` = 当前页；`next_cursor` 始终存在 |
| W5 | cursor 不绑定认证主体、过滤条件或 `--fields` | 跨账号/跨查询串页 | 绑定 endpoint + 主体 + 命令 + 过滤 + 排序 + `--fields` |
| W6 | 长任务没有 `job cancel` / `job wait` | 无法管理远端任务 | 完整 job 生命周期 |
| W7 | `--no-wait` 返回形状与同步结果混用 | agent 无法区分 | 固定 `{job_id,status,created_at}`，schema 用 `oneOf` |
| W8 | 未知 flag 被静默忽略 | 拼写错误变成危险默认行为 | 输入严格：未知 flag 退出 2 |
| W9 | 输出含本地化文案、颜色或表格边框 | 机器解析失败 | 机器模式纯数据、无 ANSI |
| W10 | 时间格式不统一或带本地时区 | 比较和排序出错 | RFC 3339 UTC，秒精度 |
| W11 | ID 被序列化为数字 | 精度丢失、跨语言解析不一致 | 一律字符串 |
| W12 | 空列表返回 `null` | 调用方需要额外分支 | 返回 `[]` |
| W13 | `--quiet` 抑制错误/警告/确认 | 关键信息丢失 | `--quiet` 只抑制信息性诊断 |
| W14 | 破坏性批量操作没有摘要 | 用户不知道影响范围 | 先显示总数和样例，再确认 |
| W15 | 状态只靠颜色表达 | 不可访问；无色终端丢信息 | 颜色只作冗余，必须带文字/符号 |
| W16 | 启动时检查更新、发网络请求或弹登录提示 | 破坏可预测性和离线场景 | 启动零副作用 |
| W17 | 用隐藏状态（历史、缓存）影响输出 | 结果不可复现 | 显式输入决定输出 |
| W18 | 未知服务端枚举被映射成已知值 | 静默错误语义 | 保留原值或报错，不猜测 |
| W19 | 没有版本协商或 `schema_version` | 无法安全演进 | 声明并校验 `schema_version` / `server_api_version` |
| W20 | `--fields` 会裁掉 `meta` 或分页字段 | agent 丢失分页能力 | 只裁 `data` 顶层字段 |

## 定稿前最后三问

1. 一个不知道任何隐藏约定的 agent，只读 `--help` 和 `schema`，能否完成主流程？
2. 一个非交互脚本在无 TTY、无网络、错误输入下，是否永远不会挂起或误报成功？
3. 人类在 40 列终端、无色环境、管道中，是否仍能读懂结果和下一步？
