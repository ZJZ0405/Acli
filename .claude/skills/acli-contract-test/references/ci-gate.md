# CI 门禁

## 1. 必须门禁

| 门禁 | 作用 | 失败即阻断 |
|---|---|---|
| 契约测试 | 退出码、信封、非交互、NDJSON、分页 | 是 |
| PTY 测试 | 确认超时、交互通道 | 是 |
| schema 破坏性 diff | 防止删除/重命名字段、flag、命令 | 是 |
| human golden | 防止人类输出意外变化 | 是（允许显式更新快照） |
| help/schema 一致性 | 防止发现面漂移 | 是 |
| 脱敏扫描 | 防止 token 进入输出或日志 | 是 |

## 2. schema 快照：索引 + 全量命令详情

`acli schema` 默认只返回命令索引；索引条目没有 `flags`、`output_schema`、`error_codes` 等字段。直接拿索引做 diff 会漏掉所有 flag/错误码删除。必须先按 `path` 拉取每个命令的完整详情。

`scripts/snapshot_schema.py`：

```python
#!/usr/bin/env python3
"""Snapshot schema index and every command detail."""
import json
import subprocess
import sys


def main(cli: str) -> None:
    index = json.loads(subprocess.check_output([cli, "schema"], text=True))
    commands = {}
    for entry in index["data"]:
        path = entry["path"]
        detail = json.loads(
            subprocess.check_output([cli, "schema", *path], text=True)
        )
        commands[" ".join(path)] = detail["data"]
    json.dump({"index": index, "commands": commands}, sys.stdout, indent=2, sort_keys=True)
    print()


if __name__ == "__main__":
    main(sys.argv[1])
```

生成快照：

```bash
python3 scripts/snapshot_schema.py ./acli > schema/schema.json
```

基线必须来自上一个发布标签，不能使用工作区文件（PR 可以改工作区基线来让门禁通过）。标签本身已经带 `v`，不要再拼一次：

```bash
PREV_TAG=v0.3.0   # git describe --tags --abbrev=0 HEAD^ 或上一个 release tag
git show "${PREV_TAG}:schema/schema.json" > /tmp/previous.json
python3 scripts/schema_diff.py /tmp/previous.json schema/schema.json
```

## 3. schema diff

### 破坏性变更规则

规则只在 `meta.schema_version` 相同时生效；版本号变化视为 major 边界，允许破坏性变更。同一 `schema_version` 内，以下变更阻断：

- 删除或重命名命令。
- 删除 flag 或位置参数。
- 新增必填 flag / 必填参数。
- 改变 flag/参数的类型、默认值或枚举取值（删除枚举值）。
- 删除输出字段，新增必填输出字段，改变字段类型。
- 删除错误码，改变错误码到退出码的映射。
- 修改 `requires_auth`（false→true）、`requires_confirmation`（false→true）、`supports_dry_run`（true→false）、`supports_jobs`（true→false）、`destructive`（false→true）、`default_wait`。
- 从 `dry_run_modes` 中删除模式。
- 修改 `oneOf` 结构导致同步/job 结果无法区分。

允许：新增命令、flag、可选字段、错误码、NDJSON 帧类型；新增可选参数；放宽约束。

### `scripts/schema_diff.py`

