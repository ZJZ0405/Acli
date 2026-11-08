---
name: acli-scaffold
description: Scaffold or refactor a CLI implementation to follow the Acli dual-audience contract — core/renderer separation, typed results, JSON/NDJSON envelopes, exit codes, global flags, schema command, and non-interactive safety. Use when implementing a new Acli-friendly CLI or adding Acli-compatible behavior to an existing one (脚手架、结果渲染、退出码、JSON 输出、schema、双输出、命令行实现).
---

# Acli Scaffold

把一个命令契约落成可运行、可测试的 CLI 骨架。核心目标：**一套内核，两个渲染层**，内核永远不直接打印。

## 前置条件

- 已有命令契约（来自 `acli-design` 或用户提供）。
- 没有契约时，先按 `../acli-design/references/command-matrix.md` 推导最小命令矩阵（该文件不可用时按 `DESIGN.md` §4.1–§4.2 推导），再开始实现。
- 先读仓库根目录 `DESIGN.md` 的 §2–§4；本 skill 的 references 是实施摘要。

## 架构不变量

1. **解析、校验、执行、渲染分离**：`parser → validator → planner → executor → Result → renderer`。
2. **内核不打印**：命令实现返回 `Result` 或 `CliError`，所有输出经过渲染层。
3. **内核不读 TTY 做业务决策**：TTY 只决定渲染和是否允许交互。
4. **单一命令清单**：同一个 registry/manifest 生成解析器、`--help`、`schema` 和 completion。
5. **错误即类型**：错误码、退出码、`retryable` 在错误类型上显式携带，不靠解析文案。

## 实施顺序

按依赖顺序推进，每一步都可独立验证：

1. **定义核心类型**：`Result`、`CliError`、`Meta`、`Envelope`；成功/错误/警告共用 `type` 信封。
2. **实现退出码映射**：一个集中函数把 `CliError.code` 映射到退出码；禁止散落的 `exit()`。
3. **实现 flag 解析与冲突校验**：区分全局 flag 与命令级 flag；未知 flag、冲突 flag、不适用 flag 在执行前失败。
4. **实现渲染层**：`HumanRenderer`、`JSONRenderer`、`NDJSONRenderer`；stdout/stderr 分离。
5. **实现交互门禁**：stdin TTY 判定、`--no-input`/`--interactive`、`/dev/tty`、确认硬超时、退出 12。
6. **打通一个纵向切片**：选一个读命令和一个写命令，端到端实现解析→执行→渲染→退出码。
7. **生成 `schema` 和 `version`**：从 registry 输出索引和单命令详情；`--help` 与 schema 共用元数据；`version --json` 输出版本、commit、平台、schema 版本和服务端支持区间。
8. **加入配置与认证**：配置优先级、来源追踪、项目配置安全限制、token 来源互斥与脱敏；实现 `auth login/logout/status/refresh` 和 `config list --json`。
9. **加入分页、幂等、重试、job**：cursor 编解码、`meta` 语义、幂等键、重试预算、job 生命周期。
10. **接入契约测试**：用 `acli-contract-test` 的测试矩阵作为完成门禁。

详细模块划分见 `references/architecture.md`；运行时规则见 `references/runtime-contract.md`；Go 代码草图见 `references/implementation-sketches.md`。

## 关键规则

### 输出

- 默认 `--output=human`，不随 TTY 静默切换。
- 机器模式下 stdout 只含 JSON/NDJSON；stderr 的每条诊断也是 JSON 文档。
- 非流式非零退出时 stdout 为空；`partial_failure` 例外。
- NDJSON 成功必须发 `end` 帧；硬失败或来源中断不发 `end`；批量部分失败发 `meta.partial=true` 的 `end`，在 stderr 输出汇总错误，退出 10；EPIPE 退出 0。
- `--fields` 只裁剪 `data` 顶层，不能裁掉 `meta`。

### 退出码与错误

- 集中映射 `error.code → exit code`，表见 `references/runtime-contract.md`。
- `retryable` 按操作语义计算，不按退出码猜。
- 非幂等写超时/断连且结果未知 → `outcome_unknown`，不自动重试。
- 批量部分失败 → 退出 10，不返回 0。

### 交互

- `requires_confirmation=true` 且非 dry-run 的非交互写操作，无 `--yes` 时立即退出 12，不等待、不读 stdin 负载；dry-run 不要求确认。
- 提示写 stderr 或 `/dev/tty`；硬超时默认 30 秒；EOF/超时视为否。
- 单次调用最多一个 stdin 消费者（`--from-file -`、`--token-stdin`、`--token-file -` 互斥）。
- `--yes` 只来自 argv，不读环境变量或配置。

### 配置与安全

- 优先级：flag > `ACLI_*` > 项目配置 > 用户配置 > 默认。
- 项目配置不得提供 `endpoint`、`proxy`、`ca`、`log_file`、自定义 header。
- token 来源互斥；`--token` 在 TTY 下警告；所有输出和日志脱敏。
- 配置写入原子化（临时文件 + rename）并加锁。

### 信号

- SIGINT → 130，SIGTERM → 143。
- 已完整发出的非幂等写请求有最多 5 秒宽限期等待确定响应；仍未知则 `interrupted`/`terminated` + `details.outcome_unknown=true`。
- stdout 上的 EPIPE → 停止后续输出/请求，退出 0，不写错误。

## 完成定义

- 至少一个读命令和一个写命令端到端通过；完整交付还必须覆盖契约中的全部命令，并逐个通过契约测试的相关用例。
- `--json` 成功/错误信封符合契约。
- 非交互、PTY、NDJSON、信号、分页、脱敏测试通过。
- `schema` 与 `--help` 一致，且每个命令详情包含 `DESIGN.md` §4.9 的全部字段：`args`、`flags`、`output_schema`、`error_codes`、`exit_codes`、`requires_auth`、`requires_confirmation`、`destructive`、`supports_dry_run`、`dry_run_modes`、`supports_jobs`、`default_wait`、`examples`、`stability`、`deprecated`、`replaced_by`、`removal_version`。
- `version --json`、`config list --json`、`completion`、`diagnostic` 已实现且无明显缺口。
- 通过 `acli-audit` 的 Blocker/High 项为零。

## 参考

- `references/architecture.md`：模块边界、registry、渲染器接口。
- `references/runtime-contract.md`：校验顺序、错误映射、重试、job、信号、配置。
- `references/implementation-sketches.md`：Go 类型、渲染器、退出码、交互门禁草图。
