# Acli 设计原则与命令契约（草案 v0.4）

> 状态：草案，未定稿。
> 目标：定义 Acli 作为「人类与 AI agent 共用」的 CLI 中不可妥协的约定。
> 适用范围：所有子命令、所有输出模式、所有版本。

**修订记录**

- v0.1：初稿。
- v0.2：合并 agent 契约、人类工效、完整性三路审查。重点修复：PTY 下确认挂起、错误信封与 `schema` 不一致、超时重试导致重复创建、job/分页/schema 契约缺口、认证生命周期、可访问性、平台与信号语义。
- v0.3：合并先例、极端场景、内部一致性审查。重点修复：项目配置不得覆盖 endpoint/proxy/CA；NDJSON 失败与空 stdout 规则；SIGTERM 命中在途写请求；cursor 绑定认证主体；stdin 单一所有权；dry-run client/server 模式；全局/命令级 flag 拆分；`schema_version` 类型统一；重试删除的 `not_found` 语义；服务端 API 版本协商。
- v0.4：明确 `--help` / `schema` 是唯一必需的自描述层；外部文档、skill 和插件市场是可选任务层，冲突时以自描述为准。

**快速阅读**

- 设计原则：§3
- 命令与输出契约：§4
- 人类/agent 行为矩阵：§5
- 发布前验收清单：§7
- 尚未定稿的决策：§8

---

## 0. 一句话

Acli 只有一套命令语义；人类模式和机器模式只是同一结果的两种渲染。

Agent 模式不是「加一个 `--json`」，而是一份行为契约：

> 可预测 + 不阻塞 + 可解析 + 可重试 + 可发现。

---

## 1. 目标与非目标

### 1.1 目标

1. **同一内核，两类消费者**：人类和 agent 使用完全相同的命令、参数和语义，不存在「agent 专用命令」。
2. **人类默认好用**：开箱有彩色输出、表格、进度、确认提示和可执行的错误建议。
3. **agent 默认可编程**：非交互不阻塞、输出可解析、失败可分类、执行可重试、能力可发现。
4. **行为可复现**：相同输入产生相同输出；不依赖隐藏状态、随机顺序或隐式时间。
5. **长期可依赖**：命令、JSON 字段、错误码和退出码有版本与弃用契约。

### 1.2 非目标

- 不做 GUI；v1 也不做全屏 TUI。
- 不为 agent 维护第二套命令或第二套语义。
- 不自动猜测用户意图，不静默修改全局状态。
- 不默认上传遥测，不自动更新，不在命令启动时检查更新。
- 不追求覆盖所有交互场景；每个交互能力必须有一个等价的非交互 flag。
- v1 只支持 Linux / macOS（含 WSL）；原生 Windows 不在 v1 范围内，检测到原生 Windows 时以明确错误退出。

---

## 2. 核心模型：一个内核，两个渲染层

```
argv
  ↓
parser → validator → planner → executor → Result (结构化结果)
                                              ├── HumanRenderer   (TTY)
                                              └── MachineRenderer (JSON / NDJSON)
```

架构上的硬性要求：

- **内核不直接打印**。所有输出必须经过渲染层；命令实现返回结构化 `Result`，而不是 `print()`。
- **内核不读 TTY 做业务决策**。TTY 只影响渲染和「是否允许交互」，不影响命令语义、数据结构或退出码。
- **显式 flag 优先于自动检测**。自动检测只是默认值，不是契约。
- **agent 可能运行在 PTY 中**（tmux、CI 伪终端、agent harness），因此禁止只靠 `isatty()` 判断「对面是不是人」。所有可能等待输入的地方都必须有硬超时，或要求显式 opt-in。
- **交互提示不能写 stdout**。提示写 stderr；如果存在 `/dev/tty`，优先写 `/dev/tty`。

---

## 3. 设计原则

### P1 机器契约优先

Agent 看不到屏幕，也不会读 README。它能依赖的只有：命令与 flag、stdout/stderr 的字节、退出码、`--help` / `schema` 的自描述。

因此，**机器可读输出是正式契约，不是调试副产品**。字段名、类型、空值语义、排序、错误码一旦发布就按兼容性规则管理。

要求：

- JSON 输出稳定、有 schema、有版本；成功与错误共用同一套 machine document 模型（见 §4.3.2）。
- 字段使用 `snake_case`；ID 一律字符串；时间一律 RFC 3339 UTC；空值显式为 `null`。
- 机器输出中不出现本地化文案、颜色、表格边框和进度动画。
- 错误信息同时提供机器码和人类可读文本。
- 读取方必须忽略未知对象字段、跳过未知的非终结 NDJSON 帧；未知错误码回退到退出码和 `retryable`。

### P2 永不阻塞

非交互场景下，绝不能等待 stdin、编辑器、分页器、确认提示或系统钥匙串解锁。

要求：

- 非 TTY 下默认关闭 pager、editor、颜色和确认提示。
- 需要确认的写操作在非交互下必须显式 `--yes`，否则立即以退出码 12（`confirmation_required`）失败，不能挂起。
- 即使 stdin 是 TTY，确认提示也有硬超时（默认 30 秒）；超时或 stdin EOF 一律视为「否」，立即退出 12。
- `--no-input` 强制进入非交互模式，禁止任何提示和隐式 stdin 读取；但显式数据输入（`--from-file -`、`--token-stdin`）不受影响。
- `--interactive` 强制允许交互；与 `--no-input` 同时出现时报 `conflicting_flags` 并退出 2。
- keyring 探测必须有短超时（建议 2 秒）；不可用时立即失败，不能触发系统解锁弹窗。
- 命令启动阶段不得发起网络请求、不得弹欢迎/登录/更新提示。

### P3 stdout 是数据，stderr 是诊断

- stdout：只放命令结果（人类可读结果或 JSON/NDJSON 数据）。
- stderr：进度、警告、日志、调试信息、错误说明、交互提示。
- 这样 `acli ... | jq`、`acli ... > out.txt` 永远得到干净数据。
- 进度条、spinner 和确认提示只能出现在 stderr 或 `/dev/tty`，且只能出现在 TTY。
- 机器模式下，stderr 上的每条诊断也是带 `type` 的 JSON 文档（见 §4.3.4），不能混入裸文本。

### P4 退出码与错误码是 API

Agent 依靠退出码做分支，而不是解析错误文案。

要求：

- 退出码稳定、成文、按失败类别区分（见 §4.4）。
- 错误对象包含稳定的 `code` 字段；`message` 可以改，`code` 不可以随意改。
- 部分成功不得返回 0。
- 信号取消使用约定退出码（SIGINT → 130，SIGTERM → 143）。
- `error.retryable` 是重试判断的唯一权威；退出码表里的「可重试」只是默认提示。

### P5 显式优于猜测

- 不根据 TTY 静默切换输出格式。人类管道 `| grep` 不应该突然收到 JSON。
- 不根据文件名、环境或历史状态猜测用户意图。
- flag > 环境变量 > 项目配置 > 用户配置 > 默认值，优先级固定且可查询。
- **对 CLI 输入**：未知 flag、未知枚举值、`--fields` 的未知字段名、冲突 flag 一律报错（退出 2），不静默忽略。
- **对 CLI 输出**：读取方必须前向兼容——忽略未知字段、跳过未知非终结帧、未知错误码回退到退出码。两条规则分别适用于「输入严格」和「输出宽容」。

### P6 幂等、可预演、可重试

- 读操作天然幂等。
- 写操作尽量幂等；创建类操作支持 `--idempotency-key`，并定义作用域、保留期和同 key 不同参数的冲突行为。
- 所有写操作支持 `--dry-run`，输出「将要做什么」的计划，不产生副作用。
- 只对 `retryable=true` 的操作自动重试；非幂等写操作默认不自动重试。
- 长任务返回 job id，支持查询、等待、取消。
- 无法确定写操作是否已生效时，必须返回 `outcome_unknown`，不能假装成功或普通超时。

### P7 自描述

Agent 应该能在不读外部文档的情况下发现能力边界。

要求：

- `acli --help` 面向人类，分层、简短、带示例。
- `acli schema` 面向机器，默认输出命令索引；`acli schema <resource> [<verb>]` 输出完整详情。
- schema 必须包含：参数、类型、默认值、约束、示例、退出码、错误码、是否需要认证、是否需要确认、是否支持 dry-run、是否支持 job、输出 schema、环境变量映射、弃用元数据。
- `acli version --json` 输出 CLI 版本、commit、平台和机器契约版本。
- 帮助中的示例必须是可直接复制执行的完整命令。
- 外部文档、README 和 skill 只是可选的说明层；CLI 必须在没有它们的情况下也能被完整发现和使用。

### P8 确定性与有界

- 默认稳定排序；不输出随机顺序的 map。
- 不隐式输出当前时间、随机 ID 或环境相关信息；需要时用显式 flag。
- 列表默认有上限，分页使用不透明 cursor。
- 输出被截断时必须同时提供机器可读标记（`meta.truncated` / `next_cursor`）和人类提示，不能静默截断。
- 提供 `--fields` / `--limit` / `--max-bytes` / `--quiet` 控制上下文预算。
- `acli schema` 默认只返回索引，避免首次发现就耗尽 agent 上下文。

