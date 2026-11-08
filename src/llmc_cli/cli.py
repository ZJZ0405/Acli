"""Command-line entry point for llmc-cli."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

from llmc_cli import __version__
from llmc_cli import catalog, formatting
from llmc_cli.data import DatasetOptions
from llmc_cli.errors import InputError, LLMCError, UsageError
from llmc_cli.inspect import (
    doctor,
    human_model_summary,
    inspect_dataset,
    inspect_model,
    verify_model,
)
from llmc_cli.operations import (
    OneshotOptions,
    run_apply,
    run_expert_prune,
    run_model_free,
    run_prune,
    run_quantize,
)
from llmc_cli.output import Reporter, configure_logging
from llmc_cli.recipes import (
    build_prune_specs,
    build_quantize_specs,
    load_recipe,
    recipe_from_preset,
    recipe_from_specs,
    recipe_summary,
    recipe_to_dict,
    recipe_to_yaml,
)

PROG = "llmc"
DESCRIPTION = (
    "Agent-friendly CLI for vLLM llm-compressor: inspect models, validate recipes, "
    "quantize/prune checkpoints, and verify compressed artifacts."
)
EPILOG = """\
Agent conventions:
  * stdout carries exactly one JSON object when --json is passed; logs go to stderr.
  * --progress json emits newline-delimited JSON log records on stderr.
  * Non-interactive: no prompts. Use --overwrite to write into a non-empty directory.
  * Exit codes: 0 ok, 2 usage, 3 input, 4 runtime, 5 environment, 130 interrupted.
  * Run 'llmc schema' for a machine-readable command manifest.
