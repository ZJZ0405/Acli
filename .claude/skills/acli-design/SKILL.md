---
name: acli-design
description: Design or revise the command contract for an Acli-friendly CLI before implementation. Use when planning a new CLI, defining resources/commands/flags/output formats, reviewing an existing command surface, or deciding how a tool should behave for both humans and autonomous AI agents (CLI 设计、命令契约、双受众、agent 友好、新建命令行工具、命令面评审).
---

# Acli Design

把需求整理成一份可直接实现的命令契约。只做设计，不写实现；实现交给 `acli-scaffold`。

## 何时使用

- 从零设计一个 CLI。
- 给已有工具增加面向 agent 的使用场景。
- 需要评审命令面、输出、退出码、交互是否「Acli 友好」。

## 规范来源

仓库根目录的 `DESIGN.md` 是权威规范。至少阅读 §1–§4 和 §7。本 skill 的 `references/contract-digest.md` 是工作摘要；两者冲突时以 `DESIGN.md` 为准。

## 输入清单

开始前确认以下信息；缺失项按默认值处理，并把假设写进契约：

1. 资源及生命周期（创建、更新、删除、查询）。
2. 写操作与破坏性操作清单。
3. 认证与权限模型（login/logout/status/refresh、401/403 边界、非交互登录行为）。
4. 长任务（预计 >30s）、批量操作、无界列表。
5. 人类主场景 3 个、agent 主场景 3 个。
6. 实现语言、分发方式、是否已有命令面。

## 设计流程

### 1. 建立命令面

- 使用资源优先结构：`<tool> <resource> <verb>`；标准动词只有 `list/get/create/update/delete`。
- 非资源操作放顶层：`auth`、`schema`、`version`、`config`、`job`、`completion`、`diagnostic`、`help`。
- 不为 agent 增加专用命令或隐藏别名；人类和 agent 共用同一命令。
- 每个命令确定属性：读写、破坏性、认证、确认、dry-run 模式、job、分页、幂等、输出形状。
- 用 `references/command-matrix.md` 的矩阵逐项填写；不确定时采用表中默认值。

### 2. 固定机器契约

- 所有机器输出使用统一信封：`{"type":"result|error|warning", ...}`；`meta.schema_version` 必填且为字符串。
- stdout 只放数据；stderr 放诊断；机器模式下 stderr 的每条诊断也是 JSON 文档。
- 列表必须有 `meta.count`（当前页）、`meta.has_more`、`meta.next_cursor`（最后一页为 `null`，字段始终存在）。
- 字段用 `snake_case`；ID 一律字符串；时间 RFC 3339 UTC；空值显式 `null`；排序稳定。
- 写操作定义：幂等键、`--dry-run=client|server`、同步或 job 返回形状、部分失败语义。
- 错误码与退出码复用 `DESIGN.md` §4.4/§4.5；需要新错误码时新增，不重命名旧码，不改旧映射。
- 明确 `retryable` 的计算规则；非幂等写超时或连接中断且结果未知时用 `outcome_unknown`。

### 3. 固定交互策略

- 非交互永不阻塞：`requires_confirmation=true` 的写操作没有 `--yes` 时立即退出 12，不等待、不读取 stdin 负载；`--dry-run` 不要求确认，且 dry-run 分支先于确认门禁。
- 交互判定 = stdin 是 TTY 且未设置 `--no-input` / `ACLI_NO_INPUT=1`；`--interactive` 可显式允许交互；stdout 是否 TTY 只影响渲染。
- 提示和答案优先走 `/dev/tty`，提示写 stderr；硬超时默认 30 秒；EOF 或超时视为「否」。
- 每个交互能力提供非交互等价物：`--edit` ↔ `--from-file`，确认 ↔ `--yes`。
- 破坏性操作默认确认；`--dry-run` 不要求确认；`--yes` 只能来自 argv。

### 4. 定义发现面

- `--help` 面向人类，`schema` 面向机器，两者由同一份命令清单生成，不能各写一套。
- `schema` 默认 JSON（唯一例外）；默认输出命令索引，按需输出单命令详情。
- 单命令详情至少包含：`path`、`summary`、`args`、`flags`、`output_schema`、`error_codes`、`exit_codes`、`requires_auth`、`requires_confirmation`、`destructive`、`supports_dry_run`、`dry_run_modes`、`supports_jobs`、`default_wait`、`examples`、`stability`、`deprecated`、`replaced_by`、`removal_version`。
- schema 整体还必须包含全局 flag、环境变量映射、退出码表和错误码表。
- 安全敏感配置键（`endpoint`、`proxy`、`ca`、`log_file`、自定义 header）在 schema 中标记 `project_scope: false`。
- 每个命令至少一个可直接复制执行的示例。

### 5. 写契约文档并自审

1. 按 `references/contract-template.md` 输出契约。
2. 逐条运行 `references/red-flags.md`。
3. 有命中项就修改契约，不要留到实现阶段。
4. 无法决定的问题写入「待决问题」，不要用模糊措辞掩盖。

## 硬性决策

| 问题 | 决策 |
|---|---|
| 非 TTY 输出格式 | 保持 human；只有显式 flag/env 才切换 |
| 非交互缺少 `--yes` | 需要确认且非 dry-run 的写操作退出 12，不挂起 |
| 非幂等写超时/断连 | `outcome_unknown`，`retryable=false` |
| 批量部分失败 | 退出 10，不返回 0 |
| 重试依据 | `error.retryable`，不是退出码 |
| 未知输入 | 报错退出 2 |
| 未知输出字段/帧 | 读取方忽略/跳过 |
| 项目配置 | 不得覆盖 endpoint/proxy/CA/log_file/自定义 header |
| `--all` | 只与 ndjson 组合 |
| 长列表 | 默认分页，禁止静默全量 |

## 交付物

一份契约文档，至少包含：范围与非目标、命令矩阵、机器文档模型、错误与退出码、交互与安全、认证与配置、幂等/重试/job、schema 要求、兼容策略、待决问题。不要在本 skill 中写实现代码。

## 参考

- `references/contract-digest.md`：契约速查。
- `references/command-matrix.md`：命令属性与默认决策。
- `references/contract-template.md`：契约输出模板。
- `references/red-flags.md`：设计自审清单。
