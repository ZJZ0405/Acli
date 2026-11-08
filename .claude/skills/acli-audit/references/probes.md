# 行为探针

必须在 **bash** 下运行；把命令替换成被测项目的真实命令。所有探针都带硬超时，避免审计过程本身挂起。

```bash
if [ -z "${BASH_VERSION:-}" ]; then
  echo "probes.md requires bash" >&2
  return 1 2>/dev/null || exit 1
fi

# 解析成绝对路径：后续探针会切换目录
CLI=${CLI:-./acli}
CLI=$(cd "$(dirname "$CLI")" && pwd)/$(basename "$CLI")
export CLI

# -k 2：SIGTERM 后 2 秒仍未退出则 SIGKILL，防止忽略 SIGTERM 的进程挂住审计
TO=(timeout -k 2 10)

# 非交互 bash 会让后台任务忽略 SIGINT/SIGQUIT；开启 job control 才能测默认信号处理
set -m

READ=(project list)
GET=(project get prj_123)
MISSING=(project get prj_does_not_exist)
CREATE=(project create --name acli-probe)
DELETE=(project delete prj_123)
LONG=(job wait job_123)          # 换成一个会持续一段时间的命令
```

需要 `jq`；没有时用 `python3 -m json.tool` 替代。不要用 `set -e`，探针本来就会产生非零退出码。

## P1 非 TTY 不挂起（NB-1）

```bash
"${TO[@]}" "$CLI" "${READ[@]}" </dev/null >/tmp/p1.out 2>/tmp/p1.err
echo "exit=$? stdout=$(wc -c </tmp/p1.out) stderr=$(wc -c </tmp/p1.err)"
```

期望：退出码不是 124（124 表示挂起），通常为 0。

## P2 管道不改变格式（OUT-14）

```bash
"${TO[@]}" "$CLI" "${READ[@]}" 2>/tmp/p2.err | head -n 3 | cat -v
```

期望：人类文本，无 ANSI 转义，不突然变成 JSON。

## P3 JSON 成功信封（OUT-1）

```bash
"${TO[@]}" "$CLI" "${READ[@]}" --json >/tmp/p3.out 2>/tmp/p3.err
jq -e '
  .type == "result" and
  (.data != null) and
  (.meta.schema_version == "1")
' /tmp/p3.out
```

## P4 错误信封与空 stdout（OUT-2、OUT-3、ERR-1）

```bash
"${TO[@]}" "$CLI" "${MISSING[@]}" --json >/tmp/p4.out 2>/tmp/p4.err
code=$?
echo "exit=$code stdout=$(wc -c </tmp/p4.out)"
jq -e '
  .type == "error" and
  .error.code == "not_found" and
  .error.retryable == false and
  .meta.schema_version == "1"
' /tmp/p4.err
```

期望：退出 3，stdout 为空，stderr 是合法错误信封。

## P5 非交互缺少 `--yes`（NB-9、SEC-10）

```bash
ACLI_YES=1 "${TO[@]}" "$CLI" "${DELETE[@]}" --json </dev/null >/tmp/p5.out 2>/tmp/p5.err
echo "exit=$?"
jq -e '.error.code == "confirmation_required"' /tmp/p5.err
```

期望：在数秒内退出 12；`ACLI_YES` 无效；stdout 为空。

## P6 PTY 确认超时（NB-2）

Linux（util-linux）：

```bash
cmd=$(printf '%q ' "$CLI" "${DELETE[@]}")
ACLI_PROMPT_TIMEOUT=2s "${TO[@]}" script -qec "$cmd" /dev/null >/tmp/p6.out 2>&1
echo "exit=$?"
```

macOS：

```bash
ACLI_PROMPT_TIMEOUT=2s "${TO[@]}" script -q /dev/null "$CLI" "${DELETE[@]}" >/tmp/p6.out 2>&1
echo "exit=$?"
```

期望：约 2 秒后退出 12，而不是无限等待。`printf '%q'` 保证带空格或元字符的参数不会被 `script -c` 重新拆分。

## P7 NDJSON 帧（OUT-4、OUT-5）

```bash
"${TO[@]}" "$CLI" "${READ[@]}" --output=ndjson >/tmp/p7.ndjson 2>/tmp/p7.err
jq -e . /tmp/p7.ndjson >/dev/null
tail -n 1 /tmp/p7.ndjson | jq -e '.type == "end"'
head -n 1 /tmp/p7.ndjson | jq -e '.type == "item"'
```

