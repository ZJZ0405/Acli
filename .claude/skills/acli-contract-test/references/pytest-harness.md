# 黑盒 pytest harness

复制为 `test_acli_contract.py`，按项目修改顶部命令参数。依赖只有 `pytest` 和 Python 3.10+。

```bash
ACLI_BIN=./acli \
ACLI_LIST_ARGS="project list" \
ACLI_GET_ARGS="project get prj_123" \
ACLI_GET_MISSING_ARGS="project get prj_missing" \
ACLI_CREATE_ARGS="project create --name acli-contract-test" \
ACLI_DELETE_ARGS="project delete prj_123" \
ACLI_LONG_ARGS="job wait job_123" \
ACLI_TEST_RESOURCE_NAME="acli-contract-test" \
pytest -q test_acli_contract.py
```

`ACLI_LONG_ARGS` 必须是一个会持续足够久的命令，用于信号测试。

```python
"""Acli 黑盒契约测试。"""
from __future__ import annotations

import errno
import json
import os
import pty
import select
import shlex
import signal
import subprocess
import time
from dataclasses import dataclass

import pytest

BIN = os.environ.get("ACLI_BIN")
if not BIN:
    if os.environ.get("CI"):
        raise RuntimeError("ACLI_BIN must be set in CI; refusing to skip silently")
    pytest.skip("set ACLI_BIN to the CLI binary under test", allow_module_level=True)


def _args(name: str, default: str) -> list[str]:
    return shlex.split(os.environ.get(name, default))


LIST = _args("ACLI_LIST_ARGS", "project list")
GET = _args("ACLI_GET_ARGS", "project get prj_123")
GET_MISSING = _args("ACLI_GET_MISSING_ARGS", "project get prj_missing")
CREATE = _args("ACLI_CREATE_ARGS", "project create --name acli-contract-test")
DELETE = _args("ACLI_DELETE_ARGS", "project delete prj_123")
LONG = _args("ACLI_LONG_ARGS", "job wait job_123")
RESOURCE_NAME = os.environ.get("ACLI_TEST_RESOURCE_NAME", "acli-contract-test")

BASE_ENV = {
    "TZ": "UTC",
    "LC_ALL": "C.UTF-8",
    "LANG": "C.UTF-8",
    "NO_COLOR": "1",
    "ACLI_NO_PROGRESS": "1",
    "ACLI_PROMPT_TIMEOUT": "1s",
    "COLUMNS": "80",
}


@dataclass
class Run:
    code: int
    stdout: str
    stderr: str

    def result_json(self) -> dict:
        return json.loads(self.stdout)

    def error_json(self) -> dict:
        return json.loads(self.stderr)


def run(
    *args: str,
    input: str | None = None,
    env: dict[str, str] | None = None,
    timeout: float = 10.0,
    cwd: str | None = None,
) -> Run:
    e = os.environ.copy()
    e.update(BASE_ENV)
    if env:
        e.update(env)

    kwargs: dict = {
        "capture_output": True,
        "text": True,
        "env": e,
        "timeout": timeout,
        "cwd": cwd,
    }
    if input is None:
        kwargs["stdin"] = subprocess.DEVNULL  # 不要继承 pytest 的终端
    else:
        kwargs["input"] = input

    p = subprocess.run([BIN, *args], **kwargs)
    return Run(p.returncode, p.stdout, p.stderr)


def run_pty(
    *args: str,
    env: dict[str, str] | None = None,
    timeout: float = 8.0,
) -> tuple[int, str]:
    """在带控制终端的 PTY 中运行；不写 stdin。超时后 kill。"""
    e = os.environ.copy()
    e.update(BASE_ENV)
    if env:
        e.update(env)

    pid, master = pty.fork()
    if pid == 0:
        try:
            os.execvpe(BIN, [BIN, *args], e)
        except OSError:
            os._exit(127)

    chunks: list[bytes] = []
    deadline = time.monotonic() + timeout
    try:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                os.kill(pid, signal.SIGKILL)
                os.waitpid(pid, 0)
                raise TimeoutError(f"PTY run timed out: {args}")

            ready, _, _ = select.select([master], [], [], min(remaining, 0.2))
            if ready:
                try:
                    chunk = os.read(master, 65536)
                except OSError as exc:
                    if exc.errno == errno.EIO:
                        break
                    raise
                if not chunk:
                    break
                chunks.append(chunk)

            wpid, status = os.waitpid(pid, os.WNOHANG)
            if wpid == pid:
                while True:
                    ready, _, _ = select.select([master], [], [], 0)
                    if not ready:
                        break
                    try:
                        chunk = os.read(master, 65536)
                    except OSError:
                        break
                    if not chunk:
                        break
                    chunks.append(chunk)
                return (
                    os.waitstatus_to_exitcode(status),
                    b"".join(chunks).decode("utf-8", "replace"),
                )
    finally:
        os.close(master)

    _, status = os.waitpid(pid, 0)
    return os.waitstatus_to_exitcode(status), b"".join(chunks).decode("utf-8", "replace")


# ---------- E. 信封与退出码 ----------

def test_e1_list_json_envelope():
    r = run(*LIST, "--json")
    assert r.code == 0, r
    doc = r.result_json()
    assert doc["type"] == "result"
    assert isinstance(doc["data"], list)  # 内容按夹具；空列表见 P4
    assert doc["meta"]["schema_version"] == "1"


def test_e2_get_json_envelope():
    r = run(*GET, "--json")
    assert r.code == 0, r
    doc = r.result_json()
    assert doc["type"] == "result"
    assert isinstance(doc["data"], dict)
    assert "id" in doc["data"]


def test_e3_not_found():
    r = run(*GET_MISSING, "--json")
    assert r.code == 3, r
    assert r.stdout == ""
    doc = r.error_json()
    assert doc["type"] == "error"
    assert doc["error"]["code"] == "not_found"
    assert doc["error"]["retryable"] is False
    assert doc["meta"]["schema_version"] == "1"


def test_e6_unknown_flag():
    r = run(*LIST, "--definitely-not-a-flag")
    assert r.code == 2, r
    assert r.stdout == ""


def test_e7_conflicting_flags():
    r = run(*LIST, "--quiet", "--verbose")
    assert r.code == 2, r


def test_e9_unknown_field():
    r = run(*LIST, "--json", "--fields", "definitely_not_a_field")
    assert r.code == 2, r
    assert r.error_json()["error"]["code"] == "unknown_field"


# ---------- N. 非交互与交互 ----------

def test_n1_no_tty_does_not_hang():
    try:
        r = run(*LIST, timeout=5)
    except subprocess.TimeoutExpired:
        pytest.fail("command hung in non-interactive mode")
    assert r.code != 124, r


def test_n2_destructive_without_yes_exits_12():
    r = run(
        *DELETE,
        "--json",
        env={"ACLI_NO_INPUT": "1", "ACLI_YES": "1"},
        timeout=5,
    )
    assert r.code == 12, r
    assert r.stdout == ""
    assert r.error_json()["error"]["code"] == "confirmation_required"


def test_n3_pty_confirmation_times_out():
    start = time.monotonic()
    code, output = run_pty(*DELETE, env={"ACLI_PROMPT_TIMEOUT": "1s"}, timeout=8)
    elapsed = time.monotonic() - start
    assert code == 12, output
    assert output.strip(), "expected a confirmation prompt on the PTY"
    assert 0.5 <= elapsed <= 5, f"unexpected prompt timeout: {elapsed:.1f}s"


def test_n7_no_input_still_reads_explicit_payload():
    payload = json.dumps({"name": "acli-contract-test-n7"})
    r = run(
        *CREATE[:2],
        "--from-file", "-",
        "--no-input",
        "--dry-run",
        "--json",
        input=payload,
    )
    assert r.code == 0, r
    assert r.result_json()["meta"]["dry_run"] is True


def test_n9_stdin_single_owner():
    r = run(*CREATE[:2], "--from-file", "-", "--token-stdin", "--json", input="{}")
    assert r.code == 2, r
    assert r.error_json()["error"]["code"] == "conflicting_flags"


def test_out14_human_stays_human_under_pipe():
    r = run(*LIST)
    assert r.code == 0, r
    with pytest.raises(json.JSONDecodeError):
        json.loads(r.stdout)


# ---------- S. NDJSON ----------

def test_s1_ndjson_frames():
    r = run(*LIST, "--output=ndjson", "--limit", "2")
    assert r.code == 0, r
    lines = [json.loads(line) for line in r.stdout.splitlines() if line]
    assert lines, "no NDJSON output"
    assert all("type" in line for line in lines)
    assert lines[-1]["type"] == "end"
    assert lines[-1]["meta"]["schema_version"] == "1"


def test_s3_error_has_no_end_frame():
    r = run(*LIST, "--output=ndjson", "--cursor", "invalid-cursor")
    assert r.code != 0, r
    assert '"type":"end"' not in r.stdout
    assert '"type": "end"' not in r.stdout


def test_s7_epipe_exits_zero():
    env = os.environ.copy()
    env.update(BASE_ENV)
    p = subprocess.Popen(
        [BIN, *LIST, "--all", "--output=ndjson"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )
    assert p.stdout is not None and p.stderr is not None
    # 关闭唯一读端：下一次 stdout 写入必然 EPIPE（输出越多越稳定）
    p.stdout.close()
    try:
        code = p.wait(timeout=10)
    except subprocess.TimeoutExpired:
        p.kill()
        p.wait()
        pytest.fail("CLI hung after stdout was closed")
    stderr = p.stderr.read().decode("utf-8", "replace")
    assert code == 0
    for line in stderr.splitlines():
        if not line.strip():
            continue
        try:
            doc = json.loads(line)
        except json.JSONDecodeError:
            continue
        assert doc.get("type") != "error", f"EPIPE must not emit an error document: {line}"
    assert "broken pipe" not in stderr.lower()


# ---------- P. 分页与字段 ----------

def test_p1_pagination_meta():
    r = run(*LIST, "--json", "--limit", "1")
    assert r.code == 0, r
    meta = r.result_json()["meta"]
    for key in ("schema_version", "count", "has_more", "next_cursor"):
        assert key in meta, f"missing meta.{key}"


def test_p2_count_is_page_size():
    r = run(*LIST, "--json", "--limit", "1")
    meta = r.result_json()["meta"]
    assert meta["count"] <= 1


def test_p7_fields_projection():
    r = run(*LIST, "--json", "--fields", "id")
    assert r.code == 0, r
    for item in r.result_json()["data"]:
        assert set(item) == {"id"}


def test_p8_fields_keeps_meta():
    r = run(*LIST, "--json", "--fields", "id")
    assert r.code == 0, r
    meta = r.result_json()["meta"]
    assert "next_cursor" in meta


# ---------- SEC. 安全 ----------

def test_sec1_token_not_leaked():
    sentinel = "SENTINEL_SECRET_123"
    r = run(*LIST, "--json", "--debug", env={"ACLI_TOKEN": sentinel})
    assert sentinel not in r.stdout
    assert sentinel not in r.stderr


def test_sec6_dry_run_has_no_side_effect():
    r = run(*CREATE, "--dry-run", "--json")
    assert r.code == 0, r
    doc = r.result_json()
    assert doc["meta"]["dry_run"] is True
    assert doc["data"]["plan"]["mode"] in ("client", "server")

    listing = run(*LIST, "--json")
    names = [item.get("name") for item in listing.result_json()["data"]]
    assert RESOURCE_NAME not in names


# ---------- SIG. 信号 ----------

def test_sig1_sigint_exit_130():
    proc = subprocess.Popen(
        [BIN, *LONG],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    time.sleep(1.0)
    assert proc.poll() is None, "LONG command exited too early; choose a longer fixture"
    proc.send_signal(signal.SIGINT)
    assert proc.wait(timeout=10) == 130


def test_sig2_sigterm_exit_143():
    proc = subprocess.Popen(
        [BIN, *LONG],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    time.sleep(1.0)
    assert proc.poll() is None, "LONG command exited too early; choose a longer fixture"
    proc.send_signal(signal.SIGTERM)
    assert proc.wait(timeout=10) == 143


# ---------- D. 发现 ----------

# 每个 schema 命令至少要在这张表里登记一个测试；新增命令必须补测试或显式豁免。
COVERED_COMMANDS = {
    "project list": ["test_e1_list_json_envelope", "test_p1_pagination_meta"],
    "project get": ["test_e2_get_json_envelope"],
    "project create": ["test_sec6_dry_run_has_no_side_effect"],
    "project delete": ["test_n2_destructive_without_yes_exits_12"],
    "auth login": ["test_auth1_non_interactive_login_does_not_block"],
    "auth status": ["test_auth2_status_does_not_leak_token"],
    "auth logout": ["test_auth3_logout_is_idempotent"],
    "job wait": ["test_sig1_sigint_exit_130"],
}


def test_d6_every_schema_command_has_a_test():
    index = run("schema").result_json()["data"]
    missing = [
        " ".join(entry["path"])
        for entry in index
        if " ".join(entry["path"]) not in COVERED_COMMANDS
    ]
    assert not missing, f"commands without contract tests: {missing}"


def test_d7_delete_confirmation_matches_schema():
    detail = run("schema", *DELETE[:2]).result_json()["data"]
    if detail.get("requires_confirmation"):
        r = run(*DELETE, "--json", timeout=5)
        assert r.code == 12, r


def test_d1_schema_defaults_to_json():
    r = run("schema")
    assert r.code == 0, r
    assert r.result_json()["type"] == "result"


def test_d4_every_schema_path_has_help():
    r = run("schema")
    assert r.code == 0, r
    for entry in r.result_json()["data"]:
        path = entry["path"]
        h = run(*path, "--help")
        assert h.code == 0, f"--help failed for {path}: {h.stderr}"


def test_d5_schema_detail_flags_match_help():
    index = run("schema").result_json()["data"]
    for entry in index:
        path = entry["path"]
        detail = run("schema", *path).result_json()["data"]
        h = run(*path, "--help")
        assert h.code == 0, path
        help_text = h.stdout + h.stderr
        for spec in detail.get("flags", []):
            name = spec["name"]
            assert name in help_text, f"schema flag {name} missing from --help for {path}"


# ---------- AUTH. 认证 ----------

def test_auth1_non_interactive_login_does_not_block():
    r = run("auth", "login", timeout=5)
    assert r.code in (0, 4), r


def test_auth2_status_does_not_leak_token():
    sentinel = "SENTINEL_AUTH_123"
    r = run("auth", "status", "--json", env={"ACLI_TOKEN": sentinel})
    assert sentinel not in r.stdout
    assert sentinel not in r.stderr


def test_auth3_logout_is_idempotent():
    first = run("auth", "logout")
    second = run("auth", "logout")
    assert second.code == 0, second
```

