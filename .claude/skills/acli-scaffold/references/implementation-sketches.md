# 实现草图（Go）

这些片段用于说明不变量，不是可直接复制的完整实现。换语言时保持同样的类型和数据形状。

## 1. 核心类型

```go
package core

type Result struct {
    Data any
    Meta Meta
}

type Meta struct {
    RequestID string
    DryRun    bool
}

type CliError struct {
    Code              string         // snake_case，稳定
    Message           string
    Hint              string
    Retryable         bool
    RequestID         string
    RetryAfterSeconds *int           // 429/503 的建议等待时间
    Details           map[string]any // 不得包含密钥
}
```

约束：

- `Result` 不包含渲染格式。
- `CliError` 不包含退出码；退出码由集中函数计算。
- 错误构造器负责脱敏：`details` 不接收 token、Cookie、Authorization。

## 2. 信封与 JSON 渲染

```go
type jsonEnvelope struct {
    Type    string       `json:"type"`
    Data    any          `json:"data,omitempty"`
    Error   *jsonError   `json:"error,omitempty"`
    Warning *jsonWarning `json:"warning,omitempty"`
    Meta    any          `json:"meta"` // jsonMeta 或 listMeta；列表必须用 listMeta
}

type jsonMeta struct {
    SchemaVersion string `json:"schema_version"`
    RequestID     string `json:"request_id,omitempty"`
    DryRun        *bool  `json:"dry_run,omitempty"`
}

type jsonWarning struct {
    Code           string `json:"code"`
    Message        string `json:"message"`
    ReplacedBy     string `json:"replaced_by,omitempty"`
    RemovalVersion string `json:"removal_version,omitempty"`
}

type jsonError struct {
    Code              string         `json:"code"`
    Message           string         `json:"message"`
    Hint              string         `json:"hint,omitempty"`
    Retryable         bool           `json:"retryable"`
    RequestID         string         `json:"request_id,omitempty"`
    RetryAfterSeconds *int           `json:"retry_after_seconds,omitempty"`
    Details           map[string]any `json:"details,omitempty"`
}

type listMeta struct {
    SchemaVersion    string  `json:"schema_version"`
    RequestID        string  `json:"request_id,omitempty"`
    Count            int     `json:"count"`
    HasMore          bool    `json:"has_more"`
    NextCursor       *string `json:"next_cursor"` // 始终存在；最后一页为 null
    Truncated        bool    `json:"truncated"`
    OmittedItems     *int    `json:"omitted_items,omitempty"`
    Partial          bool    `json:"partial,omitempty"`
    DryRun           *bool   `json:"dry_run,omitempty"`
    IdempotentReplay bool    `json:"idempotent_replay,omitempty"`
}
```

要点：

- `next_cursor` 不要加 `omitempty`，否则最后一页会丢字段。
- 列表 `data` 为空时必须是 `[]`，不能是 `nil`（`nil` 切片序列化为 `null`）。
- 错误只写 stderr；成功只写 stdout。

## 3. 退出码集中映射

```go
func ExitCode(e *core.CliError) int {
    switch e.Code {
    case "usage", "unknown_flag", "flag_not_applicable", "unknown_field",
        "conflicting_flags", "invalid_cursor", "cursor_expired":
        return 2
    case "unsupported_platform", "unsupported_server_version",
        "internal_error", "tls_error", "job_failed":
        return 1
    case "not_found":
        return 3
    case "unauthenticated":
        return 4
    case "permission_denied":
        return 5
    case "conflict", "idempotency_key_reuse":
        return 6
    case "rate_limited":
        return 7
    case "timeout", "outcome_unknown":
        return 8
    case "canceled":
        return 9
    case "partial_failure":
        return 10
    case "unavailable", "network_error":
        return 11
    case "confirmation_required":
        return 12
    case "interrupted":
        return 130
    case "terminated":
        return 143
    default:
        return 1
    }
}
```

`main` 只负责：

```go
func main() {
    signal.Ignore(syscall.SIGPIPE) // 让 stdout EPIPE 变成可处理错误，而不是 141
    code := cli.Run(os.Args[1:], os.Stdin, os.Stdout, os.Stderr)
    os.Exit(code)
}
```