期望：每行合法；首帧 `item`；末帧 `end`。

## P8 失败流不发 `end`（OUT-5）

```bash
"${TO[@]}" "$CLI" "${READ[@]}" --output=ndjson --cursor invalid >/tmp/p8.ndjson 2>/tmp/p8.err
code=$?
echo "exit=$code"
if grep -q '"type":"end"' /tmp/p8.ndjson 2>/dev/null || jq -e 'select(.type == "end")' /tmp/p8.ndjson >/dev/null 2>&1; then
  echo "FAIL: end frame on error"
else
  echo "ok: no end"
fi
```

期望：非零退出，无 `end`。这是「请求前失败」的近似；要验证流中途失败，需要对测试服务器注入断连。

## P9 EPIPE（OUT-15、PROC-1）

```bash
"${TO[@]}" "$CLI" "${READ[@]}" --all --output=ndjson 2>/tmp/p9.err | head -n 1 >/dev/null
status=("${PIPESTATUS[@]}")
echo "cli=${status[0]} head=${status[1]}"
if grep -qi 'broken pipe\|EPIPE' /tmp/p9.err; then echo "FAIL: broken pipe error"; else echo "ok: no broken pipe error"; fi
```

期望：`cli=0`；stderr 无 broken pipe 错误。输出很少时可能不会真正触发 EPIPE；需要多页/大结果集才能稳定覆盖。

## P10 信号退出码（PROC-1、PROC-2）

```bash
"$CLI" "${LONG[@]}" >/tmp/p10.out 2>/tmp/p10.err &
pid=$!
( sleep 12; kill -KILL "$pid" 2>/dev/null ) &
watchdog=$!
sleep 1
if kill -0 "$pid" 2>/dev/null; then
  kill -INT "$pid"
else
  echo "FAIL: LONG command exited before signal"
fi
wait "$pid"; echo "SIGINT exit=$?"
kill "$watchdog" 2>/dev/null || true
pkill -P "$watchdog" 2>/dev/null || true
wait "$watchdog" 2>/dev/null || true

"$CLI" "${LONG[@]}" >/tmp/p10b.out 2>/tmp/p10b.err &
pid=$!
( sleep 12; kill -KILL "$pid" 2>/dev/null ) &
watchdog=$!
sleep 1
if kill -0 "$pid" 2>/dev/null; then
  kill -TERM "$pid"
else
  echo "FAIL: LONG command exited before signal"
fi
wait "$pid"; echo "SIGTERM exit=$?"
kill "$watchdog" 2>/dev/null || true
pkill -P "$watchdog" 2>/dev/null || true
wait "$watchdog" 2>/dev/null || true
```

期望：130 / 143。若命令先结束，换更长或可控的命令。

## P11 `--fields` 与分页 meta（OUT-8、OUT-9）

```bash
"${TO[@]}" "$CLI" "${READ[@]}" --json --fields id >/tmp/p11.out 2>/tmp/p11.err
jq -e '.data | all(keys == ["id"])' /tmp/p11.out
"${TO[@]}" "$CLI" "${READ[@]}" --json >/tmp/p11b.out 2>/tmp/p11b.err
jq -e '.meta | has("count") and has("has_more") and has("next_cursor")' /tmp/p11b.out

"${TO[@]}" "$CLI" "${READ[@]}" --json --fields not_a_field >/tmp/p11c.out 2>/tmp/p11c.err
echo "unknown-field exit=$?"
jq -e '.error.code == "unknown_field"' /tmp/p11c.err
```

期望：字段裁剪生效；未知字段退出 2；分页 meta 三字段存在。

## P12 cursor 绑定（OUT-9、ERR-1）

```bash
"${TO[@]}" "$CLI" "${READ[@]}" --json --limit 1 >/tmp/p12.page 2>/tmp/p12.err
has_more=$(jq -r '.meta.has_more' /tmp/p12.page)
cursor=$(jq -r '.meta.next_cursor' /tmp/p12.page)
if [ "$has_more" != "true" ] || [ "$cursor" = "null" ] || [ -z "$cursor" ]; then
  echo "SKIP P12: need a multi-page fixture (has_more=$has_more next_cursor=$cursor)"
else
  "${TO[@]}" "$CLI" "${READ[@]}" --json --cursor "$cursor" --limit 1 >/tmp/p12b.out 2>/tmp/p12b.err
  echo "reuse exit=$?"
  "${TO[@]}" "$CLI" "${READ[@]}" --json --cursor "$cursor" --fields id >/tmp/p12c.out 2>/tmp/p12c.err
  echo "changed-params exit=$?"
  jq -e '.error.code == "invalid_cursor"' /tmp/p12c.err
fi
```

