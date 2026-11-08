# Acli 插件市场规范（草案 v0.1）

> 状态：草案。
> 目标：定义一个与 agent 架构无关的插件注册表，让任何 agent、脚本或人类都能发现、评估、安装并正确使用一个 CLI 插件。
> 非目标：不定义某个 agent 的插件格式；不托管二进制；不做包管理器。

## 0. 一句话

市场只描述「这个 CLI 是什么、怎么安装、怎么自描述、有没有任务指南」；接口权威永远在 CLI 自己的 `--help` 和 `schema` 里，agent 特有的打包方式只是适配器。

## 1. 分层模型

```
registry (marketplace/index.json)
  └── plugin manifest (plugins/<id>/plugin.json)
        ├── distributions     安装来源
        ├── interface         help / schema / version 的调用方式
        └── usage             可选的可移植任务指南
              └── adapter     转换成 Claude SKILL.md、AGENTS.md、自定义格式……
```

四层职责：

| 层 | 内容 | 权威性 |
|---|---|---|
| 注册表 | 插件 id、清单路径 | 市场维护 |
| 插件清单 | 版本、安装来源、接口命令、指南路径 | 插件发布者维护 |
| 接口 | `--help`、`schema`、退出码 | **二进制自身** |
| 指南 | 何时用、工作流、安全策略 | 发布者维护，必须指向接口 |

## 2. 目录结构

```text
marketplace/
  index.json
  schema/                 # JSON Schema，可被任意语言校验
  plugins/
    <id>/
      plugin.json
      usage/<id>.md       # 可选，可移植指南
adapters/
  claude-code/            # 第一个适配器
scripts/
  acli_market.py          # 与 agent 无关的目录客户端
  build_claude_adapter.py # 从注册表生成 Claude 文件
```

`.claude-plugin/` 和 `plugins/<id>/.claude-plugin/` 是**生成物**，不是事实来源。CI 用 `build_claude_adapter.py --check` 防止漂移。

## 3. 注册表

`marketplace/index.json`：

```json
{
  "schema_version": 1,
  "name": "acli",
  "description": "Agent-agnostic registry of Acli-compatible CLI plugins.",
  "homepage": "https://github.com/ZJZ0405/Acli",
  "plugins": [
    { "id": "llmc", "manifest": "plugins/llmc/plugin.json" }
  ]
}
```

规则：

- `manifest` 路径相对于 `index.json`。
- 插件 id 全局唯一，`^[a-z0-9][a-z0-9._-]*$`。
- 注册表只放数据和校验信息，不放安装脚本。
- 注册表本身可以被镜像、签名、缓存；agent 读取方式不限于本仓库。

## 4. 插件清单 v1

见 `marketplace/schema/plugin.schema.json`。最小示例：

```json
{
  "schema_version": 1,
  "id": "llmc",
  "kind": "cli",
  "name": "llmc",
  "version": "0.1.0",
  "summary": "Agent-friendly CLI for vLLM llm-compressor.",
  "license": "Apache-2.0",
  "publisher": { "name": "ZJZ0405" },
  "distributions": [
    { "type": "source", "path": "src/llmc_cli", "entrypoint": ["python3", "-m", "llmc_cli"] }
  ],
  "interface": {
    "executable": "llmc",
    "version_command": ["llmc", "version", "--json"],
    "help_command": ["llmc", "--help"],
    "schema_command": ["llmc", "schema", "-o", "{file}"],
    "schema_version": "llmc-agent-cli/1",
    "output_protocols": ["json"],
    "non_interactive": true
  },
  "usage": {
    "mode": "guide",
    "guide": "usage/llmc.md",
    "format": "markdown",
    "applies_to": { "cli": ">=0.1.0 <0.2.0", "interface": "llmc-agent-cli/1" }
  }
}
```

字段说明：

| 字段 | 必填 | 说明 |
|---|---|---|
| `schema_version` | 是 | 清单格式版本，当前固定为 1 |
| `id` | 是 | 市场内唯一 id |
| `kind` | 是 | v1 只支持 `cli`；为 `skill-pack`、`mcp-server` 预留 |
| `version` | 是 | 插件版本（semver），不是 CLI 接口版本 |
| `summary` | 是 | 一句话用途，≤160 字符，用于发现 |
| `distributions` | 是 | 安装来源：pypi / npm / binary / source / container |
| `interface` | 是 | 如何调用版本、帮助、schema |
| `usage` | 否 | `help` / `guide` / `both` |
| `platforms` | 否 | 支持平台 |
| `keywords` | 否 | 发现用 |

`interface.schema_version` 是 CLI 自己 schema 里的版本（例如 `llmc-agent-cli/1`），不是市场清单版本。两者不能混用。

## 5. CLI 使用说明：`--help` / `schema` 还是附加指南

结论：**接口层必须内置，任务层可选附加，两者不是替代关系。**

### 5.1 三层信息

| 信息 | 放在哪里 | 原因 |
|---|---|---|
| 命令、flag、类型、默认值、退出码 | `--help` / `schema` | 从解析器生成，随版本发布，可执行验证 |
| 何时用、跨命令流程、安全策略、常见坑 | 可移植指南 | help 是命令局部的，教不了任务编排 |
| 名称、一句话、关键词 | 市场清单 | 发现发生在二进制之外 |

### 5.2 为什么每个 CLI 插件都必须有 `--help` 和 `schema`

