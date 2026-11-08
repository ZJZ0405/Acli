---
name: acli-contract-test
description: Write and run executable black-box contract tests for an Acli-compatible CLI — machine output envelopes, exit codes, non-interactive safety, NDJSON streaming, pagination, signals, secret redaction, and CI schema gates. Use when adding conformance tests, PTY tests, golden files, or a release gate for a CLI (契约测试、验收测试、PTY 测试、CI 门禁).
---

# Acli Contract Test

用黑盒方式验证 CLI 是否遵守 Acli 契约。测试直接运行构建后的二进制，不依赖内部实现。

## 原则

- **三元组断言**：每个用例同时断言退出码、stdout、stderr；只断言其中一个会漏掉契约违规。
- **黑盒**：通过 argv、环境变量、stdin 驱动；不 import 被测代码。
- **确定性**：固定 `TZ=UTC`、`LC_ALL=C.UTF-8`、`NO_COLOR=1`、`COLUMNS=80`；不依赖网络和真实时间。
- **短超时**：确认提示用 `ACLI_PROMPT_TIMEOUT=1s`；每个进程级用例都有硬超时。
- **无真实副作用**：使用一次性测试账号/资源；破坏性用例只针对夹具数据。
- **故障可注入**：流中途失败、慢写、断连需要本地假服务器或代理；不要跳过这些用例。

## 准备

1. 构建或定位被测二进制，设置 `ACLI_BIN`。
2. 根据项目替换测试命令参数（见 `references/pytest-harness.md` 顶部的环境变量）。
3. 准备测试数据：至少一个存在的资源、一个不存在的 ID、一个可删除的一次性资源、一个长任务。
4. 如果要测 401/403/429/5xx，启动本地假 API 或使用测试 endpoint。

## 工作流

1. **建立 harness**：复制 `references/pytest-harness.md`，先跑通成功/错误两个用例。
2. **按矩阵补齐**：从 `references/test-matrix.md` 选取适用项，逐条实现；每条标注矩阵 ID。
3. **补故障注入**：流中途断连、慢响应、429/503、在途写信号。
4. **补人类输出 golden**：固定环境变量，快照 human 输出。
5. **接入 CI**：按 `references/ci-gate.md` 增加 schema diff、golden 和 PTY 测试。
6. **报告未覆盖项**：矩阵中没有实现的用例要明确列出，不能默认通过。

## 必测类别

| 类别 | 最低要求 |
|---|---|
| 信封与退出码 | 成功、错误、部分失败、确认失败各至少一条 |
| 非交互 | 无 TTY 不挂起；PTY 确认超时；`--yes` 门禁 |
| NDJSON | 每行合法；成功有 `end`；硬失败无 `end`；部分失败有 `meta.partial=true` 的 `end` 和 stderr 汇总错误；EPIPE 退出 0 |
| 分页 | `count`/`has_more`/`next_cursor`；cursor 绑定主体/命令/过滤/`--fields`；过期/非法 |
| 安全 | token 不出现在任何输出；项目配置不能覆盖 endpoint/proxy/CA/log_file/header；dry-run 无副作用 |
| 信号 | SIGINT 130；SIGTERM 143；在途写的结果未知语义 |
| 认证 | 非交互 login 不阻塞；status 不泄漏 token；logout 幂等；401 不自动交互刷新 |
| 发现 | schema 默认 JSON；每个命令的 schema 详情与 `--help` 一致；未知命令 did-you-mean |
| 按命令覆盖 | 每个 schema 命令至少一个 schema 驱动的 smoke test；关键命令按矩阵补全 |

## 测试规则

- 每个 schema 命令至少一个 smoke test（`--help`、schema 详情、flag 一致性）；维护 harness 中的 `COVERED_COMMANDS`，新增命令未登记测试时 D6 失败；关键命令再按矩阵补全。
- 不要断言完整的人类文案；用子串或 golden 文件，并同时断言退出码。
- JSON 断言用 `json.loads`，不要用字符串匹配。
- PTY 测试必须在超时后 kill 子进程，避免测试套件挂起。
- 信号测试只在长任务/可控命令上做；先确认进程仍在运行再发信号。
- 每个测试使用独立临时目录和独立资源名，避免并发污染。
- 失败时输出完整三元组，便于定位是契约问题还是测试问题。
- 不把 flaky 测试标记为 skip 后忘记；要么修夹具，要么记录未覆盖。

## CI 门禁

至少包含：

1. 全量契约测试（Linux 必跑；macOS 发布前必跑）。
2. PTY 测试（非 TTY CI 环境必须显式创建 PTY）。
3. schema 快照与上一发布版本比较，禁止破坏性 diff。
4. human 输出 golden 比较。
5. `--help` 与 `schema` 一致性检查。
6. 代理环境和 `NO_COLOR` / 窄终端用例。

## 参考

- `references/test-matrix.md`：用例矩阵与期望结果。
- `references/pytest-harness.md`：可直接使用的 pytest 黑盒 harness。
- `references/ci-gate.md`：schema diff、golden 与 CI 配置。
- 仓库根目录 `DESIGN.md` §7 是验收项权威来源。