## 4. flag 冲突校验

先解析成一个结构体，再按固定顺序校验；不要在执行阶段才发现冲突。

```go
type Flags struct {
    Output      string
    JSON        bool
    Quiet       bool
    Verbose     bool
    Debug       bool
    NoInput     bool
    Interactive bool
    All         bool
    Cursor      string
    MaxBytes    int64
    DryRun      string // "" | "client" | "server"
    Yes         bool
    Token       string
    TokenFile   string
    TokenStdin  bool
    FromFile    string
}

func ValidateFlags(f Flags) *core.CliError {
    conflict := func(msg string) *core.CliError {
        return &core.CliError{Code: "conflicting_flags", Message: msg, Retryable: false}
    }
    if f.JSON && f.Output != "" && f.Output != "json" {
        return conflict("--json conflicts with --output")
    }
    if f.Quiet && (f.Verbose || f.Debug) {
        return conflict("--quiet conflicts with --verbose/--debug")
    }
    if f.NoInput && f.Interactive {
        return conflict("--no-input conflicts with --interactive")
    }
    if f.All && f.Cursor != "" {
        return conflict("--all conflicts with --cursor")
    }
    if f.All && f.MaxBytes > 0 {
        return conflict("--all conflicts with --max-bytes")
    }
    if countTrue(f.Token != "", f.TokenFile != "", f.TokenStdin) > 1 {
        return conflict("only one token source is allowed")
    }
    if countTrue(f.FromFile == "-", f.TokenStdin, f.TokenFile == "-") > 1 {
        return conflict("only one stdin consumer is allowed")
    }
    return nil
}
```

输出模式解析：

```go
func EffectiveOutput(f Flags, commandPath []string) string {
    if f.JSON {
        return "json"
    }
    if f.Output != "" {
        return f.Output
    }
    if env := os.Getenv("ACLI_OUTPUT"); env != "" {
        return env // flag > env > 默认值
    }
    if isSchemaCommand(commandPath) {
        return "json" // 唯一默认 JSON 例外
    }
    return "human"
}
```

## 5. 交互门禁与确认超时

```go
type IO struct {
    Stdin      io.Reader
    Stdout     io.Writer
    Stderr     io.Writer
    StdinIsTTY bool
    TTY        *os.File // /dev/tty；打不开时为 nil
}

func PromptAllowed(io IO, noInput, interactive bool) (bool, *core.CliError) {
    if noInput && interactive {
        return false, &core.CliError{Code: "conflicting_flags", Message: "--no-input conflicts with --interactive"}
    }
    if noInput {
        return false, nil
    }
    if interactive {
        if !io.StdinIsTTY && io.TTY == nil {
            return false, &core.CliError{Code: "usage", Message: "--interactive requires a terminal"}
        }
        return true, nil
    }
    if os.Getenv("ACLI_NO_INPUT") == "1" {
        return false, nil
    }
    return io.StdinIsTTY, nil
}
```

确认读取要有硬超时，EOF/超时都视为否：

```go
func Confirm(io IO, prompt string, timeout time.Duration) (bool, *core.CliError) {
    out := io.Stderr
    reader := io.Stdin
    if io.TTY != nil {
        out = io.TTY
        reader = io.TTY
    }
    fmt.Fprintln(out, prompt)

    answer := make(chan bool, 1)
    go func() {
        line, err := bufio.NewReader(reader).ReadString('\n')
        if err != nil {
            answer <- false
            return
        }
        s := strings.ToLower(strings.TrimSpace(line))
        answer <- s == "y" || s == "yes"
    }()

    select {
    case ok := <-answer:
        if !ok {
            return false, confirmationRequired()
        }
        return true, nil
    case <-time.After(timeout):
        return false, confirmationRequired()
    }
}

func confirmationRequired() *core.CliError {
    return &core.CliError{
        Code:      "confirmation_required",
        Message:   "operation requires confirmation in non-interactive mode",
        Hint:      "Re-run with --yes to confirm.",
        Retryable: false,
    }
}
```

注意：超时后进程应尽快退出，避免遗留读 goroutine；如需长驻，关闭 TTY fd 解除阻塞。

## 6. NDJSON 写入