### P9 安全默认

- 破坏性操作默认需要确认；非交互下必须 `--yes`。
- `--yes` 只跳过确认，不跳过校验、权限检查或 dry-run 语义。
- `--yes` 只能来自 argv，不能来自环境变量或配置文件。
- 不隐式修改全局状态；配置路径可显式指定。
- 输出中默认脱敏 token、密码、Cookie 和密钥；需要显示时用显式 flag。
- 认证失败（401）与授权失败（403）必须区分。
- 推荐用 `--token-file` 或 `ACLI_TOKEN` 传递凭据；`--token` 会进入 shell history 和进程列表，仅作为兼容入口并在 TTY 下警告。

### P10 人类体验是一等渲染层

Agent 模式不是「把人类功能删掉」，而是「把人类功能降级为可忽略的诊断信息」。

要求：

- 默认彩色表格、对齐、进度、确认、shell completion。
- 错误消息给出下一步可执行的命令建议。
- 非 TTY 时输出退化为无 ANSI 的纯文本，语义不变。
- `NO_COLOR`、`CLICOLOR`、`CLICOLOR_FORCE`、`FORCE_COLOR`、`TERM=dumb` 等约定必须被尊重。
- 状态不能只靠颜色表达；错误、警告、破坏性状态必须同时有文字或符号。
- 表格按 wcwidth 计算列宽，支持 CJK 宽字符；窄终端（建议 < 80 列）降级为纵向 `key: value` 或逐条列表。
- 提供 `--no-progress`；`--quiet` 只抑制信息性诊断，绝不抑制确认、错误和弃用警告。

### P11 兼容性是承诺

Agent 生成的脚本比人类更依赖长期稳定性。

- CLI 使用 SemVer。
- 机器契约有独立版本号 `schema_version`，只在破坏性变更时递增。
- 同一大版本内只做增量变更；读取方按 P5 的前向兼容规则处理新字段。
- 弃用先在 stderr 警告，保留至少一个 minor 版本后再移除。
- schema 中为命令、flag、字段和错误码提供 `deprecated`、`replaced_by`、`removal_version`、`stability` 元数据。
- CI 必须对已发布 schema 做破坏性 diff 检查，并维护人类输出 golden 文件。

---

## 4. 命令契约

### 4.1 命令语法

```
acli [global flags] <resource> <verb> [args] [flags]
acli [global flags] <top-level-command> [args] [flags]
```

采用 **资源优先（noun-verb）** 结构：

| 形式 | 示例 | 说明 |
|---|---|---|
| 资源 + 动词 | `acli project list` | 主要命令面；资源可枚举 |
| 资源 + 动词 + ID | `acli project get <id>` | ID 作为位置参数 |
| 顶层命令 | `acli schema` / `acli version` / `acli auth` / `acli config` / `acli job` / `acli completion` / `acli diagnostic` / `acli help` | 非资源型操作 |

标准动词：

| 动词 | 语义 | 是否写操作 |
|---|---|---|
| `list` | 列出资源 | 否 |
| `get` | 获取单个资源 | 否 |
| `create` | 创建资源 | 是 |
| `update` | 更新资源 | 是 |
| `delete` | 删除资源 | 是 |

规则：

- 每个写动词都必须支持 `--dry-run`。
- 每个命令都必须有非交互路径；交互式能力必须有等价 flag（例如 `--edit` 的等价物是 `--from-file`）。
- v1 不提供隐藏别名；未知命令/动词/flag 输出 did-you-mean 建议（如 `Did you mean 'list'?`），退出 2。
- 不带参数运行 `acli`：输出认证状态、三条可复制命令和 `acli --help` 提示，退出 0。
- 只给资源不给动词（如 `acli project`）：输出资源级帮助，退出 0。
- `acli help <command>` 是 `acli <command> --help` 的等价形式；显式帮助退出 0。
- 短选项：`-h` = `--help`，`-V` = `--version`，`-o` = `--output`，`-y` = `--yes`。

### 4.2 全局 flag、命令级 flag 与环境变量

**全局 flag**（所有命令都接受）：

| Flag | 取值 | 默认 | 用途 |
|---|---|---|---|
| `-h`, `--help` | — | — | 帮助；退出 0 |
| `-V`, `--version` | — | — | 版本；退出 0 |
| `-o`, `--output` | `human` \| `json` \| `ndjson` | `human` | 输出格式；显式选择，不随 TTY 切换 |
| `--json` | — | — | `--output=json` 的简写 |
| `--quiet` | — | — | 抑制信息性诊断；不抑制错误、警告、弃用提示和确认 |
| `--verbose` | — | — | 输出更多诊断到 stderr |
| `--debug` | — | — | 输出请求 ID、重定向链路等调试信息；强制脱敏 |
| `--no-color` | — | 自动 | 禁用 ANSI 颜色 |
| `--no-progress` | — | 自动 | 禁用进度条和 spinner |
| `--no-pager` | — | 自动 | 禁用分页器 |
| `--no-input` | — | 自动 | 禁止提示和隐式 stdin 读取；显式数据输入不受影响 |
| `--interactive` | — | 自动 | 强制允许交互；与 `--no-input` 冲突 |
| `--timeout` | 时长，如 `30s` / `10m` | 命令定义（普通 60s，等待类 10m） | 超时；超时退出码 8 |
| `--config` | 路径 | 自动发现 | 指定配置文件；禁用自动发现 |
| `--strict-config` | — | 否 | 配置中出现未知键时报错，而不是警告并忽略 |
| `--token-file` | 路径；`-` 表示 stdin | — | 从文件读取 token |
| `--token-stdin` | — | — | 从 stdin 读取 token |
| `--token` | 字符串 | — | 直接传 token；TTY 下警告（会进入 shell history / 进程列表） |
| `--log-file` | 路径 | — | 写日志文件；默认不写 |

**命令级 flag**（由命令声明，未声明该 flag 的命令收到时报错）：

| Flag | 取值 | 默认 | 用途 |
|---|---|---|---|
| `-y`, `--yes` | — | 否 | 跳过确认提示；只能来自 argv |
| `--dry-run` | `client` \| `server` | 命令定义 | 只输出计划，不产生副作用；默认优先 server |
| `--limit` | 整数 | 命令定义（默认 50） | 单页数量；最大值由 schema 公开 |
| `--cursor` | 不透明字符串 | — | 分页游标 |
| `--all` | — | 否 | 拉取所有页；要求生效输出为 `ndjson` |
| `--fields` | 逗号分隔的顶层字段名 | 全部 | 裁剪 `data` 字段；不能裁掉 `meta` |
| `--max-bytes` | 字节数 | 无 | 机器输出在条目边界截断；设置 `meta.truncated` |
| `--idempotency-key` | 字符串 | — | 创建类操作的幂等键 |
| `--wait` / `--no-wait` | — | 命令定义 | 是否等待长任务完成 |
| `--from-file` | 路径；`-` 表示 stdin | — | `--edit` 的非交互等价 |
| `--cancel-on-interrupt` | — | 否 | 等待 job 时收到 SIGINT/SIGTERM 是否取消远端任务 |
| `--full` | — | 否 | `get` 返回全部字段，而不是摘要 |
| `--filter` | 命令定义的表达式 | — | 批量选择；仅批量命令支持 |

命令级 flag 的适用性：

- 每个命令必须在 schema 的 `flags` 中列出自己接受的命令级 flag。
- 未声明的命令级 flag 一律视为用法错误：退出 2，错误码 `flag_not_applicable`。
- `--fields` 只裁剪 `data` 的顶层字段；人类模式接受该 flag，但不改变人类摘要版式。`--max-bytes` 只对机器输出生效。
- `--all`、`--cursor`、`--limit` 只对有分页语义的命令有效。
- `--dry-run`、`--yes` 只对写命令有效；只读命令收到时报错。

环境变量映射：

| 环境变量 | 对应 flag | 说明 |
|---|---|---|
| `ACLI_OUTPUT` | `--output` | `human` / `json` / `ndjson` |
| `ACLI_NO_INPUT` | `--no-input` | 设为 `1` 时禁止提示 |
| `ACLI_TIMEOUT` | `--timeout` | 时长字符串 |
| `ACLI_CONFIG` | `--config` | 配置文件路径 |
| `ACLI_TOKEN` | — | token；优先级低于显式 flag |
| `ACLI_TOKEN_FILE` | `--token-file` | token 文件路径 |
| `ACLI_LOG_FILE` | `--log-file` | 日志文件路径 |
| `ACLI_NO_COLOR` | `--no-color` | 设为 `1` 时禁用颜色 |
| `ACLI_NO_PROGRESS` | `--no-progress` | 设为 `1` 时禁用进度 |
| `ACLI_PROMPT_TIMEOUT` | — | 确认提示超时，默认 `30s` |
| `ACLI_ENDPOINT` | — | 覆盖 API endpoint |
| `HTTPS_PROXY` / `HTTP_PROXY` / `NO_PROXY` | — | 标准代理变量；大小写形式都支持 |
| `SSL_CERT_FILE` / `SSL_CERT_DIR` | — | 自定义 CA |

