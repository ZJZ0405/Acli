# Acli

Acli 是「人类与 AI agent 共用」的 CLI 契约，以及围绕它的技能、插件市场和参考实现。

- [`DESIGN.md`](DESIGN.md) — CLI 契约草案（v0.3）。
- [`MARKETPLACE.md`](MARKETPLACE.md) — 与 agent 架构无关的插件市场规范。
- [`marketplace/`](marketplace) — 注册表、插件清单、可移植使用指南。
- [`scripts/acli_market.py`](scripts/acli_market.py) — 任意 agent 都能调用的目录客户端。
- [`adapters/claude-code/`](adapters/claude-code) — 第一个适配器（Claude Code）。
- [`src/llmc_cli/`](src/llmc_cli) — 第一个 CLI 插件 `llmc` 的实现。

## 快速开始（不依赖任何 agent 框架）

```bash
python3 scripts/acli_market.py list
python3 scripts/acli_market.py info llmc
python3 scripts/acli_market.py plan llmc
python3 scripts/acli_market.py guide llmc
python3 scripts/acli_market.py --json list
```

`acli-market` 本身遵循 Acli 的机器契约：`--json` 模式下 stdout 只放一个 JSON 文档，诊断走 stderr，退出码区分为 `2 usage`、`3 not_found`、`4 invalid_catalog`。任何能执行 shell 的 agent 都能消费它，不需要插件系统。

## 市场模型

市场只描述「CLI 是什么、怎么安装、怎么自描述、有没有任务指南」：

```text
registry → plugin manifest → interface (--help / schema) + optional guide → adapter
```

- **接口层**：每个插件必须暴露 `--help` 和 `schema`，这是最低公共层，也是唯一与二进制同版本的事实来源。
- **任务层**：可选的可移植 Markdown 指南，写何时用、工作流、安全规则，不复制 flag 表。
- **适配层**：Claude Code、Codex、Cursor 等格式都从注册表生成，适配器不是事实来源。

完整字段、安装流程、信任模型和版本规则见 [`MARKETPLACE.md`](MARKETPLACE.md)。

## CLI 使用说明：skill 还是 `--help`

结论是两层并存：`--help`/`schema` 负责「这个二进制支持什么」，指南/skill 负责「什么时候用、按什么顺序做」。只靠 `--help` 的问题是发现和跨命令流程；只靠 skill 的问题是漂移、harness 绑定和版本错位。细节见 [`MARKETPLACE.md`](MARKETPLACE.md) §5。

## Claude Code 适配器（先遣）

Claude Code 是第一个适配器，不是市场的定义。

```text
/plugin marketplace add ZJZ0405/Acli
/plugin install llmc@acli
/plugin install acli-toolkit@acli
```

- `llmc` — 从通用清单生成的 CLI 插件，包含一份薄 skill，接口仍以 `llmc schema` / `llmc --help` 为准。
- `acli-toolkit` — 适配器附带的本仓库技能包，包含 `acli-design`、`acli-scaffold`、`acli-audit`、`acli-contract-test`。

`.claude-plugin/` 和 `plugins/llmc/` 由适配器生成，不要手改：

```bash
python3 scripts/build_claude_adapter.py
python3 scripts/build_claude_adapter.py --check   # CI 漂移检查
```

## 新增一个插件

1. 在 `marketplace/plugins/<id>/plugin.json` 写清单，`interface` 必须指向真实的 `--help` 和 `schema`。
2. 复杂工具在 `usage/<id>.md` 写可移植指南：声明 `applies_to`，正文只写工作流和安全规则。
3. 在 `marketplace/index.json` 登记 `{id, manifest}`。
4. 校验并生成适配器：

```bash
python3 scripts/acli_market.py validate
python3 scripts/build_claude_adapter.py
claude plugin validate . --strict
claude plugin validate plugins/llmc --strict
```

5. 如果 CLI 本身还不符合 Acli 契约，先用 `acli-design` 定契约，再用 `acli-scaffold` 改实现，最后用 `acli-audit` 和 `acli-contract-test` 验收。

## 参考实现

`llmc` 是 vLLM llm-compressor 的 agent-friendly CLI。重依赖（`torch`、`llmcompressor`）都是延迟导入；`--help`、`schema`、`version` 不需要安装它们：

```bash
PYTHONPATH=src python3 -m llmc_cli --help
PYTHONPATH=src python3 -m llmc_cli schema -o /tmp/llmc-schema.json
```