```go
type NDJSONWriter struct {
    enc *json.Encoder
}

func NewNDJSONWriter(w io.Writer) *NDJSONWriter {
    return &NDJSONWriter{enc: json.NewEncoder(w)} // Encode 自带换行
}

func (n *NDJSONWriter) Item(data any) error {
    return n.enc.Encode(map[string]any{"type": "item", "data": data})
}

func (n *NDJSONWriter) End(meta listMeta) error {
    return n.enc.Encode(map[string]any{"type": "end", "meta": meta})
}
```

上层规则：

```go
for _, item := range items {
    if err := nw.Item(item); err != nil {
        if errors.Is(err, syscall.EPIPE) {
            return 0 // 下游关闭，正常结束
        }
        return 1
    }
}
if err := nw.End(meta); err != nil {
    if errors.Is(err, syscall.EPIPE) {
        return 0
    }
    return 1
}
```

失败路径不要调用 `End`；批量部分失败要调用 `End` 并设置 `Partial: true`，在 stderr 输出汇总错误，然后退出 10。

所有输出路径（human/json/ndjson）都必须经过同一个写错误处理：

```go
func WriteExitCode(err error, fd int) int {
    if err == nil {
        return 0
    }
    if errors.Is(err, syscall.EPIPE) {
        if fd == int(os.Stdout.Fd()) {
            return 0 // 下游正常关闭
        }
        return 141 // 其他 fd 的异常管道关闭
    }
    return 1
}
```

`signal.Ignore(syscall.SIGPIPE)` 只是让 Go 把 EPIPE 作为错误返回，不能只忽略而不再做 fd 级映射；human/json 路径也必须使用同一个处理函数。

## 7. registry 与 schema

```go
type Command struct {
    Path      []string
    Summary   string
    Kind      string // read | write
    Destructive bool
    RequiresAuth bool
    RequiresConfirmation bool
    SupportsDryRun bool
    DryRunModes []string
    SupportsJobs bool
    DefaultWait bool
    Pagination  string // none | cursor（设计期属性）
    Idempotency string // none | by_id | key（设计期属性）
    OutputShape string // object | list | sync_or_job | plan（设计期属性）
    Args      []ArgSpec
    Flags     []FlagSpec
    OutputSchema map[string]any
    ErrorCodes []string
    ExitCodes  []int
    Examples   []string
    Stability  string
    Deprecated bool
    ReplacedBy *string
    RemovalVersion *string
    Handler    func(context.Context, Input) (core.Result, *core.CliError)
}

var registry = []Command{ /* ... */ }
```

- 解析器、`--help`、`schema`、completion 全部遍历 `registry`。
- `schema` 默认只输出索引；按 path 输出详情。
- `pagination` / `idempotency` / `output_shape` 是设计期属性，用于实现和测试；schema 只暴露 `DESIGN.md` §4.9 定义的字段。
- 增加一致性测试：registry 中每个 path 都存在、`--help` 可用、schema 可生成。

## 8. dry-run 计划

```go
type Plan struct {
    DryRun  bool          `json:"dry_run"`
    Mode    string        `json:"mode"` // client | server
    Summary PlanSummary   `json:"summary"`
    Actions []PlanAction  `json:"actions"`
}

type PlanSummary struct {
    Create int `json:"create"`
    Update int `json:"update"`
    Delete int `json:"delete"`
}

type PlanAction struct {
    Op       string         `json:"op"`
    Resource string         `json:"resource"`
    ID       string         `json:"id,omitempty"`
    Name     string         `json:"name,omitempty"`
    Changes  map[string]any `json:"changes"` // 无变化时为 null
}
```

- 机器模式放在 `data.plan`，`meta.dry_run=true`。
- 人类模式结尾必须有 `Dry run (<mode>): no changes made.`。
- dry-run 不要求 `--yes`；不产生任何写请求。

## 9. 测试接缝

- `Run(args, stdin, stdout, stderr) int` 不调用 `os.Exit`，便于单元测试。
- `isTTY`、`/dev/tty`、时钟、HTTP client 通过 `IO` / 依赖注入替换。
- 渲染器写入注入的 buffer，断言字节而不是终端状态。
- 契约测试直接运行构建后的二进制，见 `acli-contract-test`。