冲突与优先级：

- `--json` 与 `--output` 同时给出且值不同 → 退出 2，错误码 `conflicting_flags`。
- `--quiet` 与 `--verbose` / `--debug` 同时给出 → 退出 2。
- `--no-input` 与 `--interactive` 同时给出 → 退出 2。
- `--all` 与 `--cursor` 同时给出 → 退出 2。
- `--all` 要求生效输出为 `ndjson`（可来自 `--output=ndjson` 或 `ACLI_OUTPUT=ndjson`）；生效输出为 `human` 或 `json` 时退出 2。
- `--all` 与 `--max-bytes` 同时给出 → 退出 2；「拉取全部」和「按字节截断」互斥。
- `--token`、`--token-file`、`--token-stdin` 三者任意两个同时给出 → 退出 2。
- `--wait` 与 `--no-wait` 同时给出 → 退出 2。
- 单次调用最多一个 stdin 消费者：`--from-file -`、`--token-stdin`、`--token-file -` 任意两个同时给出 → 退出 2。
- `--dry-run=server` 在命令不支持 server 模式时 → 退出 2，错误码 `usage`；`--dry-run` 不带值时按命令的默认模式（优先 server，其次 client）。
- `--dry-run` 与 `--yes` 可以同时给出；dry-run 优先，不执行任何写操作。
- 布尔 flag 语法：`--flag`、`--no-flag`、`--flag=true|false`；不接受 `--flag=1`、`--flag=yes` 等隐式真值。
- `ACLI_YES` 和配置文件中的 `yes` 一律无效；确认绕过只能来自 argv。

### 4.3 输出契约

#### 4.3.1 默认格式

默认 `--output=human`，**即使输出被管道重定向也不静默改成 JSON**。

理由：

- 人类管道（`acli project list | grep foo`）不应该因为离开 TTY 就改变格式。
- 静默格式切换是最常见的 agent/人类双用 CLI 事故之一。
- Agent 可以通过 `--json`、`--output=ndjson`、`ACLI_OUTPUT=json` 或 `schema` 明确选择机器格式。

非 TTY 的人类输出：

- 保留相同的文本结构和字段，去掉 ANSI 颜色、进度动画和分页器。
- 表格列以空格对齐；不输出终端宽度相关的换行。
- 截断使用明确的 `…` 标记，并在 stderr 提示如何获取完整数据。

**唯一例外**：`acli schema` 是机器发现命令，默认 `--output=json`；`acli schema --output=human` 输出人类摘要。该例外必须在帮助和 schema 中明确标注。

#### 4.3.2 机器文档模型

所有机器可读输出（stdout 和 stderr）共享同一套带 `type` 的信封：

成功（stdout）：

```json
{
  "type": "result",
  "data": {},
  "meta": {
    "schema_version": "1"
  }
}
```