- agent 可能不支持任何 skill/plugin 格式，只能通过 shell 调用；这是最低公共层。
- `--help` 和 `schema` 与二进制同版本，是唯一不会版本错位的事实来源。
- 安装后可以立即验证：`version_command` 和 `schema_command` 必须成功。
- 指南可以缺失、过期、被裁剪；接口不能。

### 5.3 为什么指南仍然值得存在

- 发现：agent 不知道工具存在，就不会执行 `--help`。
- 编排：`inspect → recipe → quantize → verify` 这类流程跨多个命令。
- 成本：复杂 CLI 的 help/schema 很大。`llmc schema` 约 165 KB，整份注入不现实。
- 安全：破坏性操作、dry-run 顺序、`--overwrite` 策略需要显式说明。
- 一致性：不同 CLI 的 help 质量参差，指南可以统一成 agent 能执行的形状。

### 5.4 指南规则

1. 指南是 Markdown + 最小 frontmatter：

   ```yaml
   ---
   name: llmc
   description: 何时使用这个工具（也是发现钩子）
   applies_to: "llmc >=0.1.0 <0.2.0; interface llmc-agent-cli/1"
   ---
   ```

2. **不复制完整 flag 表**。正文只写选择条件、工作流、安全规则、常见坑、报告方式，接口细节指向 `tool schema` / `tool <cmd> --help`。
3. 声明兼容的 CLI 版本和接口版本；运行时用 `version_command` 确认。
4. CI 校验：指南里提到的命令和 flag 必须存在于 schema；示例命令必须能被解析。
5. 指南是**不可信内容**：它是自然语言指令，可能被注入。市场条目要 review、签名、锁版本；安装更新时展示 diff。

### 5.5 不要劫持 `--help`

市场不能修改已安装二进制的 `--help`。如果需要一个聚合入口，提供：

```text
python3 scripts/acli_market.py guide <id>   # 只打印可移植指南
python3 scripts/acli_market.py plan <id>    # 安装与验证步骤
```

原生 `--help` 保持二进制自己的语义。包装器只做聚合，不改变命令行为。

### 5.6 什么时候不需要指南

- 单命令、flag 稳定、agent 已经知道怎么调用；
- 使用频率很低，维护指南的收益小于成本；
- 输出契约简单，没有破坏性操作。

这时 `usage.mode: "help"` 即可。市场可以根据 schema 生成一份最小指南（名称 + 用途 + 命令索引），但这属于适配器的便利功能，不是契约。

## 6. 发现与安装流程

```text
1. list / search                 → 找到插件 id
2. info <id>                     → 读清单：版本、平台、来源、接口
3. plan <id>                     → 得到安装来源和验证命令
4. 安装                          → pypi/npm/binary/source，校验 sha256
5. version_command               → 确认实际版本
6. schema_command                → 确认接口版本和命令面
7. 适配器物化指南（可选）         → 例如生成 Claude SKILL.md
8. 记录 lockfile                 → id, version, sha256, schema_hash
```

市场不默认执行安装脚本。二进制分发必须提供 `sha256`；包管理器分发固定版本。升级是显式动作，不做静默自动更新。

## 7. 信任与安全

- 清单是数据，指南是指令；两者信任级别不同。
- 注册表和清单应签名；至少记录每个分发物和指南的 `sha256`。
- 不允许安装脚本默认执行；需要时显式 opt-in 并展示内容。
- 指南注入的风险由适配器承担：安装前展示 diff，更新时保留旧版本以便回滚。
- 插件可以声明 `platforms`；不匹配时安装器应明确失败，而不是尝试运行。

## 8. 适配器

适配器只做格式转换：注册表 → 某个 agent 的原生插件格式。

第一个适配器是 Claude Code：

```bash
python3 scripts/build_claude_adapter.py          # 生成
python3 scripts/build_claude_adapter.py --check  # CI 漂移检查
```

映射关系：

| 通用 | Claude Code |
|---|---|
| plugin manifest | `.claude-plugin/plugin.json` |
| marketplace index | `.claude-plugin/marketplace.json` |
| portable guide | `skills/<id>/SKILL.md`（frontmatter 只保留 name/description） |

其他 agent 可以按同样方式适配：Codex/AGENTS.md、Cursor rules、自定义 system prompt、甚至纯 shell 的 `acli-market guide`。适配器不得成为事实来源；删除适配器后，注册表和 CLI 仍应完整可用。

## 9. 版本与兼容

三个版本号不能混：

| 版本 | 位置 | 变更含义 |
|---|---|---|
| 市场清单 `schema_version` | 清单根 | 清单字段结构变化 |
| 插件 `version` | 清单 | 分发物变化 |
| CLI `interface.schema_version` | CLI schema | 命令/输出契约变化 |

规则：

- `interface.schema_version` 变化时，适配器必须重新生成指南并提示用户。
- 指南的 `applies_to` 与实际版本不匹配时，适配器应拒绝安装指南，但仍然允许使用 CLI 的 `--help`/`schema`。
- 插件的破坏性变更遵循 `DESIGN.md` 的兼容规则。

## 10. 待决问题

1. 注册表签名格式：Sigstore / minisign / 简单 detached GPG？
2. 注册表托管：本仓库、独立静态站点、还是 Git 镜像？
3. 是否允许 `install` 命令自动执行包管理器？当前只输出计划。
4. 指南能否从 `schema` 半自动生成？当前假设人工维护、CI 校验。
5. 多文件指南（references/、示例）如何声明和打包？
6. 插件依赖（一个插件依赖另一个）是否需要进入 v1？
