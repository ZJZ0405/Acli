# 架构与模块边界

## 1. 数据流

```
argv
  ↓
parse        → 语法、未知 flag、did-you-mean
  ↓
validate     → 冲突、适用性、取值、认证前置
  ↓
plan         → dry-run 分支 → 确认门禁 → 幂等键准备
  ↓
execute      → API 调用、重试、job 创建/等待
  ↓
Result       → data + meta（纯数据，无格式）
  ↓
┌───────────────┬────────────────┬─────────────────┐
│ HumanRenderer │ JSONRenderer   │ NDJSONRenderer  │
│ stdout=文本   │ stdout=信封    │ stdout=逐行帧   │
│ stderr=诊断   │ stderr=JSON    │ stderr=JSON     │
└───────────────┴────────────────┴─────────────────┘
```

## 2. 推荐目录结构

语言无关；按职责命名，不要求逐字照搬。

```
cmd/<tool>/main.go          # 仅装配依赖、调用 cli.Run、设置退出码
internal/cli/               # 解析、flag 模型、冲突校验、registry
internal/core/              # 命令实现；返回 Result / CliError
internal/render/            # human.go / json.go / ndjson.go
internal/schema/            # 从 registry 生成 schema 与 help
internal/config/            # 发现、优先级、来源追踪、原子写入
internal/auth/              # token 来源、keyring、刷新
internal/api/               # HTTP 客户端、重试、幂等键、错误映射
internal/jobs/              # job 生命周期
internal/testutil/          # 契约测试夹具
```

### 边界规则

- `cli` 不知道业务；`core` 不知道输出格式；`render` 不知道网络。
- `api` 只返回领域错误或 `CliError`，不做退出。
- `main` 只做装配和 `os.Exit(code)`，不处理业务。
- 任何包都不应读取 `os.Args`；argv 只由 `cli` 解析一次。

## 3. 命令清单（registry）

单一清单是 help、schema、解析器、completion 的唯一事实来源。

每个命令条目至少包含：

```text
path              ["project","create"]
summary           "Create a project"
kind              read | write
destructive       bool
requires_auth     bool
requires_confirmation bool
supports_dry_run  bool
dry_run_modes     ["client","server"]
supports_jobs     bool
default_wait      bool
args              []ArgSpec
flags             []FlagSpec
output_schema     JSON Schema（同步/job 用 oneOf）
error_codes       []string
exit_codes        []int
examples          []string
stability         stable | beta | experimental
deprecated        bool
replaced_by       string?
removal_version   string?
handler           func(ctx, Input) (Result, *CliError)
```

生成物：

- 解析器：从 `args`/`flags` 构建。
- `--help`：从 `summary`/`args`/`flags`/`examples` 构建。
- `schema`：索引取 `path`/`summary` 等；详情取全量。
- completion：从 path 和 flag 生成。

### 一致性测试

- 遍历 registry：每个 `path` 可执行，`--help` 成功，`schema <path>` 成功。
- 每个 `examples` 中的命令能被解析器接受（不实际执行）。
- `error_codes` 与实现中可能返回的错误码一致。

## 4. 渲染器接口

```text
Renderer.RenderResult(result) error
Renderer.RenderError(err) error
Renderer.RenderWarning(warning) error
Renderer.RenderProgress(event) error   // 仅 stderr/TTY
```

约束：

- 渲染器只写指定的 writer；测试时注入 buffer。
- `JSONRenderer` 永远输出单个信封；`NDJSONRenderer` 只在列表/流命令使用。
- `HumanRenderer` 负责颜色、表格、宽度降级；机器渲染器不依赖终端宽度。
- 渲染失败（如 EPIPE）必须以可识别的错误返回，由上层决定退出 0。

## 5. 依赖注入与可测试性

- `stdin` / `stdout` / `stderr` / `isTTY` / `/dev/tty` 通过上下文注入，不直接读全局。
- 时钟、随机数、HTTP client、keyring 通过接口注入。
- 退出码由 `Run()` 返回 int；`main` 负责 `os.Exit`，便于进程内测试。
- 信号处理在 `Run()` 外层安装，把取消传进 context。

## 6. 反模式

| 反模式 | 后果 | 正确做法 |
|---|---|---|
| 在 core 里 `print` / `console.log` | 输出无法测试、污染 stdout | 返回 Result |
| 到处 `os.Exit` | 无法清理、无法测试 | 错误向上返回，集中退出 |
| 用 `isatty(stdout)` 决定数据格式 | 管道行为突变 | 只影响渲染装饰 |
| 每个命令手写 JSON | 信封漂移 | 统一渲染器 |
| 手写 help 和 schema 两份 | 长期不一致 | registry 单一来源 |
| 全局可变状态 | 并发和测试污染 | 显式依赖注入 |
| 在解析阶段发网络请求 | 启动副作用、慢 | 只在 execute 阶段请求 |