错误（stderr）：

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
  "meta": {
    "schema_version": "1"
  }
}
```

警告（stderr）：

```json
{
  "type": "warning",
  "warning": {
    "code": "deprecated_flag",
    "message": "flag --old is deprecated",
    "replaced_by": "--new",
    "removal_version": "0.3.0"
  },
  "meta": {
    "schema_version": "1"
  }
}
```

`--debug` 在机器模式下输出额外的 `{"type":"debug",...}` 文档到 stderr，仍然强制脱敏。

通用规则：

- 所有文档的 `meta.schema_version` 必填，且成功、错误、警告共享同一版本空间。
- `meta.request_id` 可选；失败时建议提供。
- `meta.dry_run` 在 dry-run 时为 `true`。
- 读取方必须忽略未知对象字段。
- 未知 `type` 的 stderr 文档可以忽略；未知的 NDJSON 非终结帧必须跳过。
- 未知 `error.code` 时，读取方回退到退出码和 `error.retryable`。

#### 4.3.3 JSON 成功输出

单资源：

```json
{
  "type": "result",
  "data": {
    "id": "prj_123",
    "name": "demo",
    "created_at": "2026-11-08T05:00:00Z"
  },
  "meta": {
    "schema_version": "1",
    "request_id": "req_abc123"
  }
}
```

列表：

```json
{
  "type": "result",
  "data": [
    { "id": "prj_123", "name": "demo" },
    { "id": "prj_456", "name": "test" }
  ],
  "meta": {
    "schema_version": "1",
    "request_id": "req_abc123",
    "count": 2,
    "has_more": true,
    "next_cursor": "eyJvZmZzZXQiOjJ9",
    "truncated": false
  }
}
```

字段语义：

| 字段 | 适用 | 语义 |
|---|---|---|
| `meta.count` | 列表 | **当前页**条目数，不是匹配总数 |
| `meta.has_more` | 列表 | 是否还有下一页 |
| `meta.next_cursor` | 列表 | 下一页 cursor；最后一页为 `null`；该字段始终存在 |
| `meta.truncated` | 列表/流 | 是否因 `--max-bytes` 或上限被截断 |
| `meta.omitted_items` | 列表/流 | 被截断的条目数；未知时为 `null` |
| `meta.partial` | 批量 | 是否为部分成功结果 |
| `meta.dry_run` | 写操作 | 是否为 dry-run 结果 |
| `meta.idempotent_replay` | 写操作 | 重试路径上是否把已生效的操作按幂等成功处理 |

约束：

- `data` 必存在；列表空结果返回 `[]`，不是 `null`。
- 单资源响应不包含 `count` / `has_more` / `next_cursor`。
- 字段顺序不作保证；agent 必须按字段名读取。
- 不输出 NaN / Infinity；使用 `null` 或字符串。
- ID 一律字符串；时间一律 RFC 3339 UTC，输出到秒精度；解析时接受小数秒。
- 排序由命令定义且稳定；机器可读排序按字节序，不依赖 locale。

#### 4.3.4 错误与警告输出

- 错误只写 stderr。
- 非流式模式下，非零退出时 stdout 必须为空；`partial_failure` 是例外（见 §4.5）。
- 流式模式（`--output=ndjson`）下，stdout 可能已经输出部分 `item` 帧；失败时不得发送 `end` 帧，stderr 输出 `type:"error"`，非零退出。读取方必须以是否收到 `end` 判断数据是否完整。
- `partial_failure` 在流式模式下允许已输出部分条目，并以 `meta.partial=true` 的 `end` 帧结束；stderr 仍输出汇总错误。
- 人类模式（`--output=human`）：三段式文本，见 §4.3.6。
- 机器模式（`--output=json|ndjson`）：stderr 上每条诊断都是 JSON 文档，不能混入裸文本。
- 警告不改变退出码；错误改变退出码。
- `--quiet` 不抑制错误、警告、弃用提示和确认提示。
- 堆栈只在 `--debug` 下出现，且必须脱敏。
- 错误 `details` 不得包含 token、Cookie、Authorization header 或密码。

#### 4.3.5 NDJSON 输出

用于流式结果和大量数据：

```json
{"type":"item","data":{"id":"prj_123","name":"demo"}}
{"type":"item","data":{"id":"prj_456","name":"test"}}
{"type":"end","meta":{"schema_version":"1","count":2,"has_more":false,"next_cursor":null,"truncated":false}}
```

规则：

- 每个 stdout 行都是一个完整 JSON 对象。
- `type` 当前为 `item` 或 `end`；读取方必须跳过未知的非终结帧，`end` 是终结帧。
- 进度、警告、错误只写 stderr，不进入 NDJSON 数据流。
- 如果来源端读取/解码在中途失败，不发送 `end` 帧；在 stderr 输出一个 `type:"error"` 文档并非零退出。读取方以缺失 `end` 判断数据不完整。
- **stdout 上的 EPIPE/SIGPIPE 不属于流中途失败**：下游关闭管道（如 `| head`）时，立即停止当前页和 `--all` 的后续请求，不发送 `end`，不写错误文档，不重试，退出 0。
- 读取操作重试时，不得重新输出已经发出的条目；只允许在某一页的首个条目发出前重试。无法保证时，直接失败并在 `details` 中给出已发出的条目数。
- `--max-bytes` 达到上限时，在条目边界停止，发送带 `truncated:true` 的 `end` 帧，退出 0。`--max-bytes` 与 `--all` 互斥。
- `--all` 只允许与 `ndjson` 组合；逐页拉取，全部通过同一个 stdout 流输出。遇到 EPIPE 后不得继续请求后续页。
- 批量部分失败时，发送 `meta.partial=true` 的 `end` 帧，退出 10，并在 stderr 输出汇总错误。

#### 4.3.6 人类输出

- 默认彩色；`--no-color`、`NO_COLOR`、非 TTY 时自动关闭。
- `FORCE_COLOR` / `CLICOLOR_FORCE` 可在 TTY 下强制开启；优先级：`NO_COLOR` > `FORCE_COLOR` > 自动检测。
- 表格、分组、关键字段摘要优先；不要默认倾倒全部字段。`get` 默认展示摘要，`--full` 展示全部字段。
- 状态列必须带文字或 ASCII 符号，颜色只作冗余：`x error`、`! warning`、`ok success`。
- 表格按 wcwidth 计算列宽；窄于 80 列时降级为纵向 `key: value` 或逐条列表；超长单元格用 `…` 截断，但 ID 保持完整可复制。
- 错误三段式：

```
Error: project 'prj_999' not found.
Cause: the requested project does not exist or was deleted.
Hint:  Run `acli project list` to see available projects.
Request ID: req_abc123
```

- 错误退出码不打印在正文中；需要时用 `--debug`。
- 列表被截断时给出明确提示和可复制的翻页命令：

```
… 还有 120 条。继续：acli project list --cursor 'eyJvZmZzZXQiOjJ9'
```

#### 4.3.7 dry-run 计划

所有写操作的 `--dry-run` 输出计划，不产生副作用。机器模式：

```json
{
  "type": "result",
  "data": {
    "plan": {
      "dry_run": true,
      "mode": "server",
      "summary": { "create": 0, "update": 0, "delete": 1 },
      "actions": [
        {
          "op": "delete",
          "resource": "project",
          "id": "prj_123",
          "name": "demo",
          "changes": null
        }
      ]
    }
  },
  "meta": {
    "schema_version": "1",
    "dry_run": true
  }
}
```

人类模式：

```
Would delete project 'demo' (prj_123).
Dry run (server): no changes made. Re-run without --dry-run to apply.
```

规则：

- `--dry-run` 支持两种模式：
  - `server`：服务端评估，能发现权限、冲突和状态错误；默认优先使用。
  - `client`：只做本地参数校验和计划生成；不能保证服务端会接受。
- 命令不支持 server dry-run 时：
  - `--dry-run`（无值）回退到 client，并在 stderr 警告；
  - 显式 `--dry-run=server` 报 `usage`，退出 2。
- 计划对象必须包含 `mode`，人类版结尾也必须标出模式。
- server dry-run 仍执行权限检查，并将失败的动作在计划中标记并说明原因；client dry-run 只做本地校验，必须明确标注「未经过服务端验证」。
- dry-run 不要求 `--yes`。
- 批量 dry-run 先显示总数和前若干个样例；超长时显示剩余条数。
- 人类版结尾必须有 `Dry run (<mode>): no changes made.`，避免用户误以为已执行。

### 4.4 退出码

| 退出码 | 名称 | 含义 | 默认可重试 |
|---|---|---|---|
| `0` | `success` | 成功 | — |
| `1` | `error` | 未知或内部错误 | 否 |
| `2` | `usage` | 参数/用法错误、冲突 flag、非法 cursor、未知字段 | 否 |
| `3` | `not_found` | 资源不存在 | 否 |
| `4` | `unauthenticated` | 未认证或凭证失效 | 否 |
| `5` | `permission_denied` | 已认证但无权限 | 否 |
| `6` | `conflict` | 已存在、状态冲突、并发冲突、幂等键复用 | 否 |
| `7` | `rate_limited` | 触发限流 | 是 |
| `8` | `timeout` | 操作超时；写操作可能为 `outcome_unknown` | 视操作 |
| `9` | `canceled` | 操作被取消 | 否 |
| `10` | `partial_failure` | 批量操作部分失败 | 否 |
| `11` | `unavailable` | 5xx、连接失败、DNS 失败等服务端/网络暂时不可用 | 是 |
| `12` | `confirmation_required` | 非交互模式缺少 `--yes` | 否 |
| `130` | `interrupted` | 被 SIGINT 中断 | 否 |
| `143` | `terminated` | 被 SIGTERM 终止 | 否 |

补充规则：

- `error.retryable` 是重试判断的唯一权威；上表只是默认值。
- 非零退出时必须有结构化错误（机器模式）或三段式错误（人类模式）。
- 不允许用 `0` 表示「有警告但完成」；警告只写 stderr。
- 不允许用不同退出码表达同一类错误。
- 非流式模式下，`partial_failure` 是唯一允许非空 stdout 的非零退出：stdout 输出 `type:"result"` 的分项结果，`meta.partial=true`；stderr 输出汇总错误。
- 流式模式下，失败或部分失败可能已经输出部分 `item` 帧；数据完整性以是否收到 `end` 帧判断（见 §4.3.5）。
- 写操作超时或连接中断且无法确定是否已生效时，使用退出码 8 + `error.code=outcome_unknown`，`retryable=false`。
- stdout 被下游提前关闭（如 `| head`）时，视为正常结束，退出 0，不输出错误；其他 fd 的 SIGPIPE 退出 141。
- `job wait` 的退出码：`succeeded` → 0；`failed` → job 自带错误码，否则 1（`job_failed`）；`canceled` → 9；等待超时 → 8（只停止等待，不取消 job）。
- 原生 Windows 不在 v1 支持范围：启动时以退出码 1 + `unsupported_platform` 失败。

### 4.5 错误模型

错误码表（`error.code`）：

| 错误码 | 退出码 | 默认 retryable | 说明 |
|---|---|---|---|
| `usage` | 2 | 否 | 参数不合法 |
| `unknown_flag` | 2 | 否 | 未知 flag |
| `flag_not_applicable` | 2 | 否 | 该命令不接受此 flag |
| `unknown_field` | 2 | 否 | `--fields` 指定了未知字段 |
| `conflicting_flags` | 2 | 否 | 互斥 flag 同时出现 |
| `invalid_cursor` | 2 | 否 | cursor 非法或与当前参数/认证主体不匹配 |
| `cursor_expired` | 2 | 否 | cursor 已过期 |
| `unsupported_platform` | 1 | 否 | 平台不受支持 |
| `unsupported_server_version` | 1 | 否 | 服务端 API 版本超出 CLI 支持区间 |
| `internal_error` | 1 | 否 | 未预期的内部错误 |
| `tls_error` | 1 | 否 | 证书/TLS 配置错误 |
| `job_failed` | 1 | 否 | job 失败且无法映射原错误；可映射时使用 job 自己的错误码 |
| `not_found` | 3 | 否 | 资源不存在 |
| `unauthenticated` | 4 | 否 | 未认证或凭证失效 |
| `permission_denied` | 5 | 否 | 无权限 |
| `conflict` | 6 | 否 | 状态冲突或已存在 |
| `idempotency_key_reuse` | 6 | 否 | 同一幂等键搭配了不同请求体 |
| `rate_limited` | 7 | 是 | 限流；应携带 `retry_after_seconds` |
| `timeout` | 8 | 视操作 | 超时 |
| `outcome_unknown` | 8 | 否 | 写操作可能已生效但响应丢失 |
| `canceled` | 9 | 否 | 操作被取消 |
| `partial_failure` | 10 | 否 | 批量部分失败 |
| `unavailable` | 11 | 是 | 服务端 5xx、连接失败、DNS 失败 |
| `confirmation_required` | 12 | 否 | 非交互模式缺少 `--yes` |
| `network_error` | 11 | 是 | 通用网络错误 |
| `interrupted` | 130 | 否 | 被 SIGINT 中断；可能伴随 `outcome_unknown` |
| `terminated` | 143 | 否 | 被 SIGTERM 终止；可能伴随 `outcome_unknown` |

字段约定：

| 字段 | 必填 | 说明 |
|---|---|---|
| `code` | 是 | 稳定机器码，snake_case |
| `message` | 是 | 人类可读，可随版本改进 |
| `hint` | 否 | 下一步可执行的建议；其中的命令必须可直接复制 |
| `retryable` | 是 | 当前操作是否可以安全重试 |
| `request_id` | 否 | 服务端追踪 ID |
| `retry_after_seconds` | 否 | 429/503 的建议等待时间 |
| `details` | 否 | 结构化上下文；不得包含密钥 |

`outcome_unknown` 的 `details` 应包含：

- `resource`、`id`（若已知）；
- `idempotency_key`（若使用）；
- `hint` 指向：用同一幂等键重试，或先执行 `acli <resource> get <id>` 核对。

`retry_after_seconds` 与 `--timeout`：

- 若 `retry_after_seconds` 小于剩余超时预算，CLI 可等待并重试。
- 若会超出剩余预算，立即以退出码 7 失败，并在 `details.retry_after_seconds` 返回等待时长。
- 自动重试总时长不得突破 `--timeout`。

错误码兼容：

- 新增错误码属于 minor 变更。
- 错误码不得重命名或改变退出码映射；需要改变时新增错误码并弃用旧码。
- schema 的 `error_codes` 表必须列出每个命令可能返回的错误码及 retryable 语义。

### 4.6 交互、确认与安全

交互判定：

```
允许交互 = stdin 是 TTY
       且未设置 --no-input
       且未设置 ACLI_NO_INPUT=1
