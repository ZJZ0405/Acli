---
name: llmc
description: Task playbook for the llmc CLI (vLLM llm-compressor): inspect models, validate or generate recipes, quantize, prune MoE experts, and verify compressed checkpoints. Use when a task involves model compression, quantization schemes (GPTQ, AWQ, FP8, SmoothQuant, QuIP, SpinQuant, QAD), calibration datasets, or compressed-tensors validation.
applies_to: "llmc >=0.1.0 <0.2.0; interface llmc-agent-cli/1"
---

# llmc

`llmc` is an agent-friendly CLI for vLLM llm-compressor. It covers model inspection, recipe generation and validation, quantization, MoE expert pruning, and checkpoint verification.

## Interface truth

Do not guess flags from memory or from this guide. The binary is authoritative:

- `llmc schema -o /tmp/llmc-schema.json` writes the full machine manifest. It is large (~165 KB); query only the command you need.
- `llmc <command> --help` prints the exact flags for that command.
- `llmc version --json` reports the installed CLI and dependency versions.

This guide targets llmc 0.1.0 (`interface: llmc-agent-cli/1`). If `version --json` reports a different version, trust `schema` and `--help` over this text.

Read one command without dumping the manifest:

```bash
llmc schema -o /tmp/llmc-schema.json
python3 -c 'import json; s=json.load(open("/tmp/llmc-schema.json")); print(json.dumps(s["commands"]["quantize"], indent=2))'
```

## Output contract

- `--json` prints exactly one JSON object on stdout; logs and progress go to stderr.
- Shape: `{llmc_version, command, duration_s, ok, data | error, warnings}`.
- On failure, `ok` is false and `error` carries `code` (`USAGE`, `INPUT`, `RUNTIME`, `ENVIRONMENT`, ...), `message`, and `type`.
- Exit codes: `0` success, `2` usage, `3` input, `4` runtime (compression/CUDA/OOM), `5` environment (GPU/model/network/dependency), `130` interrupted.
- `--progress json` emits newline-delimited JSON on stderr. `--quiet` silences logs but not errors.
- The CLI is non-interactive: never expect a prompt, never wait for one.

## Preflight

Run these before any long job:

```bash
llmc doctor --json
llmc inspect <model> --json
```

Check `ok`, GPU visibility, and installed `torch`/`llmcompressor`. Inspect the model architecture, parameter count, and existing quantization. If a model is gated, resolve access before starting. Do not launch quantization when `doctor` reports a missing GPU or dependency; fix the environment first (exit 5 territory).

## Choose the path

| Goal | Command |
|---|---|
| Inspect a model without loading weights | `llmc inspect MODEL --json` |
| List bundled quantization schemes | `llmc schemes --json` |
| List recipe modifiers and parameters | `llmc modifiers --json` |
| List built-in recipes | `llmc recipe presets --json` |
| Validate and normalize a recipe | `llmc recipe validate --recipe FILE --json` |
| Generate a recipe | `llmc recipe generate --preset NAME -o recipe.yaml` |
| Quantize with calibration data | `llmc quantize MODEL -o OUT --method gptq --scheme W4A16 --dataset PATH --json` |
| Apply a custom recipe | `llmc apply MODEL -r recipe.yaml -o OUT --json` |
| Data-free PTQ on safetensors | `llmc model-free MODEL -o OUT ... --json` |
| MoE expert pruning | `llmc prune ...` or `llmc expert-prune ...` |
| Verify a compressed checkpoint | `llmc verify OUT --json` |

Take method, scheme, and preset names from `llmc schemes` and `llmc recipe presets`. Never invent them. If the user names a preset such as `w4a16-gptq`, use it directly.

## Safe write workflow

1. Resolve the exact output directory. It must not exist or must be empty.
2. Run the same command with `--dry-run` first and inspect the plan.
3. Add `--overwrite` only when the user explicitly asked to replace an existing output.
4. Run the real command. Quantize, apply, and prune jobs can take minutes to hours; do not kill them on a short timeout.
5. On exit `4` or `5`, report `error.message` and fix the environment before retrying. On `130`, report that the run was interrupted and the output may be incomplete.
6. Finish with `llmc verify OUT --json`.

Calibration data changes model quality. Do not silently substitute a dataset; use the one the user specified or ask which one to use.

## Common pitfalls

- `--dataset` is a Hugging Face dataset id; `--dataset-path` is a local file. They are not interchangeable.
- `--overwrite` replaces an existing output directory. Never add it defensively.
- `--trust-remote-code` executes model code; use it only with explicit user consent.
- `--offline` sets Hugging Face offline mode. Use it only after the model and dataset are cached.
- OOM is exit `4`, not a usage error. Reduce batch size, sequence length, or device count instead of retrying unchanged.
- A missing `torch` in `doctor` means the runtime environment is wrong; do not debug the command line.
- `llmc schema -o FILE` writes the manifest to `FILE`; stdout carries only the log line, so read the file rather than piping stdout.

## Reporting

Report the command, exit code, output directory, and the relevant `data` fields. Do not paste the full JSON unless asked. For failures, include `error.code`, `error.message`, and the environment change needed before a retry.