## 故障注入骨架

429/503、慢响应、流中途断连需要本地假服务器。把 endpoint 指向它（测试环境使用 `ACLI_ENDPOINT` 或显式 `--config`）：

```python
import http.server
import threading


class FakeHandler(http.server.BaseHTTPRequestHandler):
    mode = "ok"

    def do_GET(self):
        if self.mode == "429":
            self.send_response(429)
            self.send_header("Retry-After", "1")
            self.end_headers()
            return
        if self.mode == "503":
            self.send_response(503)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"items": [], "has_more": false, "next_cursor": null}')

    def log_message(self, *args):
        pass


def start_fake(port=0):
    server = http.server.ThreadingHTTPServer(("127.0.0.1", port), FakeHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server
```

- 流中途断连：在 handler 中先写两条 JSON，再关闭连接。
- 在途写信号：让写请求挂起，send SIGINT/SIGTERM 后断言 `outcome_unknown`。
- 服务端版本：返回不带 `server_api_version` 或超区间版本，断言 `unsupported_server_version`。
- 非幂等写单次尝试：让 503 持续，统计请求次数，断言只发一次。

## 注意事项

- `run()` 在 `input=None` 时把 stdin 设为 `DEVNULL`，避免继承 pytest 的终端。
- `run_pty` 使用 `pty.fork()`，子进程拥有控制终端，`/dev/tty` 可用；stdout/stderr 合并，只用于断言退出码和提示。
- PTY 测试前确保 `ACLI_PROMPT_TIMEOUT` 足够短（1–2 秒）。
- 信号测试不要用会立即结束的命令；否则 `proc.poll() is not None` 会先失败。
- EPIPE 测试要保证输出足够多，或在子进程写入前关闭读端；小结果可能不触发 EPIPE。
- 测试资源名加随机后缀，避免并发冲突；本 harness 用固定名便于清理，串行执行即可。
- CI 中未设置 `ACLI_BIN` 时模块直接报错，不会以「零测试」绿色通过。