```

- **stdout 是否是 TTY 不影响交互判定**；它只影响颜色、表格和分页等装饰。这样 `acli delete ... | tee log` 不会把键盘前的人误判为非交互。
- `--interactive` 可以强制允许交互；与 `--no-input` 同时出现时报 `conflicting_flags`。
- `--interactive` 在没有任何可用输入通道时（stdin 非 TTY 且 `/dev/tty` 不可打开）报 `usage`，退出 2。
- `--no-input` 禁止提示和隐式 stdin 读取，但不禁止显式数据输入（`--from-file -`、`--token-stdin`）。
- **stdin 单一所有权**：单次调用最多一个 stdin 消费者。`--from-file -`、`--token-stdin`、`--token-file -` 任意两个同时出现时报 `conflicting_flags`，退出 2。

确认提示与答案通道：

- 存在可打开的 `/dev/tty` 时，提示和答案都走 `/dev/tty`，不占用 stdin。
- 只有 `/dev/tty` 不可用且 stdin 是 TTY 时，才回退到 stdin。
- 提示固定写 stderr 或 `/dev/tty`；stdout 永不出现提示。
- 有硬超时，默认 30 秒，可用 `ACLI_PROMPT_TIMEOUT` 调整。
- 答案通道 EOF 或超时一律视为「否」，立即退出 12，错误码 `confirmation_required`。
- 非交互且未提供 `--yes` 时，确认门禁先于显式 stdin 负载消费：立即退出 12，不读取 `--from-file -` / `--token-stdin` 的数据。
- 提示模板必须包含资源类型、名称、ID、影响条数和不可逆说明：

```
Delete project 'demo' (prj_123)? This cannot be undone. [y/N]
Use --dry-run to preview.
```

- 批量操作先显示总数和前若干个样例；`--yes` 不能替代该摘要。
- `--yes` 执行时向 stderr 输出一行审计摘要，例如 `Deleting 12 projects (confirmed by --yes).`。
- `--yes` 只跳过确认，不跳过校验、权限检查和 dry-run 语义。
- `--yes` 只能来自 argv；禁止 `ACLI_YES` 和配置文件中的 `yes`。
- `--dry-run` 不要求确认。

编辑器：

- 只有显式 `--edit` 才启动编辑器；非交互下 `--edit` 报错，hint 指向 `--from-file`。
- 编辑器回退顺序：`$VISUAL` > `$EDITOR` > 配置 `editor` > 平台默认。
- 编辑文件使用带注释头的 YAML/JSON 模板；校验失败时重新打开并保留用户内容，文件头列出错误。
- 编辑器非零退出：不提交，以明确错误退出。
- 内容未变化：输出 `No changes.`，退出 0。
- 提交前在 TTY 展示字段 diff 并确认；`--yes` 可跳过该确认，`--dry-run` 不跳过。

首次运行与空状态：

- 不带参数运行：输出认证状态、三条可复制命令和 `acli --help` 提示，退出 0。
- 未认证时运行需要认证的命令：退出 4，hint 固定为 `Run 'acli auth login' or set ACLI_TOKEN.`
- 只给资源不给动词：输出资源级帮助，退出 0。

### 4.7 认证生命周期

命令面：

```
acli auth login [--no-browser] [--device-code]
acli auth logout
acli auth status [--json]
acli auth refresh
```

登录降级链：

1. 浏览器回调（默认）；
2. `--device-code`：设备码流程，适合 SSH/容器；
3. `--no-browser`：无回显粘贴 token。

要求：

- 非交互模式下 `auth login` 不得自行启动浏览器或等待输入；需要交互时必须显式运行，否则退出 4。
- keyring 探测有 2 秒超时；不可用时立即失败，不触发系统解锁弹窗。
- 无 keyring 时回退到权限 `0600` 的凭据文件；`auth status` 必须显示存储后端和来源。
- 显式 token 来源 `--token-file` / `--token-stdin` / `--token` 三者互斥；任意两个同时给出 → `conflicting_flags`，退出 2。
- 非冲突来源的优先级：显式 token 来源 > `ACLI_TOKEN_FILE` > `ACLI_TOKEN` > keyring > 凭据文件。
- `--token` 在 TTY 下向 stderr 警告，提示使用 `--token-file` 或 `ACLI_TOKEN`。
- 非交互模式收到 401：退出 4，不自动尝试交互式刷新；hint 提示外部注入新 token 或运行 `acli auth refresh`。
- 多页读取中途收到 401，且当前页尚未输出任何条目：同一认证主体下允许刷新一次 token，并用同一 cursor 重发该页；刷新不占用读重试次数。刷新失败或认证主体变化 → 退出 4，`details.failed_cursor` 携带该页 cursor。
- 当前页已经输出条目后收到 401：不得自动刷新重发，避免重复输出；退出 4，`details.failed_cursor` 携带该页 cursor，`details.emitted_items` 给出已输出条目数。
- `auth status --json` 输出认证状态、主体、过期时间、存储后端；不输出 token 本身。
- `auth logout` 删除 keyring/凭据文件中的 token；幂等。
- 所有 token、Cookie、Authorization header 在输出、日志和 `--debug` 中强制脱敏。

### 4.8 幂等、重试与长任务

幂等：

- `list` / `get`：天然幂等。
- `update` / `delete`：按资源 ID 幂等。
  - 第一次请求就收到 `not_found`：返回 `not_found`，退出 3。
  - 在重试路径上（CLI 已经发出过至少一次请求）收到 `not_found`：视为成功，向 stderr 输出 warning，并设置 `meta.idempotent_replay=true`。这覆盖「第一次删除已生效但响应丢失」的情况。
- `create`：支持 `--idempotency-key`。
  - 作用域：凭据 + endpoint + 命令 + key。
  - 服务端至少保留 24 小时。
  - 相同 key + 相同请求体：返回第一次的结果，不重复创建。
  - 相同 key + 不同请求体：`idempotency_key_reuse`，退出 6，`retryable=false`。
- 批量操作：逐项报告成功/失败；整体返回 `partial_failure`，不返回 0。

重试：

- 只对 `retryable=true` 的错误自动重试。
- 默认：读操作最多 2 次；按 ID 的 update/delete 最多 2 次；带幂等键的 create 最多 2 次；其他写操作 0 次。
- 退避指数增长并带抖动；总时长受 `--timeout` 约束。
- 429/503 优先遵循 `Retry-After`；超出剩余预算时快速失败并返回 `retry_after_seconds`。
- 非幂等写操作在超时或连接中断后无法确定结果时，返回 `outcome_unknown`，不得自动重试。
- 非幂等写请求已完整发出后收到 SIGINT/SIGTERM：在 `min(剩余 --timeout, 5s)` 宽限内等待确定性响应。收到响应则按响应处理；未收到则退出 130/143，`error.code` 为 `interrupted`（SIGINT）或 `terminated`（SIGTERM），`details.outcome_unknown=true`，`retryable=false`，hint 指向用同一幂等键重试或先 `acli <resource> get <id>` 核对。请求发出前收到信号则直接退出 130/143，不标注 `outcome_unknown`。
- `--debug` 在 stderr 输出每次重试原因；`--quiet` 不抑制重试错误，但可抑制重试信息。

长任务：

- 预计超过 30 秒的操作可以支持 job 模式。
- 每个命令在 schema 中声明 `supports_jobs` 和 `default_wait`。
- `--no-wait` 的返回形状固定：

```json
{
  "type": "result",
  "data": {
    "job_id": "job_123",
    "status": "queued",
    "created_at": "2026-11-08T05:00:00Z"
  },
  "meta": { "schema_version": "1" }
}
```

- 同步返回时，`data` 是资源对象；schema 用 `oneOf` 区分同步结果与 job 结果。
- `--wait` 默认行为由命令的 `default_wait` 决定；`--wait` / `--no-wait` 显式覆盖。
- `acli job get <id>`：查询状态。
- `acli job wait <id> [--timeout]`：等待完成。
  - `succeeded` → 退出 0，`data` 为 job 对象。
  - `failed` → 使用 job 自带错误码；无法映射时 `job_failed`，退出 1。
  - `canceled` → 退出 9。
  - 等待超时 → 退出 8，`error.code=timeout`，`details.job_id`；只停止等待，不取消 job。
- `acli job cancel <id>`：幂等；对已终结 job 返回明确结果，不报未知错误。
- `acli job list`：列出当前凭据下的 job。
- 等待超过 2 秒时，在 TTY stderr 输出单行心跳：

```
Waiting for job job_123 (running, 12s)… Ctrl-C to stop waiting.
```

- 等待中 SIGINT/SIGTERM：默认只停止等待，保留远端 job，打印 id 和后续命令；`--cancel-on-interrupt` 时取消 job 并分别退出 130/143。
- 创建 job 后立即在人类输出中打印 `acli job wait <id>` 和 `acli job cancel <id>`。
- job 状态枚举：`queued` / `running` / `succeeded` / `failed` / `canceled`；未知状态按非终态处理，并输出警告。
- `job.result` 复用成功 `data`；`job.error` 复用 §4.5 错误对象。

并发：

- 多个 Acli 进程可以并行运行。
- 配置文件写入必须原子化（临时文件 + rename）并加锁；并发写冲突返回明确错误。
- token 刷新使用进程间锁，避免并发刷新互相覆盖。
- cursor 是快照语义：翻页期间资源变化不保证可见；并发修改不使 cursor 失效，除非 cursor 本身过期。

### 4.9 自描述与发现

命令面：

```bash
acli --help
acli project --help
acli project create --help
acli schema
acli schema project
acli schema project create
acli version --json
acli diagnostic
```

`acli schema` 的行为：

- 默认输出 JSON（唯一例外，见 §4.3.1），并要求 `{type:"result",data,meta}` 信封。
- 默认只输出**命令索引**：`path`、`summary`、`destructive`、`requires_auth`、`requires_confirmation`、`supports_dry_run`、`supports_jobs`、`default_wait`。
- `acli schema <resource>` 输出该资源下所有命令的索引。
- `acli schema <resource> <verb>` 输出单个命令的完整详情。
- `--output=human` 输出人类可读摘要。

命令详情至少包含：

```json
{
  "path": ["project", "create"],
  "summary": "Create a project",
  "requires_auth": true,
  "requires_confirmation": false,
  "destructive": false,
  "supports_dry_run": true,
  "dry_run_modes": ["client", "server"],
  "supports_jobs": false,
  "default_wait": true,
  "args": [],
  "flags": [
    {
      "name": "name",
      "type": "string",
      "required": true,
      "default": null,
      "env": null,
      "description": "Project name"
    }
  ],
  "output_schema": {
    "oneOf": [
      { "type": "object", "required": ["id"] },
      { "type": "object", "required": ["job_id", "status"] }
    ]
  },
  "error_codes": ["usage", "unauthenticated", "permission_denied", "conflict", "rate_limited", "timeout", "unavailable"],
  "exit_codes": [0, 2, 4, 5, 6, 7, 8, 11],
  "examples": ["acli project create --name demo --json"],
  "stability": "stable",
  "deprecated": false,
  "replaced_by": null,
  "removal_version": null
}
```

要求：

- schema 输出本身是稳定机器契约，受 `schema_version` 管理。
- schema 包含全局 flag、环境变量映射、退出码表和错误码表。
- 每个命令的 `flags` 列表是权威适用性清单；未列出的命令级 flag 视为 `flag_not_applicable`。
- schema 必须区分全局 flag 与命令级 flag，并给出 `dry_run_modes`。
- `path` 使用 argv 数组，避免 `project.create` 与 `project create` 两种写法歧义。
- `output_schema` 必须能区分同步结果与 job 结果（`oneOf`）。
- 每个命令至少一个可直接复制执行的示例。
- `--help` 的示例与 schema 的示例保持一致。
- `schema`、`version`、`help` 不需要认证，可在未登录状态调用。
- `acli diagnostic` 生成脱敏诊断包（版本、平台、配置来源、最近 request ID），用于问题反馈。

外部文档与 skill 的定位：

- `--help` / `schema` 是唯一必需的自描述层，也是接口权威。
- 外部指南（README、skill、手册）是可选任务层，只回答「何时用、按什么顺序做」，不得复制完整 flag 表。
- 外部指南与 `--help` / `schema` 冲突时，以后者为准；指南不得改变命令语义、退出码或输出契约。
- 指南必须声明适用的 CLI 版本与 `schema_version`；版本不匹配时不得作为接口依据。
- 插件市场和适配器负责把指南转换成具体 agent 的格式；CLI 本身不依赖任何市场或适配器。

### 4.10 分页、字段与上下文预算

- 列表默认 `--limit 50`；每个命令的最大值写入 schema；超过最大值报 `usage` 错误（退出 2）。
- `meta.count` 是当前页条目数；`meta.has_more` 表示是否还有下一页；`meta.next_cursor` 在列表响应中始终存在，最后一页为 `null`。
- 空列表返回 `data: []`。
- cursor 不透明；绑定 endpoint、认证主体、命令、过滤条件、排序和 `--fields`；任一变化返回 `invalid_cursor`（退出 2）。
- cursor 过期返回 `cursor_expired`（退出 2），hint 指示去掉 `--cursor` 重新开始。
- `--all` 要求生效输出为 `ndjson`；与 `--cursor`、`--max-bytes` 互斥；逐页拉取并流式输出。`--all` 时 `--limit` 只控制每页大小，不限制总量；`--timeout` 约束总时长。
- `--fields` 只作用于 `data` 的顶层字段；不能裁掉 `meta`、`next_cursor` 或分页信息；未知字段报错。
- `--max-bytes` 在条目边界截断；设置 `meta.truncated=true`，并尽量给出 `meta.omitted_items`；人类模式在 stderr 提示。
- 截断不得静默：要么 `next_cursor` 非空，要么 `meta.truncated=true`。
- `--quiet` 不改变数据结构，只抑制信息性诊断。
- 大结果优先使用 `ndjson`；不要默认把全部数据塞进一个 JSON 数组。

### 4.11 配置与环境

以下配置路径和格式暂按 TOML 描述；最终格式见 §8。

优先级（从高到低）：

1. 命令行 flag
2. 环境变量（`ACLI_*`）
3. 项目配置（`.acli.toml`）
4. 用户配置（`~/.config/acli/config.toml` 或平台等价路径）
5. 内置默认值

要求：

- `--config <path>` 指定配置文件并禁用自动发现；`ACLI_CONFIG` 同理。
- 自动发现：从当前目录向上查找 `.acli.toml`，在 git worktree 根目录或用户主目录停止。
- `acli config list --json` 输出生效配置及其来源（flag / env / project / user / default）。
- 配置键必须有 schema，并与 flag、环境变量一一对应；纳入 `acli schema`。
- 未知配置键：用户/项目配置默认警告并忽略，以支持旧版 CLI 读取新版配置；`--strict-config` 时改为报错。
- **安全敏感键**（`endpoint`、`proxy`、`ca`、`log_file`、自定义 header）不得来自向上发现的项目 `.acli.toml`。项目配置中出现这些键时忽略并警告；`--strict-config` 时报错。它们只能来自 flag、环境变量、用户配置，或用户显式 `--config` 指定的文件。
- 项目配置只应包含非安全设置（输出、颜色、进度、分页上限等）；安全敏感键在 schema 中标记为 `project_scope: false`。
- 配置文件中禁止存储明文 token、密码、Cookie；凭据只来自 §4.7 的显式来源。
- v1 不提供 profile/context；多账号/多环境使用 `--config` 隔离。
- `acli config set` 必须原子写入并加锁；不静默修改配置。
- 不读取隐藏状态；不自动修改配置文件，除非显式 `acli config set`。

### 4.12 网络、代理与限流

- 支持标准代理变量：`HTTPS_PROXY` / `HTTP_PROXY` / `NO_PROXY`，大小写形式都识别。
- 支持 `SSL_CERT_FILE` / `SSL_CERT_DIR` 自定义 CA。
- 支持 `ACLI_ENDPOINT`、用户配置或显式 `--config` 覆盖 endpoint，用于自建部署、staging 和契约测试；向上发现的项目配置不得覆盖 endpoint。
- 代理和 CA 设置只能来自环境变量、用户配置或显式 `--config`；项目配置不得覆盖。
- TLS 校验默认开启；v1 不提供 `--insecure`。
- 错误分类：
  - 连接失败/DNS/5xx → `unavailable` 或 `network_error`，退出 11，`retryable=true`。
  - TLS/证书错误 → `tls_error`，退出 1，`retryable=false`。
  - 429 → `rate_limited`，退出 7，`retryable=true`，携带 `retry_after_seconds`。
- `Retry-After` 优先于本地指数退避；超出剩余 `--timeout` 时快速失败。
- `acli config list --json` 必须显示代理、CA、endpoint 的生效值和来源。
- 启动阶段不得发起网络请求；只有实际执行命令时才连接网络。

### 4.13 平台、信号与并发

平台：

- v1 支持 Linux 和 macOS（amd64 / arm64）；WSL 按 Linux 语义支持。
- 原生 Windows 不支持：启动时以退出码 1 + `unsupported_platform` 失败，并给出 WSL 提示。
- `acli version --json` 必须报告平台和架构。

信号：

| 信号 | 退出码 | 行为 |
|---|---|---|
| SIGINT | 130 | 停止当前操作；等待 job 时默认只停止等待，`--cancel-on-interrupt` 时取消 job |
| SIGTERM | 143 | 同上；用于容器和 agent harness 超时 |
| SIGPIPE（stdout 关闭） | 0 | 视为下游正常结束，不输出错误 |
| SIGPIPE（其他 fd） | 141 | 异常管道关闭 |

- 信号处理必须清理临时文件、释放锁；不得留下半写入的配置文件。
- 信号命中在途写请求时，按 §4.8 的规则处理：已完整发出的非幂等写请求有最多 5 秒宽限期；无法确定结果时返回 `interrupted`（SIGINT）或 `terminated`（SIGTERM）+ `details.outcome_unknown=true`，`retryable=false`。
- 请求尚未发出时收到信号：直接退出 130/143，不标注 `outcome_unknown`。
- 等待 job 时收到信号，必须打印 job id 和后续命令。

并发：

- 多进程并行是支持的常态。
- 配置写入、token 刷新、cursor 和 job 等待的行为见 §4.8。
- 不依赖进程内全局缓存做正确性判断。

### 4.14 启动副作用、日志与遥测

- 启动阶段零网络请求、零提示、零更新检查。
- 遥测默认关闭；v1 不实现遥测。若未来引入，必须显式 opt-in，并尊重 `DO_NOT_TRACK=1`。
- 日志：
  - `--log-file <path>` / `ACLI_LOG_FILE` 开启文件日志；默认不写日志文件。
  - 日志包含时间、级别、命令、request_id；轮转和保留由用户或部署方负责。
  - `Authorization`、`Set-Cookie`、token、密码字段强制脱敏。
- `--debug` 输出重定向链路、请求 ID、重试原因；仍强制脱敏。
- 机器模式下 `--debug` 输出 `type:"debug"` JSON 文档，不破坏 stderr 的可解析性。
- `acli diagnostic` 生成脱敏诊断包，包含版本、平台、配置来源、最近 request ID，不包含凭据和请求体。

### 4.15 分发、安装与更新

- 分发单文件静态二进制；v1 支持 Linux/macOS amd64/arm64。
- 发布产物提供 checksums 和签名；`acli version --json` 的 `commit` 必须与产物构建一致。
- 提供官方容器镜像，tag 策略固定；容器内不写用户主目录。
- v1 不提供 `acli upgrade`；更新由包管理器或容器镜像负责。
- 不在启动时检查更新；安全公告和版本固定由发布渠道负责。
- 文档必须给出 agent/CI 固定版本和校验产物的推荐流程。

### 4.16 版本与兼容

- CLI 版本遵循 SemVer。
- 机器契约版本 `schema_version` 是字符串，当前为 `"1"`；只在破坏性变更时递增。
- 服务端 API 版本独立于 `schema_version`：CLI 在请求中发送 `Acli-Api-Version`，响应必须返回 `server_api_version`；CLI 在 `version --json` 中声明支持区间 `supported_server_api_versions`。
- 响应缺失版本或版本超出支持区间：业务命令以 `unsupported_server_version` 退出 1，`retryable=false`，hint 指向升级 CLI 或更换 endpoint；`schema` / `version` / `help` 仍可用。
- 未知枚举与新增字段按前向兼容降级；未知枚举不得映射为已知值。
- 同一 `schema_version` 内：
  - 可以新增命令、flag、JSON 字段、错误码、NDJSON 帧类型。
  - 不得删除或重命名已有字段、flag、命令。
  - 不得改变已有字段的类型或语义。
  - 不得改变已有错误码的退出码映射。
- 弃用流程：
  1. stderr 输出弃用警告，包含替代方案和移除版本。
  2. 至少保留一个 minor 版本。
  3. 下一个 major 移除。
- `--quiet` 不得抑制弃用警告。
- schema 中为命令、flag、字段、错误码提供：
  - `deprecated`：布尔；
  - `replaced_by`：替代项；
  - `removal_version`：计划移除版本；
  - `stability`：`stable` / `beta` / `experimental`。
- `acli version --json`：

```json
{
  "type": "result",
  "data": {
    "cli_version": "0.3.0",
    "commit": "abc1234",
    "platform": "linux-amd64",
    "supported_schema_versions": ["1"],
    "supported_server_api_versions": ["2026-11-01"]
  },
  "meta": {
    "schema_version": "1"
  }
}
```

- CI 发布门禁：
  - 对比上一发布版本的 schema，禁止破坏性 diff；
  - 维护人类输出 golden 文件；
  - 运行 PTY、非 TTY、跨平台、代理环境契约测试；
  - 验证 `--help` 与 schema 一致。

---

## 5. 双模式行为矩阵

| 行为 | 人类 TTY | 非 TTY / `--no-input` | `--output=json` / `--output=ndjson` |
|---|---|---|---|
| 默认输出 | 彩色表格/文本 | 无 ANSI 的纯文本 | machine document |
| stdout | 人类可读结果 | 人类可读结果 | 纯 JSON / NDJSON |
| stderr | 进度、警告、错误、提示 | 警告、错误 | JSON 诊断文档 |
| 颜色 | 自动 | 关 | 关 |
| 进度/spinner | stderr | 关 | 关 |
| 分页器 | 开（可 `--no-pager`） | 关 | 关 |
| 交互确认 | 允许（stdin TTY） | 禁止；需 `--yes`，否则退出 12 | 禁止；需 `--yes`，否则退出 12 |
| 提示输出 | stderr 或 `/dev/tty` | 无 | 无 |
| 编辑器 | 仅 `--edit` | 禁止；用 `--from-file` | 禁止；用 `--from-file` |
| 错误格式 | 三段式文本 | 三段式文本 | `type:"error"` JSON |
| 警告格式 | 文本 | 文本 | `type:"warning"` JSON |
| 退出码 | 相同 | 相同 | 相同 |
| 排序/字段语义 | 相同 | 相同 | 相同 |
| SIGINT / SIGTERM | 130 / 143 | 130 / 143 | 130 / 143 |
| stdout 提前关闭 | 0 | 0 | 0 |

关键点：**TTY 只改变呈现和交互许可，不改变命令语义、退出码或数据结构。**

---

## 6. 命令面示例

### 6.1 人类使用

```bash
# 列出项目
acli project list