期望：复用 cursor 退出 0；改变 `--fields` 后返回 `invalid_cursor`，退出 2。

## P13 凭据不泄漏（SEC-1、SEC-2）

```bash
ACLI_TOKEN=SENTINEL_SECRET_123 "${TO[@]}" "$CLI" "${READ[@]}" --json --debug >/tmp/p13.out 2>/tmp/p13.err
if grep -q 'SENTINEL_SECRET_123' /tmp/p13.out /tmp/p13.err; then
  echo "FAIL: token leaked"
else
  echo "ok: token redacted"
fi
```

期望：无泄漏；错误详情和 debug 文档也不包含该值。

## P14 `--dry-run` 无副作用（SEC-11）

```bash
"${TO[@]}" "$CLI" "${CREATE[@]}" --dry-run --json >/tmp/p14.plan 2>/tmp/p14.err
jq -e '.meta.dry_run == true and .data.plan.mode != null' /tmp/p14.plan
"${TO[@]}" "$CLI" "${READ[@]}" --json >/tmp/p14.list 2>/tmp/p14.err
jq -e --arg n acli-probe '.data | all(.name != $n)' /tmp/p14.list
```

期望：计划可解析；列表中不存在探针创建的资源。跑完清理测试数据。

## P15 help 与 schema 一致（DISC-3）

```bash
while read -r line; do
  read -ra argv <<< "$line"
  "${TO[@]}" "$CLI" "${argv[@]}" --help >/dev/null 2>&1 || echo "missing help: $line"
done < <("${TO[@]}" "$CLI" schema | jq -r '.data[].path | join(" ")')
```

期望：无输出。`--help` 中的 flag 与 `schema <path>` 的 flags 也应一致，可人工抽查。

## P16 启动无网络（PROC-8）

```bash
HTTPS_PROXY=http://127.0.0.1:9 HTTP_PROXY=http://127.0.0.1:9 \
  "${TO[@]}" "$CLI" version --json >/tmp/p16.out 2>/tmp/p16.err
echo "exit=$?"
```

期望：快速成功；若命令启动即访问网络，会因代理不可用失败或明显变慢。

## P17 项目配置安全（SEC-8）

```bash
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
(
  cd "$tmp" || exit 1
  printf 'endpoint = "https://evil.example"\n[headers]\nAuthorization = "Bearer EVIL"\n' > .acli.toml
  "${TO[@]}" "$CLI" config list --json >/tmp/p17.out 2>/tmp/p17.err
)
jq '.' /tmp/p17.out
grep -iE 'endpoint|header|authorization' /tmp/p17.err || true
```

期望：项目配置中的 endpoint 被忽略并警告，来源不是 project。`CLI` 已解析为绝对路径，切换目录后仍可执行。若 `config list` 结构不同，人工核对。

## P18 原生 Windows（PROC-7）

在 Windows 原生环境运行：

```bash
"${TO[@]}" "$CLI" version --json
```

期望：明确以 `unsupported_platform`（退出 1）失败，而不是崩溃或半可用；WSL 中应按 Linux 正常工作。

## P19 认证生命周期（SEC-13、SEC-14、SEC-15）

```bash
"${TO[@]}" "$CLI" auth login </dev/null >/tmp/p19.out 2>/tmp/p19.err
echo "non-interactive login exit=$? (expect 4)"
if grep -qi 'token\|secret' /tmp/p19.err; then echo "check stderr for token leak"; fi

"${TO[@]}" "$CLI" auth status --json >/tmp/p19s.out 2>/tmp/p19s.err
echo "status exit=$?"

"${TO[@]}" "$CLI" auth logout >/tmp/p19l.out 2>/tmp/p19l.err
echo "logout exit=$?"
"${TO[@]}" "$CLI" auth logout >/tmp/p19l2.out 2>/tmp/p19l2.err
echo "logout again exit=$? (expect 0)"
```

期望：非交互 login 不启动浏览器、不等待，退出 4；status 不输出 token；重复 logout 成功。

## 探针记录

每个探针至少记录：

```
probe: P5
cmd: ...
exit: 12
stdout_bytes: 0
stderr_head: {"type":"error","error":{"code":"confirmation_required",...}}
verdict: pass
```

没有实际运行证据的项在报告中标记为「未验证」。