```python
#!/usr/bin/env python3
"""Compare two full schema snapshots; exit 1 on breaking changes."""
import json
import sys


def load(path):
    doc = json.load(open(path, encoding="utf-8"))
    return doc["index"], doc["commands"]


def schema_breaking(old, new, where, out):
    if old == new:
        return
    if not isinstance(old, dict) or not isinstance(new, dict):
        out.append(f"{where}: schema changed")
        return

    ot, nt = old.get("type"), new.get("type")
    if ot != nt:
        out.append(f"{where}: type {ot} -> {nt}")

    old_props, new_props = old.get("properties", {}), new.get("properties", {})
    for key in old_props.keys() - new_props.keys():
        out.append(f"{where}.{key}: property removed")
    for key in old_props.keys() & new_props.keys():
        schema_breaking(old_props[key], new_props[key], f"{where}.{key}", out)

    added_required = set(new.get("required", [])) - set(old.get("required", []))
    for key in added_required:
        out.append(f"{where}.{key}: became required")

    removed_enum = set(old.get("enum", [])) - set(new.get("enum", []))
    for value in removed_enum:
        out.append(f"{where}: enum value removed: {value!r}")

    if ("oneOf" in old) != ("oneOf" in new):
        out.append(f"{where}: oneOf structure changed")
    elif "oneOf" in old:
        if len(old["oneOf"]) != len(new["oneOf"]):
            out.append(f"{where}: oneOf branch count changed")
        else:
            for i, (a, b) in enumerate(zip(old["oneOf"], new["oneOf"])):
                schema_breaking(a, b, f"{where}.oneOf[{i}]", out)

    if ot == "array":
        schema_breaking(old.get("items", {}), new.get("items", {}), f"{where}[]", out)


def diff_values(old, new, where, out):
    """Breaking-only diff: removed dict keys, changed scalars, removed list entries."""
    if old == new:
        return
    if isinstance(old, dict) and isinstance(new, dict):
        for key in old.keys() - new.keys():
            out.append(f"{where}.{key}: removed")
        for key in old.keys() & new.keys():
            diff_values(old[key], new[key], f"{where}.{key}", out)
    elif isinstance(old, list) and isinstance(new, list):
        new_items = {json.dumps(item, sort_keys=True) for item in new}
        for item in old:
            raw = json.dumps(item, sort_keys=True)
            if raw not in new_items:
                out.append(f"{where}: entry removed: {raw}")
    else:
        out.append(f"{where}: changed {old!r} -> {new!r}")


def index_map(index):
    return {
        tuple(entry["path"]): entry
        for entry in index.get("data", [])
        if isinstance(entry, dict) and "path" in entry
    }


def compare_flags(path, old_flags, new_flags, out):
    old_map = {f["name"]: f for f in old_flags}
    new_map = {f["name"]: f for f in new_flags}
    for name in old_map.keys() - new_map.keys():
        out.append(f"{path}: flag removed: {name}")
    for name in new_map.keys() - old_map.keys():
        if new_map[name].get("required"):
            out.append(f"{path}: required flag added: {name}")
    for name in old_map.keys() & new_map.keys():
        a, b = old_map[name], new_map[name]
        for key in ("type", "required", "default", "short"):
            if a.get(key) != b.get(key):
                out.append(f"{path}: flag {name} changed {key}")
        if set(a.get("enum", [])) - set(b.get("enum", [])):
            out.append(f"{path}: flag {name} removed enum value")
        if a.get("project_scope") != b.get("project_scope"):
            out.append(f"{path}: flag {name} changed project_scope")


def compare_args(path, old_args, new_args, out):
    old_map = {a["name"]: a for a in old_args}
    new_map = {a["name"]: a for a in new_args}
    for name in old_map.keys() - new_map.keys():
        out.append(f"{path}: arg removed: {name}")
    for name in new_map.keys() - old_map.keys():
        if new_map[name].get("required"):
            out.append(f"{path}: required arg added: {name}")
    for name in old_map.keys() & new_map.keys():
        a, b = old_map[name], new_map[name]
        for key in ("type", "required", "default"):
            if a.get(key) != b.get(key):
                out.append(f"{path}: arg {name} changed {key}")
        if set(a.get("enum", [])) - set(b.get("enum", [])):
            out.append(f"{path}: arg {name} removed enum value")


def main(old_path, new_path):
    old_index, old_commands = load(old_path)
    new_index, new_commands = load(new_path)
    breaking = []

    old_version = old_index.get("meta", {}).get("schema_version")
    new_version = new_index.get("meta", {}).get("schema_version")
    if old_version != new_version:
        print(f"schema_version changed: {old_version} -> {new_version}; breaking changes allowed")
        return 0

    old_index_map = index_map(old_index)
    new_index_map = index_map(new_index)
    for path in old_index_map.keys() - new_index_map.keys():
        breaking.append(f"command removed: {' '.join(path)}")
    for path in old_index_map.keys() & new_index_map.keys():
        old_entry, new_entry = old_index_map[path], new_index_map[path]
        for key in old_entry.keys() - new_entry.keys():
            breaking.append(f"index {' '.join(path)}: {key} removed")
        for key in old_entry.keys() & new_entry.keys():
            if key == "path":
                continue
            if old_entry[key] != new_entry[key]:
                breaking.append(f"index {' '.join(path)}: {key} changed")

    for path in old_commands.keys() - new_commands.keys():
        breaking.append(f"command detail removed: {path}")

    for path in old_commands.keys() & new_commands.keys():
        old, new = old_commands[path], new_commands[path]

        for key in (
            "requires_auth",
            "requires_confirmation",
            "destructive",
            "supports_dry_run",
            "supports_jobs",
            "default_wait",
        ):
            if old.get(key) != new.get(key):
                breaking.append(f"{path}: changed {key}")

        if set(old.get("dry_run_modes", [])) - set(new.get("dry_run_modes", [])):
            breaking.append(f"{path}: dry_run_modes value removed")

        compare_flags(path, old.get("flags", []), new.get("flags", []), breaking)
        compare_args(path, old.get("args", []), new.get("args", []), breaking)

        for key in ("error_codes", "exit_codes"):
            for value in set(old.get(key, [])) - set(new.get(key, [])):
                breaking.append(f"{path}: {key} removed: {value}")

        schema_breaking(
            old.get("output_schema", {}),
            new.get("output_schema", {}),
            f"{path}.output_schema",
            breaking,
        )

    # 全局表：error_code → exit_code 映射、全局 flag、环境变量、退出码表等。
    old_global = {k: v for k, v in old_index.items() if k != "data"}
    new_global = {k: v for k, v in new_index.items() if k != "data"}
    diff_values(old_global, new_global, "schema", breaking)

    if breaking:
        print("BREAKING CHANGES:")
        for line in breaking:
            print(f"  - {line}")
        return 1

    print("schema diff: no breaking changes")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2]))
```