# 查看单个项目（摘要；--full 看全部字段）
acli project get prj_123

# 创建项目（先预演）
acli project create --name demo --dry-run
acli project create --name demo

# 删除项目
acli project delete prj_123
# → stderr: Delete project 'demo' (prj_123)? This cannot be undone. [y/N]

# 批量删除前先看计划
acli project delete --filter 'starts_with(name, "tmp-")' --dry-run
```

### 6.2 Agent 使用

```bash
# 机器可读列表
acli project list --json --limit 20

# 分页
acli project list --json --limit 20 --cursor 'eyJvZmZzZXQiOjIwfQ=='

# 字段裁剪
acli project list --json --fields id,name

# 创建（非交互 + 幂等）
acli project create --name demo --yes --idempotency-key prj-demo-20261108 --json

# 删除（非交互必须 --yes）
acli project delete prj_123 --yes --json

# 长任务
acli project export prj_123 --no-wait --json
acli job wait job_123 --json --timeout 10m

# 发现能力
acli schema
acli schema project
acli schema project create

# 认证
acli auth status --json
```

### 6.3 输出示例

列表：

```bash
$ acli project list
ID        NAME   CREATED
prj_123   demo   2026-11-08
prj_456   test   2026-11-08
```

机器成功：

```bash
$ acli project get prj_123 --json
{
  "type": "result",
  "data": {
    "id": "prj_123",
    "name": "demo",
    "created_at": "2026-11-08T05:00:00Z"
  },
  "meta": {
    "schema_version": "1",
    "request_id": "req_abc123"
  }
}
```

机器错误：

```bash
$ acli project get prj_999 --json
# stdout 为空
# stderr:
{
  "type": "error",
  "error": {
    "code": "not_found",
    "message": "project 'prj_999' not found",
    "hint": "Run `acli project list` to see available projects.",
    "retryable": false,
    "request_id": "req_abc123"
  },
  "meta": { "schema_version": "1" }
}
# 退出码 3
```

人类错误：

```bash
$ acli project get prj_999
Error: project 'prj_999' not found.
Cause: the requested project does not exist or was deleted.
Hint:  Run `acli project list` to see available projects.
Request ID: req_abc123
# 退出码 3
```

非交互缺少确认：

```bash
$ acli project delete prj_123
# stderr:
{
  "type": "error",
  "error": {
    "code": "confirmation_required",
    "message": "delete requires confirmation in non-interactive mode",
    "hint": "Re-run with --yes to confirm.",
    "retryable": false
  },
  "meta": { "schema_version": "1" }
}
# 退出码 12
```

等待 job 失败：

```bash
$ acli job wait job_123 --json
# stderr: type:"error"，error.code 为 job 的原错误码，或 job_failed
# 退出码：原错误码，或 1
```

管道提前关闭：

```bash
$ acli project list | head -1
# 退出码 0；不输出 broken pipe 错误
```

---

## 7. 命令验收清单

任何命令在发布前必须通过以下检查。建议每条都写成自动化契约测试。

**非交互与阻塞**

- [ ] 在 stdin 关闭、stdout 被管道重定向、无 TTY 的环境下不会挂起。
- [ ] 在 `script -qec` 创建的 PTY 中运行需要确认的命令且不写 stdin，进程在提示超时内以退出码 12 结束，无挂起。
- [ ] `--no-input` 下任何命令都不读取提示；`--from-file -` / `--token-stdin` 仍能读取显式数据。
- [ ] `--no-input` 与 `--interactive` 同时给出时报 `conflicting_flags`，退出 2。
- [ ] keyring 不可用时在 2 秒内以退出码 4 失败，不触发系统解锁弹窗。
- [ ] 启动阶段无网络请求、无欢迎/登录/更新提示。
- [ ] 提示与答案优先走 `/dev/tty`；stdin 为管道时 `--interactive` 不会从管道读答案。
- [ ] 单次调用最多一个 stdin 消费者；`--from-file -` 与 `--token-stdin` / `--token-file -` 并用时报 `conflicting_flags`。
- [ ] 非交互且无 `--yes` 时，确认门禁先于显式 stdin 负载消费，不读取 payload。

**输出与解析**

- [ ] `--json` 输出符合 schema，包含 `type:"result"`、`data` 和 `meta.schema_version`。
- [ ] 错误输出包含 `type:"error"`、`error.code`、`retryable` 和 `meta.schema_version`；非流式模式下非零退出时 stdout 为空（`partial_failure` 除外），流式模式以是否收到 `end` 判断完整性。
- [ ] `--output=ndjson` 的每一行都是独立合法 JSON；失败时不发送 `end` 帧。
- [ ] stdout 只包含数据；进度、警告、错误、确认提示只出现在 stderr 或 `/dev/tty`。
- [ ] 读取方忽略未知字段、跳过未知非终结帧、未知错误码回退到退出码。
- [ ] `--fields` 只返回指定字段；未知字段报错；`meta` 和分页字段不能被裁掉。
- [ ] 列表默认有上限；`meta.count` 是当前页条数；`meta.has_more` / `meta.next_cursor` 语义正确。
- [ ] cursor 过期/非法分别返回 `cursor_expired` / `invalid_cursor`，退出 2。
- [ ] `--all` 只与 `ndjson` 组合；与 `cursor` / `json` 冲突时报错。
- [ ] `--max-bytes` 截断时 `meta.truncated=true`，人类模式有提示。
- [ ] 相同输入连续执行两次，输出内容一致（允许 `request_id` 等显式元数据不同）。
- [ ] 机器输出时间为 RFC 3339 UTC；ID 为字符串；空值为 `null`。
- [ ] NDJSON 来源端失败时不发送 `end`；EPIPE 时停止后续页请求、不写错误、退出 0；`partial_failure` 使用 `meta.partial=true` 的 `end`。
- [ ] `--all` 与 `--output=human`、`--output=json`、`--cursor`、`--max-bytes` 冲突时报错。
- [ ] 未声明的命令级 flag 返回 `flag_not_applicable`，退出 2。

**退出码与错误**

- [ ] 退出码符合 §4.4；同一失败场景退出码稳定。
- [ ] `retryable` 根据操作语义计算，不只依赖退出码。
- [ ] 非幂等写操作超时返回 `outcome_unknown`，不自动重试。
- [ ] 429/503 遵循 `Retry-After`；超出 `--timeout` 时快速失败并返回 `retry_after_seconds`。
- [ ] `job wait` 在 job 失败时返回原错误码或 `job_failed`，不返回 0。
- [ ] `partial_failure` 输出分项结果和汇总错误，退出 10。
- [ ] 重试路径上的 delete `not_found` 视为成功并标注 `meta.idempotent_replay`；首次 `not_found` 仍退出 3。
- [ ] 服务端 API 版本超出区间返回 `unsupported_server_version`，退出 1。
- [ ] hint 中的命令可直接复制执行。
- [ ] 未知命令/flag 输出 did-you-mean，退出 2。

**安全与交互**

- [ ] `--dry-run` 不产生任何副作用，输出可被机器解析，计划包含 `mode`。
- [ ] `--dry-run=server` 不可用时按规则回退或报错；client 模式明确标注「未经过服务端验证」。
- [ ] server dry-run 执行权限检查并列出将失败的动作；人类版包含 `Dry run (<mode>): no changes made.`。
- [ ] 批量删除先显示摘要；`--yes` 执行时 stderr 有审计行。
- [ ] `--yes` 只能来自 argv；`ACLI_YES` 和配置文件中的 `yes` 无效。
- [ ] 确认提示写 stderr 或 `/dev/tty`，stdout 无提示。
- [ ] `--edit` 在非交互下报错，hint 指向 `--from-file`；编辑器非零退出不提交。
- [ ] token、Cookie、Authorization header 不出现在 stdout、stderr、错误详情、日志和 `--debug` 中。
- [ ] `--token` 在 TTY 下向 stderr 发出泄漏警告。
- [ ] `auth status --json` 不输出 token；keyring 不可用时明确失败。
- [ ] 未认证错误的 hint 包含 `acli auth login` 或 `ACLI_TOKEN`。

**人类体验与可访问性**

- [ ] `NO_COLOR=1`、非 TTY 时无 ANSI 转义序列；`FORCE_COLOR=1` 可强制颜色。
- [ ] 状态不只靠颜色表达；错误、警告、破坏性状态都有文字或符号。
- [ ] 40/80 列窄终端下表格降级为可读布局。
- [ ] CJK 宽字符名称的表格列对齐正确。
- [ ] `--no-progress` 和 `TERM=dumb` 下无 spinner/进度动画。
- [ ] `--quiet` 不抑制错误、警告、弃用提示和确认。
- [ ] 人类 `get` 默认摘要，`--full` 展示全部字段。
- [ ] shell completion 可用；安装说明清晰。

**进程、平台与兼容**

- [ ] SIGINT 退出 130；SIGTERM 退出 143；stdout 被 `| head` 关闭时退出 0。
- [ ] SIGTERM/SIGINT 命中已完整发出的非幂等写请求时，按 §4.8 的 5 秒宽限规则处理，可能返回 `interrupted` / `terminated` + `outcome_unknown=true`。
- [ ] 等待 job 时收到信号，打印 job id 和后续命令；`--cancel-on-interrupt` 才取消远端任务。
- [ ] 配置文件原子写入；并发写入不损坏文件。
- [ ] token 并发刷新不互相覆盖。
- [ ] schema 与 `--help` 的命令、flag、示例一致。
- [ ] schema 包含 `requires_auth`、`requires_confirmation`、`supports_dry_run`、`supports_jobs`、`default_wait`、`error_codes`、`output_schema`。
- [ ] CI 对比上一发布 schema，禁止破坏性 diff；人类输出 golden 文件通过。
- [ ] 原生 Windows 启动时以 `unsupported_platform` 明确失败；WSL 下按 Linux 语义通过测试。
- [ ] 配置优先级符合 §4.11；`acli config list --json` 显示来源。
- [ ] 未知配置键默认警告并忽略；`--strict-config` 时报错。
- [ ] 项目 `.acli.toml` 不能覆盖 `endpoint` / `proxy` / `ca` / `log_file`；出现时警告并忽略。
- [ ] token 来源互斥与优先级符合 §4.7；`--token` 在 TTY 下有泄漏警告。

---

## 8. 待决问题

以下决策仍需要确认：

1. **配置文件格式**：当前假设 TOML。备选 YAML / JSON。需要结合实现语言和生态决定。
2. **`--fields` 的深度**：当前只承诺顶层字段。是否支持 `meta.owner.name` 这类点路径，需要看实际数据模型。
3. **是否提供格式化模板**：如 `--format '{{.id}}\t{{.name}}'`。这会让人类管道更灵活，但会增加机器契约的维护面。
4. **profile / context**：v1 当前不提供，只用 `--config` 隔离。是否需要在 v1 支持多账号/多 endpoint。
5. **原生 Windows 时间表**：v1 只支持 Linux/macOS/WSL；何时支持原生 Windows，以及 Ctrl+C 退出码、凭据管理器、配置目录如何映射。
6. **分发渠道**：当前只承诺静态二进制 + 容器镜像；是否需要 Homebrew / apt / npm。
7. **遥测**：v1 不做。未来是否提供 opt-in 遥测，以及如何与 `DO_NOT_TRACK` 交互。
8. **`acli schema` 默认 JSON 例外**：当前作为唯一例外保留。如果团队更倾向「所有命令默认 human」，则改为 `acli schema --json` 显式调用，并在 agent 引导文档中固定写法。

---

## 附录 A：设计取舍速查

| 选择 | 采用 | 拒绝 | 原因 |
|---|---|---|---|
| 输出切换 | 显式 flag | 按 TTY 静默切换 | 人类管道稳定；agent 应显式声明 |
| 交互判定 | stdin TTY + 显式开关 | 只看 stdin/stdout `isatty()` | agent 可能跑在 PTY；`| tee` 的人类不应被误判 |
| 确认提示 | stderr / `/dev/tty` + 硬超时 | stdout + 无限等待 | 管道干净；不阻塞 agent |
| 确认失败 | 退出码 12 | 与 usage 共用退出码 2 | 脚本可区分「flag 写错」和「缺 --yes」 |
| 错误传递 | 退出码 + `error.code` + `retryable` | 解析错误文案 | 文案会变，码不会 |
| 重试判断 | `error.retryable` | 退出码表 | 同一退出码在不同操作上可重试性不同 |
| 数据/诊断 | stdout / stderr 分离 | 混在一起 | 可组合、可管道 |
| 列表输出 | 默认限量 + cursor + `meta.has_more` | 默认全量 | 上下文预算和性能 |
| 分页元数据 | `count` = 当前页，`has_more` + `next_cursor` | 模糊的 `count` | agent 可正确分支 |
| 写操作 | 默认确认 + `--yes` | 默认执行 | 安全默认 |
| 机器格式 | 稳定 schema + `schema_version` + 弃用元数据 | 自由 JSON | 长期兼容 |
| 错误与成功 | 同一 `type` 信封 + `meta.schema_version` | 裸错误对象 | 一套解析器处理所有输出 |
| schema | 索引 + 按需详情 | 一次返回全部 | 控制首次发现成本 |
| 人类体验 | 渲染层降级 | 为 agent 删掉 | 两类用户都要好用 |
| 进度/提示 | stderr / `/dev/tty` | stdout | 不污染数据 |
| 平台 | POSIX + WSL | 原生 Windows v1 | 避免半套信号/路径契约 |
