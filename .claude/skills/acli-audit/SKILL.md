---
name: acli-audit
description: Audit an existing CLI for Acli contract compliance and produce a prioritized fix report. Use when reviewing CLI code or behavior for agent usability, dual-audience output, exit codes, non-interactive safety, secret redaction, or assessing migration readiness to Acli conventions (read-only; implementation belongs to acli-scaffold) (兼容性审查、契约审计、agent 友好性检查、CLI 代码审查).
---

# Acli Audit

审查一个已有 CLI 是否符合 Acli 契约，输出按严重度排序、带证据的修复报告。默认只读；只有用户要求时才改代码。

## 工作流

### 0. 确认范围与安全边界

- 找到构建方式和测试二进制；确认要审计的命令范围。
- 只在测试环境或一次性资源上运行破坏性探针；不要对生产数据执行 delete/update。
- 所有探针加 `timeout`；默认不发送真实凭据，必要时用测试 token。
- 记录二进制版本、平台和 commit。

### 1. 盘点命令面

先设置 `export CLI=${CLI:-./acli}`（建议用绝对路径）。命令需要 `jq`；没有时用 `python3 -m json.tool` 替代。

```bash
"$CLI" --help
"$CLI" schema 2>/dev/null | jq '.data'
"$CLI" version --json
```

若没有 `schema`，直接从 `--help` 和源码枚举命令；把「缺少 schema」记为发现项。

### 2. 静态审查

按模式扫描代码，定位结构性问题：

| 模式 | 查找 | 风险 |
|---|---|---|
| 内核直接输出 | `fmt.Print*` / `console.log` / `print(` / `println!` 在命令实现中 | 输出不可测试、污染 stdout |
| 散落退出 | 多处 `os.Exit` / `process.exit` / `sys.exit` | 退出码不一致、无法清理 |
| TTY 决定输出/业务 | `isatty(stdout/stderr)` 用于选择 JSON 格式或业务分支 | 管道行为突变。注意：`isatty(stdin)` 判断是否允许交互是契约要求的，不算缺陷 |
| 无超时读取 | `Scanln` / `input()` / `readline` 无 deadline | agent 永久阻塞 |
| 手写 JSON | 字符串拼接 JSON、无统一信封 | 字段漂移、转义错误 |
| 字符串错误 | 错误只返回文案、无稳定 code | 无法分支和重试 |
| 凭据落日志 | token/Cookie/Authorization 进入日志或错误 | 泄漏 |
| 配置优先级 | 多处合并配置、无来源追踪 | 不可预测 |
| 项目配置放行敏感键 | `.acli.toml` 可设 endpoint/proxy/CA/log_file/自定义 header | 安全劫持 |
| 环境变量控制确认 | `ACLI_YES` / 配置 `yes` | 静默绕过确认 |

记录 `file:line` 作为证据。

### 3. 行为探针

按 `references/probes.md` 执行。每个探针记录三元组：**退出码、stdout、stderr**。不要只读代码判断行为。

重点验证：

- 非交互、无 TTY、stdin 关闭时不挂起。
- PTY 中确认提示会在超时内以 12 退出。
- `--json` 成功/错误信封；错误时 stdout 为空。
- NDJSON 每行合法、成功有 `end`、硬失败无 `end`、部分失败有 `meta.partial=true` 的 `end` 和 stderr 汇总错误。
- `auth login` 非交互不启动浏览器/不等待，退出 4；`auth status --json` 不输出 token；`auth logout` 幂等。
- `| head` 退出 0。
- SIGINT/SIGTERM 退出 130/143。
- token 不出现在任何输出中。
- `--dry-run` 无副作用。
- `schema` 与 `--help` 一致。

### 4. 分级

默认级别以 `references/audit-checklist.md` 为准；下表只是摘要：

| 级别 | 判定 |
|---|---|
| **Blocker** | 非交互挂起（NB-1/2/7/9）；确认可被环境变量绕过（SEC-10）；项目配置可覆盖 endpoint/proxy/CA/header（SEC-8）；重复写/数据损坏/结果未知（ERR-3、PROC-3）；密钥泄漏（SEC-1/2）；非流式失败仍向 stdout 写数据（OUT-3/6/14） |
| **High** | 机器输出不可解析（OUT-1/2/4/5/7）；退出码或错误映射错误（ERR-1/2/5/6）；错误写 stdout；401/403 混淆；非幂等自动重试；信号退出码错误（PROC-1/2）；schema 缺失或漂移（DISC-1/2/3）；脱敏不完整 |
| **Medium** | 分页 meta/cursor 语义错误；`--fields` 可裁掉 meta；截断不可见；配置来源不可查；auth 刷新规则错误；服务端版本协商缺失 |
| **Low** | 人类输出可读性问题；颜色/宽度/提示；completion 缺失 |

分级原则：影响 agent 正确性或数据安全的优先，纯人类体验其次。

### 5. 报告

按 `references/report-template.md` 输出：

- 结论摘要：能否安全用于 agent/CI。
- 发现表：ID、级别、规则、证据、影响、建议修复。
- 修复顺序：先 Blocker，再 High；给出最小改动。
- 未验证项：明确列出无法运行或缺少环境的部分，不要假装通过。

### 6. 可选修复

用户要求修复时：

- 每次只修一个级别的问题，保持 diff 最小。
- 不顺手重构与契约无关的代码。
- 修复后重跑对应探针，并把证据更新到报告。
- 修复不得改变已发布字段、退出码映射或命令语义；需要变更时走弃用流程。

## 审查规则

- 代码审查与行为验证必须交叉：代码看起来对，不代表运行时对。
- 不把「文档写了」当作通过；以实际二进制行为为准。
- 不因为命令是内部的就放宽 Blocker/High 标准。
- 不把推测写成结论；无法验证的项标为「未验证」。

## 参考

- `references/audit-checklist.md`：按维度的检查清单与默认级别。
- `references/probes.md`：可执行探针与期望结果。
- `references/report-template.md`：报告模板。
- 仓库根目录 `DESIGN.md` §7 是验收清单的权威来源。