全局表在 diff 中按值比较：标量变化（例如 `not_found: 3 → 4`）和数组条目删除都会阻断。`meta.schema_version` 变化时直接放行，避免 major 发布被误判。

## 4. human golden

固定环境，屏蔽不确定字段：

```bash
export TZ=UTC LC_ALL=C.UTF-8 LANG=C.UTF-8 NO_COLOR=1 COLUMNS=80 ACLI_NO_PROGRESS=1
"$CLI" project list | sed -E 's/req_[A-Za-z0-9]+/req_REDACTED/g' > /tmp/actual.txt
diff -u golden/project-list.txt /tmp/actual.txt
```

原则：

- 只用测试夹具数据，保证顺序和内容稳定。
- 时间戳、request_id、主机名用占位符替换。
- golden 中保留窄终端和 CJK 用例各一份。
- 更新 golden 必须走显式命令并在 PR 中审查。

## 5. GitHub Actions 示例

```yaml
name: contract

on:
  push:
  pull_request:
  release:
    types: [published]

jobs:
  contract:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0

      - uses: actions/setup-go@v5
        with:
          go-version: "1.22"

      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"

      - name: Install test deps
        run: python3 -m pip install pytest

      - name: Build
        run: make build

      - name: Start fixture server
        # 必须提供测试 endpoint 和夹具；不要指向默认真实服务
        run: ./scripts/start-fixtures.sh &

      - name: Contract tests
        env:
          ACLI_BIN: ./acli
          ACLI_ENDPOINT: http://127.0.0.1:8080
          ACLI_LIST_ARGS: project list
          ACLI_GET_ARGS: project get prj_ci
          ACLI_GET_MISSING_ARGS: project get prj_missing
          ACLI_CREATE_ARGS: project create --name acli-ci
          ACLI_DELETE_ARGS: project delete prj_ci
          ACLI_LONG_ARGS: job wait job_ci
        run: python3 -m pytest -q test_acli_contract.py

      - name: Snapshot schema
        run: python3 scripts/snapshot_schema.py ./acli > schema/current.json

      - name: Resolve previous tag
        id: prev
        run: |
          if [ "${{ github.event_name }}" = "release" ]; then
            tag="${{ github.event.release.tag_name }}"
          else
            tag="$(git describe --tags --abbrev=0 HEAD^ 2>/dev/null || true)"
          fi
          if [ -z "$tag" ]; then
            echo "skip=true" >> "$GITHUB_OUTPUT"
          else
            echo "skip=false" >> "$GITHUB_OUTPUT"
            echo "tag=$tag" >> "$GITHUB_OUTPUT"
          fi

      - name: Schema diff
        if: steps.prev.outputs.skip != 'true'
        run: |
          git show "${{ steps.prev.outputs.tag }}:schema/schema.json" > /tmp/previous.json
          python3 scripts/schema_diff.py /tmp/previous.json schema/current.json
```

要点：

- 标签只拼一次 `v`：`github.event.release.tag_name` 已经是 `v0.3.0` 形式；不要写 `v${PREV_VERSION}`。
- push/PR 上没有 release 上下文，用 `git describe --tags --abbrev=0 HEAD^` 取上一个发布标签；没有标签时跳过 diff（bootstrap 版本）。
- 必须安装 pytest 和构建工具；clean runner 不会自带。
- 契约测试必须连接假服务器或测试租户，并导出 `ACLI_ENDPOINT`；不能落到默认生产 endpoint。
- `ACLI_BIN` 必须设置；harness 在 CI 中不会静默 skip。
- schema 基线从发布标签读取，工作区文件不可信。

## 6. 平台矩阵

| 平台 | 发布前必跑 |
|---|---|
| Linux amd64 | 全量契约 + PTY + golden + schema diff |
| Linux arm64 | 全量契约 |
| macOS amd64/arm64 | 契约 + PTY + golden |
| WSL | 契约 + PTY |
| 原生 Windows | 仅验证 `unsupported_platform` 明确失败 |

## 7. 常见坑

- **CI 默认没有 TTY**：PTY 用例必须显式创建伪终端，否则测不到确认逻辑。
- **stdout 缓冲**：NDJSON 用例读取前确保进程已 flush；`subprocess.run` 会等待退出，天然满足。
- **信号测试偶发**：先轮询确认进程仍在运行，再发信号；不要用固定 sleep 后盲目 `kill`。
- **golden 漂移**：`TZ`、locale、终端宽度任一未固定都会导致差异；在脚本里统一 export。
- **schema 快照过期**：快照必须随版本发布更新，不能只在本地生成一次。
- **索引当详情用**：`schema` 索引没有 flags/output_schema；diff 必须读取每个命令的详情。
- **版本号变化**：`meta.schema_version` 不同意味着 major 边界，破坏性 diff 放行；不要用它掩盖意外删除。
- **跳过即通过**：CI 中 skip 的用例要在报告里可见；不要让环境缺失悄悄变成绿色。