"""

EXIT_CODES = {
    "0": "success",
    "2": "usage error (bad flags or command)",
    "3": "input error (missing file, invalid recipe, non-empty output)",
    "4": "runtime error (compression, CUDA/OOM, conversion)",
    "5": "environment error (missing GPU, model, network, dependency)",
    "130": "interrupted",
}


class AgentArgumentParser(argparse.ArgumentParser):
    def error(self, message):  # noqa: A003 - argparse API
        raise UsageError(message, hint=f"Run '{self.prog} --help' for usage.")


def _add_common(parser: argparse.ArgumentParser, *, suppress: bool) -> None:
    def default(value):
        return argparse.SUPPRESS if suppress else value

    parser.add_argument(
        "--json",
        action="store_true",
        default=default(False),
        help="Emit one machine-readable JSON object on stdout.",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="count",
        default=default(0),
        help="Increase log verbosity (repeat for debug).",
    )
    parser.add_argument(
        "-q",
        "--quiet",
        action="store_true",
        default=default(False),
        help="Silence logs and progress (errors only).",
    )
    parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        default=default(None),
        help="Explicit loguru log level.",
    )
    parser.add_argument(
        "--log-file",
        default=default(None),
        metavar="PATH",
        help="Write DEBUG-level logs to this file.",
    )
    parser.add_argument(
        "--progress",
        choices=["auto", "off", "json"],
        default=default("auto"),
        help="Progress/log format on stderr; 'json' emits NDJSON.",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        default=default(False),
        help="Print tracebacks to stderr on failure.",
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        default=default(False),
        help="Set HF_HUB_OFFLINE/TRANSFORMERS_OFFLINE for this process.",
    )
    parser.add_argument(
        "--cuda-visible-devices",
        default=default(None),
        metavar="IDS",
        help="Set CUDA_VISIBLE_DEVICES before any CUDA library is imported.",
    )


def _new_parser(name: str, *, help_text: str, description: str, epilog: str, aliases=None) -> AgentArgumentParser:
    parser = _subparsers_ref[0].add_parser(
        name,
        help=help_text,
        description=description,
        epilog=epilog,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        aliases=aliases or [],
    )
    _add_common(parser, suppress=True)
    parser._llmc_aliases = list(aliases or [])
    parser._llmc_examples = [
        line.strip()
        for line in epilog.splitlines()
        if line.strip().startswith("llmc ") or line.strip().startswith("cat ")
    ]
    return parser


_subparsers_ref: list[Any] = []


def _add_dataset_args(parser: argparse.ArgumentParser) -> None:
    group = parser.add_argument_group("calibration dataset")
    group.add_argument("--dataset", metavar="HF_ID", help="Hugging Face dataset id or name.")
    group.add_argument(
        "--dataset-path",
        metavar="PATH",
        help="Local .json/.jsonl/.csv/.parquet/.arrow file or saved datasets directory.",
    )
    group.add_argument("--split", metavar="SPLIT", help="Dataset split, e.g. train[:512] or train_sft.")
    group.add_argument("--dataset-config", metavar="NAME", help="Dataset configuration name.")
    group.add_argument("--num-samples", type=int, default=512, help="Calibration samples (default: 512).")
    group.add_argument(
        "--max-seq-length",
        type=int,
        default=2048,
        help="Max tokens per calibration sample (default: 2048).",
    )
    group.add_argument("--batch-size", type=int, default=1, help="Calibration batch size (default: 1).")
    group.add_argument("--text-column", default="text", help="Text column for local datasets (default: text).")
    group.add_argument(
        "--data-collator",
        choices=["truncation", "padding"],
        default="truncation",
        help="Batch collation strategy (default: truncation).",
    )
    group.add_argument("--no-shuffle", dest="shuffle", action="store_false", default=True, help="Do not shuffle calibration rows.")
    group.add_argument("--streaming", action="store_true", help="Stream remote datasets.")
    group.add_argument("--overwrite-cache", action="store_true", help="Re-tokenize instead of using the datasets cache.")
    group.add_argument("--preprocessing-workers", type=int, help="Tokenization worker count.")
    group.add_argument("--dataloader-workers", type=int, default=0, help="DataLoader worker count.")
    group.add_argument("--concatenate", action="store_true", help="Concatenate samples into fixed-length chunks.")
    group.add_argument("--pad-to-max-length", action="store_true", help="Pad every sample to max length.")
    group.add_argument(
        "--moe-no-calibrate-all-experts",
        dest="moe_calibrate_all_experts",
        action="store_false",
        default=True,
        help="Calibrate only routed experts in MoE layers.",
    )
    group.add_argument("--min-tokens-per-module", type=float, help="Minimum calibration tokens per module.")


def _add_model_args(parser: argparse.ArgumentParser) -> None:
    group = parser.add_argument_group("model loading")
    group.add_argument(
        "--precision",
        choices=["auto", "float16", "bfloat16", "float32", "half", "full"],
        default="auto",
        help="Model precision (default: auto).",
    )
    group.add_argument("--revision", default="main", help="Model revision/branch/commit.")
    group.add_argument("--trust-remote-code", action="store_true", help="Allow custom modeling code.")
    group.add_argument("--tokenizer", help="Tokenizer id/path if different from the model.")
    group.add_argument("--processor", help="Processor id/path for multimodal models.")
    group.add_argument("--config-name", help="Config id/path if different from the model.")
    group.add_argument(
        "--no-tie-word-embeddings",
        dest="tie_word_embeddings",
        action="store_false",
        default=True,
        help="Untie input/output embeddings before compression.",
    )
    group.add_argument(
        "--no-save-compressed",
        dest="save_compressed",
        action="store_false",
        default=True,
        help="Save uncompressed weights (rarely useful).",
    )


def _add_oneshot_runtime_args(parser: argparse.ArgumentParser, *, with_stage: bool = False) -> None:
    group = parser.add_argument_group("calibration runtime")
    group.add_argument(
        "--pipeline",
        choices=["auto", "independent", "sequential", "basic", "datafree"],
        default="auto",
        help="Calibration pipeline; auto infers per modifier (default: auto).",
    )
    group.add_argument("--sequential-targets", action="append", metavar="CLASS", help="Sequential pipeline target modules; repeatable.")
    group.add_argument("--sequential-targets-per-subgraph", type=int, help="Modules trained jointly per subgraph.")
    group.add_argument("--sequential-offload-device", default="cpu", help="Offload device for sequential calibration (default: cpu).")
    group.add_argument("--stage-weights-in-pinned-memory", action="store_true", help="Stage weights in pinned host memory.")
    group.add_argument("--use-eager-attention", action="store_true", help="Use eager attention during calibration.")
    group.add_argument(
        "--no-propagate-error",
        dest="propagate_error",
        action="store_false",
        default=True,
        help="Disable error propagation between sequential subgraphs.",
    )
    group.add_argument("--log-sequential-error", action="store_true", help="Log per-subgraph SQNR errors.")
    if with_stage:
        group.add_argument("--stage", help="Recipe stage to select from a multi-stage recipe.")
    group.add_argument("--recipe-arg", action="append", dest="recipe_args", metavar="K=V", help="Recipe argument override; repeatable.")
    group.add_argument("--clear-sparse-session", action="store_true", help="Clear compression session state between modifiers.")
    group.add_argument("--run-log-dir", metavar="DIR", help="Write a timestamped llm-compressor log file here.")
    group.add_argument("--seed", type=int, default=42, help="Global random seed (default: 42).")
    group.add_argument(
        "--deterministic",
        action="store_true",
        help="Enable torch deterministic algorithms (slower; may reject some ops).",
    )


def _add_output_args(parser: argparse.ArgumentParser, *, dry_run: bool = True) -> None:
    group = parser.add_argument_group("output")
    group.add_argument("-o", "--output", required=True, metavar="DIR", help="Output directory for the compressed model.")
    group.add_argument("--overwrite", action="store_true", help="Allow writing into a non-empty output directory.")
    if dry_run:
        group.add_argument("--dry-run", action="store_true", help="Validate and print the plan without loading the model.")


def _add_ignore_args(parser: argparse.ArgumentParser, *, default: bool = True) -> None:
    group = parser.add_argument_group("module selection")
    group.add_argument(
        "--targets",
        action="append",
        metavar="MODULE",
        help="Target module class or name/pattern; repeatable (default: Linear).",
    )
    group.add_argument(
        "--ignore",
        action="append",
        metavar="PATTERN",
        help="Module name/pattern to skip; repeatable" + (" (default: lm_head)." if default else "."),
    )
    group.add_argument("--no-ignore", action="store_true", help="Do not apply the default lm_head ignore.")


def _add_quantize_method_args(parser: argparse.ArgumentParser) -> None:
    group = parser.add_argument_group("quantization method")
    group.add_argument(
        "--method",
        action="append",
        metavar="METHOD",
        help=(
            "Quantizer/transform: gptq, rtn, autoround, awq, smoothquant, quip, "
            "spinquant, log-equalization, qad. Repeat to compose."
        ),
    )
    group.add_argument("--scheme", metavar="SCHEME", help="Preset scheme, e.g. W4A16, W8A8, FP8_DYNAMIC, NVFP4.")
    group.add_argument(
        "--group-size",
        type=int,
        metavar="N",
        help="Override the scheme's weight/activation group size (must divide weight columns).",
    )
    group.add_argument("--kv-cache", choices=["fp8", "int8", "none"], help="KV-cache quantization preset.")
    group.add_argument("--kv-cache-json", metavar="JSON", help="Raw QuantizationArgs JSON for KV-cache quantization.")
    group.add_argument("--weight-observer", help="Weight observer override (e.g. mse, minmax).")
    group.add_argument("--input-observer", help="Input activation observer override.")
    group.add_argument("--output-observer", help="Output activation observer override.")
    group.add_argument("--bypass-divisibility-checks", action="store_true", help="Skip group-size divisibility checks.")

    gptq = parser.add_argument_group("GPTQ options")
    gptq.add_argument("--gptq-block-size", type=int, help="GPTQ Hessian block size (default: 128).")
    gptq.add_argument("--gptq-dampening-frac", type=float, help="GPTQ Hessian dampening fraction (default: 0.01).")
    gptq.add_argument("--gptq-actorder", choices=["static", "group"], help="GPTQ activation ordering.")
    gptq.add_argument(
        "--gptq-batched-quantization",
        choices=["auto", "true", "false"],
        help="GPTQ batched quantization toggle.",
    )

    awq = parser.add_argument_group("AWQ options")
    awq.add_argument("--awq-duo-scaling", choices=["true", "false", "both"], help="AWQ duo scaling mode.")
    awq.add_argument("--awq-grid", type=int, help="AWQ scaling grid size (default: 20).")

    smooth = parser.add_argument_group("SmoothQuant options")
    smooth.add_argument("--smoothquant-strength", type=float, help="Smoothing strength (default: 0.5).")
    smooth.add_argument("--smoothquant-steps", type=int, help="Number of calibration steps.")

    auto = parser.add_argument_group("AutoRound options")
    auto.add_argument("--autoround-iters", type=int, help="AutoRound iterations (default: 200).")
    auto.add_argument("--autoround-lr", type=float, help="AutoRound learning rate.")
    auto.add_argument("--autoround-batch-size", type=int, help="AutoRound batch size (default: 8).")
    auto.add_argument("--autoround-device-ids", help="AutoRound device ids, e.g. 0,1.")
    auto.add_argument("--autoround-disable-opt-rtn", action="store_true", help="Disable AutoRound opt-RTN.")
    auto.add_argument("--autoround-no-torch-compile", action="store_true", help="Disable torch.compile in AutoRound.")

    quip = parser.add_argument_group("QuIP options")
    quip.add_argument("--quip-rotations", action="append", metavar="ROT", help="QuIP rotations, e.g. v.")
    quip.add_argument("--quip-transform-type", help="QuIP transform type (default: random-hadamard).")
    quip.add_argument("--quip-block-size", type=int, help="QuIP transform block size.")

    spin = parser.add_argument_group("SpinQuant options")
    spin.add_argument("--spinquant-rotations", action="append", metavar="ROT", help="SpinQuant rotations, e.g. R1, R2, R4.")
    spin.add_argument("--spinquant-transform-type", help="SpinQuant transform type (default: hadamard).")
    spin.add_argument("--spinquant-block-size", type=int, help="SpinQuant transform block size.")

    qad = parser.add_argument_group("QAD options")
    qad.add_argument("--qad-epochs", type=int, help="QAD epochs (default: 1).")
    qad.add_argument("--qad-lr", type=float, help="QAD learning rate (default: 2e-6).")
    qad.add_argument("--qad-accum", type=int, help="QAD gradient accumulation steps.")
    qad.add_argument("--qad-targets-per-subgraph", type=int, help="Decoder layers trained jointly by QAD.")
    qad.add_argument("--qad-device", default=None, help="QAD offload device (default: cpu).")


def _add_local_dataset_args(parser: argparse.ArgumentParser) -> None:
    group = parser.add_argument_group("dataset inspection")
    group.add_argument("--config-name", metavar="NAME", help="Dataset configuration name.")
    group.add_argument("--split", metavar="SPLIT", help="Split to inspect.")
    group.add_argument("--streaming", action="store_true", help="Use streaming mode.")
    group.add_argument("--revision", metavar="REV", help="Dataset revision.")
    group.add_argument("--sample-rows", type=int, default=3, help="Rows to show (default: 3; 0 disables).")
    group.add_argument("--text-column", metavar="COL", help="Preferred text column to report.")


def _add_converter_args(parser: argparse.ArgumentParser) -> None:
    group = parser.add_argument_group("checkpoint conversion")
    group.add_argument("--max-workers", default="auto", metavar="N|auto", help="Concurrent shard workers (default: auto).")
    group.add_argument("--device", action="append", metavar="DEV", help="Quantization device(s), e.g. cuda:0; repeatable or comma-separated.")


def build_parser() -> AgentArgumentParser:
    parser = AgentArgumentParser(
        prog=PROG,
        description=DESCRIPTION,
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    _add_common(parser, suppress=False)
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")
    sub.required = True
    _subparsers_ref[:] = [sub]

    doctor_p = _new_parser(
        "doctor",
        help_text="Check Python, packages, CUDA, GPU, and disk.",
        description="Check the runtime environment and report actionable issues.",
        epilog="Example:\n  llmc doctor --json",
    )
    doctor_p.set_defaults(func=_cmd_doctor)

    schemes_p = _new_parser(
        "schemes",
        help_text="List or inspect preset quantization schemes.",
        description="List the quantization schemes bundled with compressed-tensors.",
        epilog="Examples:\n  llmc schemes\n  llmc schemes --name W4A16 --json",
    )
    schemes_p.add_argument("--name", metavar="SCHEME", help="Show one scheme in detail.")
    schemes_p.set_defaults(func=_cmd_schemes)

    modifiers_p = _new_parser(
        "modifiers",
        help_text="List or inspect recipe modifiers.",
        description="List llm-compressor recipe modifiers and their parameters.",
        epilog="Examples:\n  llmc modifiers\n  llmc modifiers --name GPTQModifier --json",
    )
    modifiers_p.add_argument("--name", metavar="MODIFIER", help="Show one modifier and its fields.")
    modifiers_p.add_argument("--include-internal", action="store_true", help="Include internal lifecycle fields.")
    modifiers_p.set_defaults(func=_cmd_modifiers)

    inspect_p = _new_parser(
        "inspect",
        help_text="Inspect a model's config, size, and quantization.",
        description="Inspect model metadata without loading weights.",
        epilog="Examples:\n  llmc inspect Qwen/Qwen2.5-0.5B-Instruct --json\n  llmc inspect ./local-model",
        aliases=["info"],
    )
    inspect_p.add_argument("model", help="Hugging Face model id or local directory.")
    inspect_p.add_argument("--revision", default="main", help="Model revision (default: main).")
    inspect_p.add_argument("--trust-remote-code", action="store_true", help="Allow custom config code.")
    inspect_p.add_argument("--local-files-only", action="store_true", help="Do not access the network.")
    inspect_p.set_defaults(func=_cmd_inspect)

    dataset_p = _new_parser(
        "dataset",
        help_text="Inspect calibration datasets.",
        description="Inspect Hugging Face or local calibration datasets.",
        epilog="Examples:\n  llmc dataset inspect ultrachat_200k --config-name default --split train_sft\n  llmc dataset inspect ./calib.jsonl",
    )
    dataset_sub = dataset_p.add_subparsers(dest="dataset_command", metavar="SUBCOMMAND")
    dataset_sub.required = True
    dataset_inspect_p = dataset_sub.add_parser(
        "inspect",
        help="Show columns, splits, sizes, and sample rows.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Examples:\n  llmc dataset inspect ./calib.jsonl --json\n  llmc dataset inspect wikitext --config-name wikitext-2-raw-v1 --split train",
    )
    _add_common(dataset_inspect_p, suppress=True)
    dataset_inspect_p.add_argument("dataset", help="Hugging Face dataset id or local path.")
    _add_local_dataset_args(dataset_inspect_p)
    dataset_inspect_p.set_defaults(func=_cmd_dataset_inspect)

    dataset_list_p = dataset_sub.add_parser(
        "list",
        help="List llm-compressor's built-in calibration datasets.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Example:\n  llmc dataset list --json",
    )
    _add_common(dataset_list_p, suppress=True)
    dataset_list_p.set_defaults(func=_cmd_dataset_list)

    recipe_p = _new_parser(
        "recipe",
        help_text="Validate, show, generate, or list recipes.",
        description="Work with llm-compressor YAML/JSON recipes.",
        epilog="Examples:\n  llmc recipe presets\n  llmc recipe generate --preset w4a16-gptq -o recipe.yaml\n  llmc recipe validate --recipe recipe.yaml --json",
    )
    recipe_sub = recipe_p.add_subparsers(dest="recipe_command", metavar="SUBCOMMAND")
    recipe_sub.required = True
    _subparsers_ref[:] = [recipe_sub]

    recipe_presets_p = _new_parser(
        "presets",
        help_text="List built-in recipes.",
        description="List built-in compression recipe presets.",
        epilog="Example:\n  llmc recipe presets --json",
    )
    recipe_presets_p.set_defaults(func=_cmd_recipe_presets)

    recipe_validate_p = _new_parser(
        "validate",
        help_text="Validate a recipe and print its normalized form.",
        description="Parse a recipe, instantiate every modifier, and print a summary.",
        epilog="Examples:\n  llmc recipe validate --recipe recipe.yaml\n  cat recipe.yaml | llmc recipe validate --recipe - --json",
    )
    recipe_validate_p.add_argument("-r", "--recipe", required=True, metavar="PATH", help="Recipe path, or '-' for stdin.")
    recipe_validate_p.add_argument("--stage", help="Recipe stage to select.")
    recipe_validate_p.set_defaults(func=_cmd_recipe_validate)

    recipe_show_p = _new_parser(
        "show",
        help_text="Print a recipe as normalized YAML.",
        description="Load and serialize a recipe as normalized YAML.",
        epilog="Example:\n  llmc recipe show --recipe recipe.yaml",
    )
    recipe_show_p.add_argument("-r", "--recipe", required=True, metavar="PATH", help="Recipe path, or '-' for stdin.")
    recipe_show_p.add_argument("--stage", help="Recipe stage to select.")
    recipe_show_p.set_defaults(func=_cmd_recipe_show)

    recipe_generate_p = _new_parser(
        "generate",
        help_text="Generate a recipe from a preset or method flags.",
        description="Generate a valid recipe YAML from a preset or from method/scheme flags.",
        epilog=(
            "Examples:\n"
            "  llmc recipe generate --preset w4a16-gptq -o recipe.yaml\n"
            "  llmc recipe generate --method smoothquant --method gptq --scheme W8A8\n"
            "  llmc recipe generate --method rtn --scheme FP8_DYNAMIC --json"
        ),
    )
    recipe_generate_p.add_argument("--preset", metavar="NAME", help="Built-in recipe preset.")
    recipe_generate_p.add_argument("--method", action="append", metavar="METHOD", help="Quantization method; repeatable.")
    recipe_generate_p.add_argument("--scheme", metavar="SCHEME", help="Preset quantization scheme.")
    recipe_generate_p.add_argument("--group-size", type=int, metavar="N", help="Override the scheme group size.")
    recipe_generate_p.add_argument("--targets", action="append", metavar="MODULE", help="Target modules (default: Linear).")
    recipe_generate_p.add_argument("--ignore", action="append", metavar="PATTERN", help="Ignored modules (default: lm_head).")
    recipe_generate_p.add_argument("--no-ignore", action="store_true", help="Do not ignore lm_head.")
    recipe_generate_p.add_argument("--kv-cache", choices=["fp8", "int8", "none"], help="KV-cache quantization preset.")
    recipe_generate_p.add_argument("--kv-cache-json", metavar="JSON", help="Raw QuantizationArgs JSON.")
    recipe_generate_p.add_argument("--method-options", metavar="JSON", help="Extra per-method option object.")
    recipe_generate_p.add_argument("--stage", default="quant", help="Recipe stage name (default: quant).")
    recipe_generate_p.add_argument("-o", "--output", metavar="PATH", help="Write YAML here instead of stdout.")
    recipe_generate_p.add_argument("--overwrite", action="store_true", help="Overwrite an existing output file.")
    recipe_generate_p.set_defaults(func=_cmd_recipe_generate)

    _subparsers_ref[:] = [sub]

    quantize_p = _new_parser(
        "quantize",
        help_text="Quantize a model with calibration or data-free methods.",
        description=(
            "Build a recipe from --method/--scheme and run llm-compressor oneshot. "
            "Transforms (awq, smoothquant, quip, spinquant) compose with one quantizer (gptq, rtn, autoround)."
        ),
        epilog=(
            "Examples:\n"
            "  llmc quantize Qwen/Qwen2.5-0.5B-Instruct -o out --method gptq --scheme W4A16 "
            "--dataset ultrachat_200k --split train_sft --num-samples 256\n"
            "  llmc quantize ./model -o out --method rtn --scheme FP8_DYNAMIC --dry-run\n"
            "  llmc quantize ./model -o out --method awq --scheme W4A16_ASYM --dataset-path calib.jsonl"
        ),
        aliases=["quant"],
    )
    quantize_p.add_argument("model", help="Hugging Face model id or local directory.")
    _add_output_args(quantize_p)
    _add_quantize_method_args(quantize_p)
    _add_ignore_args(quantize_p)
    _add_dataset_args(quantize_p)
    _add_model_args(quantize_p)
    _add_oneshot_runtime_args(quantize_p)
    quantize_p.set_defaults(func=_cmd_quantize)

    apply_p = _new_parser(
        "apply",
        help_text="Apply an arbitrary recipe with oneshot.",
        description="Run llm-compressor oneshot with a user-provided YAML/JSON/Markdown recipe.",
        epilog=(
            "Examples:\n"
            "  llmc apply ./model -r recipe.yaml -o out --dataset-path calib.jsonl\n"
            "  cat recipe.yaml | llmc apply ./model -r - -o out --dry-run"
        ),
        aliases=["run"],
    )
    apply_p.add_argument("model", help="Hugging Face model id or local directory.")
    apply_p.add_argument("-r", "--recipe", required=True, metavar="PATH", help="Recipe path, or '-' for stdin.")
    _add_output_args(apply_p)
    _add_dataset_args(apply_p)
    _add_model_args(apply_p)
    _add_oneshot_runtime_args(apply_p, with_stage=True)
    apply_p.set_defaults(func=_cmd_apply)

    prune_p = _new_parser(
        "prune",
        help_text="Prune MoE experts (REAP/MoNe), optionally then quantize.",
        description="Apply REAP or MoNe expert pruning; optionally chain a quantizer in the same oneshot run.",
        epilog=(
            "Examples:\n"
            "  llmc prune ./moe-model -o pruned --method reap --sparsity 0.25 --dataset ultrachat_200k\n"
            "  llmc prune ./moe-model -o pruned-q --method reap --sparsity 0.25 --report-path reap.json "
            "--quant-method rtn --scheme FP8_DYNAMIC --dataset ultrachat_200k"
        ),
    )
    prune_p.add_argument("model", help="Hugging Face model id or local directory.")
    _add_output_args(prune_p)
    prune_p.add_argument("--method", required=True, choices=["reap", "mone"], help="Pruning method.")
    prune_p.add_argument("--sparsity", required=True, type=float, help="Fraction of experts to prune, in [0, 1).")
    prune_p.add_argument("--report-path", metavar="PATH", help="Write REAP/MoNe saliency statistics to this JSON file.")
    prune_p.add_argument("--quant-method", choices=["gptq", "rtn", "autoround"], help="Optional quantizer to chain after pruning.")
    prune_p.add_argument("--scheme", metavar="SCHEME", help="Optional quantization scheme for the chained quantizer.")
    prune_p.add_argument("--group-size", type=int, metavar="N", help="Group size for the chained quantizer.")
    _add_ignore_args(prune_p)
    _add_dataset_args(prune_p)
    _add_model_args(prune_p)
    _add_oneshot_runtime_args(prune_p)
    prune_p.set_defaults(func=_cmd_prune)

    model_free_p = _new_parser(
        "model-free",
        help_text="Data-free checkpoint PTQ without loading a model.",
        description="Quantize safetensors directly with compressed-tensors, without a transformers model definition.",
        epilog=(
            "Examples:\n"
            "  llmc model-free ./model -o out --scheme FP8_DYNAMIC\n"
            "  llmc model-free Qwen/Qwen3-8B -o out --scheme NVFP4A16 --device cuda:0 --max-workers 2"
        ),
        aliases=["mfptq"],
    )
    model_free_p.add_argument("model", help="Hugging Face model id or local checkpoint directory.")
    _add_output_args(model_free_p)
    model_free_p.add_argument("--scheme", metavar="SCHEME", help="Data-free preset scheme.")
    model_free_p.add_argument("--group-size", type=int, metavar="N", help="Override the scheme group size.")
    model_free_p.add_argument("--config", metavar="PATH", help="compressed-tensors QuantizationConfig JSON.")
    model_free_p.add_argument(
        "--ignore",
        action="append",
        metavar="PATTERN",
        help="Ignored modules (default: lm_head, embed_tokens, MoE router gates).",
    )
    model_free_p.add_argument("--no-ignore", action="store_true", help="Do not ignore lm_head.")
    _add_converter_args(model_free_p)
    model_free_p.set_defaults(func=_cmd_model_free)

    expert_p = _new_parser(
        "expert-prune",
        help_text="Prune MoE experts directly in a checkpoint.",
        description="Score and slice MoE experts in safetensors; optionally chain data-free PTQ.",
        epilog=(
            "Examples:\n"
            "  llmc expert-prune ./moe -o pruned --sparsity 0.25 --metric magnitude\n"
            "  llmc expert-prune ./moe -o pruned --sparsity 0.25 --metric saliency "
            "--saliency-report reap.json --scheme FP8_DYNAMIC"
        ),
        aliases=["eprune"],
    )
    expert_p.add_argument("model", help="Hugging Face model id or local checkpoint directory.")
    _add_output_args(expert_p)
    expert_p.add_argument("--sparsity", required=True, type=float, help="Fraction of experts to prune, in [0, 1].")
    expert_p.add_argument(
        "--metric",
        required=True,
        choices=["magnitude", "saliency", "layerwise_saliency", "count"],
        help="Expert scoring metric.",
    )
    expert_p.add_argument("--no-uniform", dest="uniform", action="store_false", default=True, help="Prune globally instead of per layer.")
    expert_p.add_argument("--router-pattern", default=r"mlp\.gate\.weight$", help="Regex for router weights.")
    expert_p.add_argument("--expert-pattern", default=r"mlp\.experts\.(gate_up_proj|down_proj)", help="Regex for expert weights.")
    expert_p.add_argument("--aux-pattern", default=r"(?:mlp|ffn)\.gate\.bias$", help="Regex for per-expert router vectors; 'none' disables.")
    expert_p.add_argument("--index-pattern", default=r"(?:mlp|ffn)\.gate\.tid2eid$", help="Regex for expert-index tables; 'none' disables.")
    expert_p.add_argument("--num-experts-config-key", help="config.json key holding the expert count.")
    expert_p.add_argument("--saliency-report", metavar="PATH", help="REAP saliency report required by non-magnitude metrics.")
    expert_p.add_argument("--scheme", metavar="SCHEME", help="Optional data-free scheme to apply after pruning.")
    expert_p.add_argument("--group-size", type=int, metavar="N", help="Group size for the chained data-free scheme.")
    expert_p.add_argument(
        "--ignore",
        action="append",
        metavar="PATTERN",
        help="Ignored modules when --scheme is used (default: lm_head, embed_tokens, MoE routers).",
    )
    expert_p.add_argument("--no-ignore", action="store_true", help="Do not ignore lm_head when --scheme is used.")
    _add_converter_args(expert_p)
    expert_p.set_defaults(func=_cmd_expert_prune)

    verify_p = _new_parser(
        "verify",
        help_text="Verify a compressed-tensors checkpoint.",
        description="Check config.json, safetensors shards, index consistency, quantization tensors, and optionally load configs.",
        epilog=(
            "Examples:\n"
            "  llmc verify out --json\n"
            "  llmc verify out --strict --load-check"
        ),
        aliases=["check"],
    )
    verify_p.add_argument("path", help="Model directory to verify.")
    verify_p.add_argument("--strict", action="store_true", help="Treat warnings as errors.")
    verify_p.add_argument("--load-check", action="store_true", help="Load config/tokenizer and the model on the meta device.")
    verify_p.set_defaults(func=_cmd_verify)

    version_p = _new_parser(
        "version",
        help_text="Print package versions.",
        description="Print llmc-cli and llm-compressor package versions without importing torch.",
        epilog="Example:\n  llmc version --json",
    )
    version_p.set_defaults(func=_cmd_version)

    schema_p = _new_parser(
        "schema",
        help_text="Emit the machine-readable command manifest.",
        description="Emit a self-describing JSON manifest of every command, argument, and exit code.",
        epilog="Examples:\n  llmc schema\n  llmc schema -o manifest.json",
    )
    schema_p.add_argument("-o", "--output", metavar="PATH", help="Write the manifest to a file.")
    schema_p.set_defaults(func=_cmd_schema)

    return parser


# ---------------------------------------------------------------------------
# helpers


def _command_name(args: argparse.Namespace) -> str:
    if args.command == "recipe":
        return f"recipe {getattr(args, 'recipe_command', '')}".strip()
    if args.command == "dataset":
        return f"dataset {getattr(args, 'dataset_command', '')}".strip()
    return args.command


def _flatten_values(values: list[str] | None) -> list[str]:
    result: list[str] = []
    for value in values or []:
        result.extend(part.strip() for part in value.split(",") if part.strip())
    return result


def _ignore_list(args: argparse.Namespace) -> list[str]:
    if getattr(args, "no_ignore", False):
        return []
    values = getattr(args, "ignore", None)
    return list(values) if values else ["lm_head"]


def _model_free_ignore(args: argparse.Namespace) -> list[str]:
    """Model-free PTQ matches all 2D weights, so protect embeddings and routers."""
    if getattr(args, "no_ignore", False):
        return []
    values = getattr(args, "ignore", None)
    if values:
        return list(values)
    return ["lm_head", "re:.*embed_tokens.*", "re:.*mlp.gate$"]


def _targets_list(args: argparse.Namespace) -> list[str]:
    return _flatten_values(getattr(args, "targets", None)) or ["Linear"]


def _dataset_options(args: argparse.Namespace) -> DatasetOptions:
    return DatasetOptions(
        dataset=getattr(args, "dataset", None),
        dataset_path=getattr(args, "dataset_path", None),
        split=getattr(args, "split", None),
        config_name=getattr(args, "dataset_config", None),
        num_samples=getattr(args, "num_samples", 512),
        max_seq_length=getattr(args, "max_seq_length", 2048),
        batch_size=getattr(args, "batch_size", 1),
        text_column=getattr(args, "text_column", "text"),
        data_collator=getattr(args, "data_collator", "truncation"),
        shuffle=getattr(args, "shuffle", True),
        streaming=getattr(args, "streaming", False),
        overwrite_cache=getattr(args, "overwrite_cache", False),
        preprocessing_workers=getattr(args, "preprocessing_workers", None),
        dataloader_workers=getattr(args, "dataloader_workers", 0),
        concatenate=getattr(args, "concatenate", False),
        pad_to_max_length=getattr(args, "pad_to_max_length", False),
        moe_calibrate_all_experts=getattr(args, "moe_calibrate_all_experts", True),
        min_tokens_per_module=getattr(args, "min_tokens_per_module", None),
    )


def _oneshot_options(args: argparse.Namespace, *, stage: str | None = None) -> OneshotOptions:
    pipeline = getattr(args, "pipeline", "auto")
    return OneshotOptions(
        output_dir=args.output,
        overwrite=getattr(args, "overwrite", False),
        dry_run=getattr(args, "dry_run", False),
        seed=getattr(args, "seed", 42),
        json_mode=getattr(args, "json", False),
        precision=getattr(args, "precision", "auto"),
        revision=getattr(args, "revision", "main"),
        trust_remote_code=getattr(args, "trust_remote_code", False),
        tokenizer=getattr(args, "tokenizer", None),
        processor=getattr(args, "processor", None),
        config_name=getattr(args, "config_name", None),
        tie_word_embeddings=getattr(args, "tie_word_embeddings", True),
        save_compressed=getattr(args, "save_compressed", True),
        pipeline=None if pipeline in (None, "auto") else pipeline,
        sequential_targets=_flatten_values(getattr(args, "sequential_targets", None)) or None,
        sequential_targets_per_subgraph=getattr(args, "sequential_targets_per_subgraph", None),
        sequential_offload_device=getattr(args, "sequential_offload_device", "cpu"),
        stage_weights_in_pinned_memory=getattr(args, "stage_weights_in_pinned_memory", False),
        use_eager_attention=getattr(args, "use_eager_attention", False),
        propagate_error=getattr(args, "propagate_error", True),
        log_sequential_error=getattr(args, "log_sequential_error", False),
        recipe_args=getattr(args, "recipe_args", None),
        stage=stage,
        clear_sparse_session=getattr(args, "clear_sparse_session", False),
        log_dir=getattr(args, "run_log_dir", None),
        offline=getattr(args, "offline", False),
        deterministic=getattr(args, "deterministic", False),
    )


def _parse_optional_bool(value: str | None):
    if value is None:
        return None
    if value == "true":
        return True
    if value == "false":
        return False
    return value


def _method_options(args: argparse.Namespace) -> dict[str, dict[str, Any]]:
    options: dict[str, dict[str, Any]] = {}

    def put(method: str, key: str, value: Any) -> None:
        if value is not None:
            options.setdefault(method, {})[key] = value

    put("gptq", "block_size", getattr(args, "gptq_block_size", None))
    put("gptq", "dampening_frac", getattr(args, "gptq_dampening_frac", None))
    put("gptq", "actorder", getattr(args, "gptq_actorder", None))
    put("gptq", "batched_quantization", _parse_optional_bool(getattr(args, "gptq_batched_quantization", None)))
    put("awq", "duo_scaling", _parse_optional_bool(getattr(args, "awq_duo_scaling", None)))
    put("awq", "n_grid", getattr(args, "awq_grid", None))
    put("smoothquant", "smoothing_strength", getattr(args, "smoothquant_strength", None))
    put("smoothquant", "num_calibration_steps", getattr(args, "smoothquant_steps", None))
    put("autoround", "iters", getattr(args, "autoround_iters", None))
    put("autoround", "lr", getattr(args, "autoround_lr", None))
    put("autoround", "batch_size", getattr(args, "autoround_batch_size", None))
    put("autoround", "device_ids", getattr(args, "autoround_device_ids", None))
    put("autoround", "disable_opt_rtn", True if getattr(args, "autoround_disable_opt_rtn", False) else None)
    put("autoround", "enable_torch_compile", False if getattr(args, "autoround_no_torch_compile", False) else None)
    put("quip", "rotations", _flatten_values(getattr(args, "quip_rotations", None)) or None)
    put("quip", "transform_type", getattr(args, "quip_transform_type", None))
    put("quip", "transform_block_size", getattr(args, "quip_block_size", None))
    put("spinquant", "rotations", _flatten_values(getattr(args, "spinquant_rotations", None)) or None)
    put("spinquant", "transform_type", getattr(args, "spinquant_transform_type", None))
    put("spinquant", "transform_block_size", getattr(args, "spinquant_block_size", None))
    put("qad", "num_epochs", getattr(args, "qad_epochs", None))
    put("qad", "lr", getattr(args, "qad_lr", None))
    put("qad", "gradient_accumulation_steps", getattr(args, "qad_accum", None))
    put("qad", "offload_device", getattr(args, "qad_device", None))
    put(
        "quant",
        "weight_observer",
        getattr(args, "weight_observer", None),
    )
    put("quant", "input_observer", getattr(args, "input_observer", None))
    put("quant", "output_observer", getattr(args, "output_observer", None))
    put(
        "quant",
        "bypass_divisibility_checks",
        True if getattr(args, "bypass_divisibility_checks", False) else None,
    )
    return options


def _validate_method_options(methods: list[str], options: dict[str, dict[str, Any]]) -> None:
    for method in ("gptq", "awq", "smoothquant", "log-equalization", "autoround", "quip", "spinquant", "qad"):
        if method in options and method not in methods:
            raise InputError(
                f"Options for {method} were provided but --method {method} was not selected",
                hint=f"Add --method {method} to the command line.",
            )


def _parse_devices(values: list[str] | None) -> list[str] | None:
    devices = _flatten_values(values)
    return devices or None


def _parse_max_workers(value: str) -> int | str:
    if str(value).lower() == "auto":
        return "auto"
    try:
        parsed = int(value)
    except ValueError as exc:
        raise InputError(f"--max-workers must be an integer or 'auto', got {value!r}") from exc
    if parsed < 1:
        raise InputError("--max-workers must be >= 1")
    return parsed


def _parse_method_options_json(raw: str | None) -> dict[str, dict[str, Any]]:
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise InputError(f"Invalid --method-options JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise InputError("--method-options must be a JSON object keyed by method")
    for key, value in data.items():
        if not isinstance(value, dict):
            raise InputError(f"--method-options[{key!r}] must be an object")
    return data


# ---------------------------------------------------------------------------
# command handlers


def _cmd_doctor(args: argparse.Namespace):
    data = doctor()
    return data, formatting.fmt_doctor(data)


def _cmd_schemes(args: argparse.Namespace):
    if args.name:
        data = catalog.scheme_details(args.name)
        return data, formatting.fmt_scheme_detail(data)
    data = catalog.list_schemes()
    return {"schemes": data}, formatting.fmt_schemes(data)


def _cmd_modifiers(args: argparse.Namespace):
    if args.name:
        data = catalog.modifier_details(args.name, include_internal=args.include_internal)
        return data, formatting.fmt_modifier_detail(data)
    data = catalog.list_modifiers(include_internal=args.include_internal)
    return {"modifiers": data}, formatting.fmt_modifiers(data)


def _cmd_inspect(args: argparse.Namespace):
    data = inspect_model(
        args.model,
        revision=args.revision,
        trust_remote_code=args.trust_remote_code,
        local_files_only=args.local_files_only,
    )
    return data, human_model_summary(data)


def _cmd_dataset_inspect(args: argparse.Namespace):
    data = inspect_dataset(
        args.dataset,
        config_name=args.config_name,
        split=args.split,
        streaming=args.streaming,
        revision=args.revision,
        sample_rows=args.sample_rows,
        text_column=args.text_column,
    )
    return data, formatting.fmt_dataset(data)


def _cmd_dataset_list(args: argparse.Namespace):
    from llmc_cli.data import builtin_dataset_list

    data = builtin_dataset_list()
    return {"datasets": data}, formatting.fmt_dataset_list(data)


def _cmd_recipe_presets(args: argparse.Namespace):
    data = catalog.list_presets()
    return {"presets": data}, formatting.fmt_recipe_presets(data)


def _recipe_validate_data(recipe: Any, source: str) -> dict[str, Any]:
    data = {
        "source": source,
        **_recipe_summary_data(recipe),
        "recipe": recipe_to_dict(recipe),
        "yaml": recipe_to_yaml(recipe),
    }
    return data


def _recipe_summary_data(recipe: Any) -> dict[str, Any]:
    summary = recipe_summary(recipe)
    return summary


def _cmd_recipe_validate(args: argparse.Namespace):
    recipe = load_recipe(args.recipe, stage=args.stage)
    data = _recipe_validate_data(recipe, args.recipe)
    return data, formatting.fmt_recipe_validate(data)


def _cmd_recipe_show(args: argparse.Namespace):
    recipe = load_recipe(args.recipe, stage=args.stage)
    yaml_text = recipe_to_yaml(recipe)
    data = {
        "source": args.recipe,
        "stage": recipe.stage,
        "yaml": yaml_text,
        "recipe": recipe_to_dict(recipe),
    }
    return data, yaml_text


def _cmd_recipe_generate(args: argparse.Namespace):
    if args.preset and args.method:
        raise InputError("Pass either --preset or --method, not both")
    if args.preset:
        conflicts = [
            name
            for name, value in (
                ("--method", args.method),
                ("--scheme", args.scheme),
                ("--group-size", args.group_size),
                ("--targets", args.targets),
                ("--ignore", args.ignore),
                ("--no-ignore", args.no_ignore),
                ("--kv-cache", args.kv_cache),
                ("--kv-cache-json", args.kv_cache_json),
                ("--method-options", args.method_options),
            )
            if value
        ]
        if conflicts:
            raise InputError(
                f"--preset cannot be combined with {', '.join(conflicts)}",
                hint="Either use a preset as-is, or build a recipe with --method/--scheme flags.",
            )
        recipe, _specs, _stage = recipe_from_preset(args.preset)
    else:
        methods = _flatten_values(args.method) or ["rtn"]
        ignore = [] if args.no_ignore else (args.ignore or ["lm_head"])
        options = _parse_method_options_json(args.method_options)
        specs, _scheme, _requires = build_quantize_specs(
            methods,
            args.scheme,
            targets=_targets_list(args),
            ignore=ignore,
            kv_cache=args.kv_cache,
            kv_cache_json=args.kv_cache_json,
            group_size=args.group_size,
            options=options,
        )
        recipe, _specs = recipe_from_specs(specs, stage=args.stage)

    yaml_text = recipe_to_yaml(recipe)
    written = None
    if args.output:
        target = Path(args.output).expanduser()
        if target.exists() and not args.overwrite:
            raise InputError(
                f"Refusing to overwrite existing recipe: {target}",
                hint="Pass --overwrite or choose another output path.",
            )
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(yaml_text, encoding="utf-8")
        written = str(target)
    data = {
        "output": written,
        "stage": recipe.stage,
        "yaml": yaml_text,
        "recipe": recipe_to_dict(recipe),
    }
    human = yaml_text if not written else f"wrote {written}\n\n{yaml_text}"
    return data, human


def _cmd_quantize(args: argparse.Namespace):
    methods = [m.lower() for m in _flatten_values(args.method)] or ["rtn"]
    options = _method_options(args)
    _validate_method_options(methods, options)
    if getattr(args, "qad_targets_per_subgraph", None) is not None:
        if "qad" not in methods:
            raise InputError("--qad-targets-per-subgraph requires --method qad")
        args.sequential_targets_per_subgraph = args.qad_targets_per_subgraph
    data = run_quantize(
        model=args.model,
        methods=methods,
        scheme=args.scheme,
        targets=_targets_list(args),
        ignore=_ignore_list(args),
        kv_cache=args.kv_cache,
        kv_cache_json=args.kv_cache_json,
        group_size=args.group_size,
        method_options=options,
        dataset_options=_dataset_options(args),
        options=_oneshot_options(args),
    )
    return data, formatting.fmt_run(data)


def _cmd_apply(args: argparse.Namespace):
    recipe = load_recipe(args.recipe, stage=args.stage)
    data = run_apply(
        model=args.model,
        recipe=recipe,
        dataset_options=_dataset_options(args),
        options=_oneshot_options(args),
    )
    return data, formatting.fmt_run(data)


def _cmd_prune(args: argparse.Namespace):
    specs = build_prune_specs(
        args.method,
        args.sparsity,
        ignore=_ignore_list(args),
        report_path=args.report_path,
        quant_method=args.quant_method,
        scheme=args.scheme,
        targets=_targets_list(args),
        group_size=args.group_size,
    )
    data = run_prune(
        model=args.model,
        specs=specs,
        dataset_options=_dataset_options(args),
        options=_oneshot_options(args),
    )
    return data, formatting.fmt_run(data)


def _cmd_model_free(args: argparse.Namespace):
    data = run_model_free(
        model=args.model,
        output_dir=args.output,
        scheme=args.scheme,
        group_size=args.group_size,
        config_path=args.config,
        ignore=_model_free_ignore(args),
        max_workers=_parse_max_workers(args.max_workers),
        devices=_parse_devices(args.device),
        overwrite=args.overwrite,
        dry_run=args.dry_run,
        json_mode=args.json,
    )
    return data, formatting.fmt_run(data)


def _cmd_expert_prune(args: argparse.Namespace):
    aux_pattern = None if str(args.aux_pattern).lower() == "none" else args.aux_pattern
    index_pattern = None if str(args.index_pattern).lower() == "none" else args.index_pattern
    data = run_expert_prune(
        model=args.model,
        output_dir=args.output,
        sparsity=args.sparsity,
        metric=args.metric,
        uniform=args.uniform,
        router_pattern=args.router_pattern,
        expert_pattern=args.expert_pattern,
        aux_pattern=aux_pattern,
        index_pattern=index_pattern,
        num_experts_config_key=args.num_experts_config_key,
        saliency_report_path=args.saliency_report,
        scheme=args.scheme,
        group_size=args.group_size,
        ignore=_model_free_ignore(args),
        max_workers=_parse_max_workers(args.max_workers),
        devices=_parse_devices(args.device),
        overwrite=args.overwrite,
        dry_run=args.dry_run,
        json_mode=args.json,
    )
    return data, formatting.fmt_run(data)


def _cmd_verify(args: argparse.Namespace):
    data = verify_model(args.path, strict=args.strict, load_check=args.load_check)
    return data, formatting.fmt_verify(data)


def _cmd_version(args: argparse.Namespace):
    from importlib import metadata as importlib_metadata

    def version(name: str) -> str | None:
        try:
            return importlib_metadata.version(name)
        except importlib_metadata.PackageNotFoundError:
            return None

    data = {
        "llmc": __version__,
        "llmcompressor": version("llmcompressor"),
        "compressed-tensors": version("compressed-tensors"),
        "transformers": version("transformers"),
        "torch": version("torch"),
        "python": sys.version.split()[0],
    }
    return data, formatting.fmt_version(data)


def _cmd_schema(args: argparse.Namespace):
    manifest = parser_schema(build_parser())
    if args.output:
        target = Path(args.output).expanduser()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        data = {"output": str(target), "commands": sorted(manifest["commands"]), "protocol": manifest["protocol"]}
        return data, f"wrote agent manifest to {target}"
    data = manifest
    return data, json.dumps(manifest, indent=2, ensure_ascii=False)


# ---------------------------------------------------------------------------
# schema introspection


def parser_schema(parser: argparse.ArgumentParser) -> dict[str, Any]:
    commands: dict[str, Any] = {}
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            seen: set[int] = set()
            for name, subparser in action.choices.items():
                if id(subparser) in seen:
                    continue
                seen.add(id(subparser))
                commands[name] = _parser_entry(subparser, name)
    options = [_action_entry(action) for action in parser._actions if _is_user_action(action)]
    return {
        "name": PROG,
        "version": __version__,
        "protocol": "llmc-agent-cli/1",
        "description": DESCRIPTION,
        "conventions": {
            "stdout": "exactly one JSON object when --json is passed",
            "stderr": "logs/progress; --progress json emits newline-delimited JSON records",
            "interactive": False,
            "exit_codes": EXIT_CODES,
            "recommended_flow": [
                "llmc doctor --json",
                "llmc inspect MODEL --json",
                "llmc schemes --json",
                "llmc recipe generate --preset w4a16-gptq --json",
                "llmc quantize MODEL -o OUT --method gptq --scheme W4A16 --dataset PATH --json",
                "llmc verify OUT --json",
            ],
        },
        "global_options": options,
        "commands": commands,
    }


def _parser_entry(parser: argparse.ArgumentParser, name: str) -> dict[str, Any]:
    subcommands: dict[str, Any] = {}
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            seen: set[int] = set()
            for sub_name, subparser in action.choices.items():
                if id(subparser) in seen:
                    continue
                seen.add(id(subparser))
                subcommands[sub_name] = _parser_entry(subparser, sub_name)
    aliases = getattr(parser, "_llmc_aliases", [])
    options = [_action_entry(action) for action in parser._actions if _is_user_action(action)]
    return {
        "description": parser.description,
        "aliases": list(aliases),
        "options": options,
        "examples": list(getattr(parser, "_llmc_examples", [])),
        **({"subcommands": subcommands} if subcommands else {}),
    }


def _is_user_action(action: argparse.Action) -> bool:
    return action.dest not in {"help", "command", "recipe_command", "dataset_command"} and not isinstance(
        action, argparse._SubParsersAction
    )


def _action_entry(action: argparse.Action) -> dict[str, Any]:
    default = action.default
    if default is argparse.SUPPRESS:
        default = None
    if callable(default):
        default = None
    type_name = None
    if action.type is int:
        type_name = "integer"
    elif action.type is float:
        type_name = "number"
    elif action.type is str:
        type_name = "string"
    if isinstance(action, (argparse._StoreTrueAction, argparse._StoreFalseAction)):
        type_name = "boolean"
    if isinstance(action, argparse._AppendAction):
        type_name = "array"
    return {
        "flags": list(action.option_strings),
        "dest": action.dest,
        "positional": not bool(action.option_strings),
        "required": bool(action.required) if action.option_strings else action.nargs not in ("?", "*"),
        "type": type_name,
        "nargs": action.nargs,
        "choices": list(action.choices) if action.choices else None,
        "default": default,
        "help": action.help,
    }


# ---------------------------------------------------------------------------
# main


def _resolve_log_level(args: argparse.Namespace) -> str:
    explicit = getattr(args, "log_level", None)
    if explicit:
        return explicit
    if getattr(args, "quiet", False):
        return "ERROR"
    if getattr(args, "verbose", 0) >= 1:
        return "DEBUG"
    if getattr(args, "progress", None) == "json":
        return "INFO"
    if getattr(args, "json", False):
        return "WARNING"
    return "INFO"


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    json_hint = "--json" in argv
    debug_hint = "--debug" in argv
    started = time.monotonic()
    reporter = Reporter(json_mode=json_hint, debug=debug_hint)
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except UsageError as exc:
        return reporter.failure("cli", exc, started)

    reporter.json_mode = getattr(args, "json", False)
    reporter.debug = getattr(args, "debug", False)
    level = _resolve_log_level(args)
    progress = "off" if getattr(args, "quiet", False) else getattr(args, "progress", "auto")
    configure_logging(level=level, log_file=getattr(args, "log_file", None), progress=progress)
    if getattr(args, "json", False) or progress == "off":
        os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
        os.environ.setdefault("TQDM_DISABLE", "1")

    if getattr(args, "cuda_visible_devices", None):
        os.environ["CUDA_VISIBLE_DEVICES"] = str(args.cuda_visible_devices)
    if getattr(args, "offline", False):
        os.environ.setdefault("HF_HUB_OFFLINE", "1")
        os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
        os.environ.setdefault("HF_DATASETS_OFFLINE", "1")

    command = _command_name(args)
    try:
        data, human = args.func(args)
    except LLMCError as exc:
        return reporter.failure(command, exc, started)
    except KeyboardInterrupt:
        from llmc_cli.errors import LLMCError as BaseError

        interrupted = BaseError("Interrupted by user")
        interrupted.code = "INTERRUPTED"
        interrupted.exit_code = 130
        return reporter.failure(command, interrupted, started)
    except BrokenPipeError:
        return 0
    except Exception as exc:
        return reporter.failure(command, exc, started)

    warnings_list: list[str] = []
    if isinstance(data, dict):
        warnings_list = data.pop("run_warnings", []) or []
    return reporter.success(command, data, started, human=human, warnings_list=warnings_list)


if __name__ == "__main__":
    sys.exit(main())
