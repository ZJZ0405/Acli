"""Command implementations for compression workflows."""

from __future__ import annotations

import os
import random
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from llmc_cli.catalog import scheme_details
from llmc_cli.data import (
    DatasetOptions,
    oneshot_dataset_kwargs,
    resolve_calibration_dataset,
)
from llmc_cli.errors import InputError, RuntimeFailure
from llmc_cli.inspect import verify_model
from llmc_cli.recipes import (
    build_quantize_specs,
    recipe_from_specs,
    recipe_summary,
    recipe_to_dict,
    recipe_to_yaml,
)
from llmc_cli.utils import dir_size


@dataclass
class OneshotOptions:
    output_dir: str
    overwrite: bool = False
    dry_run: bool = False
    seed: int | None = 42
    json_mode: bool = False
    precision: str = "auto"
    revision: str = "main"
    trust_remote_code: bool = False
    tokenizer: str | None = None
    processor: str | None = None
    config_name: str | None = None
    tie_word_embeddings: bool = True
    save_compressed: bool = True
    pipeline: str | None = None
    sequential_targets: list[str] | None = None
    sequential_targets_per_subgraph: int | None = None
    sequential_offload_device: str = "cpu"
    stage_weights_in_pinned_memory: bool = False
    use_eager_attention: bool = False
    propagate_error: bool = True
    log_sequential_error: bool = False
    recipe_args: list[str] | None = None
    stage: str | None = None
    clear_sparse_session: bool = False
    log_dir: str | None = None
    offline: bool = False
    deterministic: bool = False


def seed_everything(seed: int | None, deterministic: bool = False) -> None:
    if seed is None:
        return
    random.seed(seed)
    os.environ["PYTHONHASHSEED"] = str(seed)
    try:
        import numpy as np

        np.random.seed(seed)
    except Exception:
        pass
    try:
        import torch

        torch.manual_seed(seed)
        if torch.cuda.is_available():
            torch.cuda.manual_seed_all(seed)
        if deterministic:
            os.environ.setdefault("CUBLAS_WORKSPACE_CONFIG", ":4096:8")
            torch.use_deterministic_algorithms(True)
            torch.backends.cudnn.deterministic = True
            torch.backends.cudnn.benchmark = False
    except Exception:
        pass
    try:
        from transformers import set_seed

        set_seed(seed)
    except Exception:
        pass


def apply_offline(offline: bool) -> None:
    if not offline:
        return
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
    os.environ.setdefault("HF_DATASETS_OFFLINE", "1")


def _prepare_output(output_dir: str, model: str, *, overwrite: bool, dry_run: bool) -> Path:
    output = Path(output_dir).expanduser()
    if output.exists() and not output.is_dir():
        raise InputError(f"Output path exists and is not a directory: {output_dir}")
    model_path = Path(model).expanduser()
    if model_path.exists() and output.resolve() == model_path.resolve():
        raise InputError(
            "Output directory must not be the input model directory",
            hint="Pass a new directory with -o/--output.",
        )
    if output.exists() and any(output.iterdir()) and not overwrite:
        raise InputError(
            f"Output directory is not empty: {output_dir}",
            hint="Pass --overwrite to write into it, or choose a new directory.",
        )
    if not dry_run:
        output.mkdir(parents=True, exist_ok=True)
    return output


def _validate_dataset_requirement(
    requires_calibration: bool,
    dataset_options: DatasetOptions,
    methods: list[str] | None = None,
) -> None:
    if not requires_calibration:
        return
    if dataset_options.dataset or dataset_options.dataset_path:
        return
    method_text = ", ".join(methods or []) or "this recipe"
    raise InputError(
        f"{method_text} requires calibration data, but no dataset was provided",
        hint=(
            "Pass --dataset <hf-id> (for example --dataset ultrachat_200k "
            "--split train_sft) or --dataset-path <local.jsonl>. "
            "Use --method rtn with a dynamic scheme such as FP8_DYNAMIC for a data-free run."
        ),
    )


def _check_gpu_compatibility(scheme: str | None) -> list[str]:
    if not scheme:
        return []
    info = scheme_details(scheme)
    minimum = tuple(info.get("min_compute_capability") or (0, 0))
    if minimum == (0, 0):
        return []
    try:
        import torch

        if not torch.cuda.is_available():
            return [f"Scheme {scheme} normally needs CUDA compute capability {minimum[0]}.{minimum[1]}; no GPU detected."]
        major, minor = torch.cuda.get_device_capability(0)
        if (major, minor) < minimum:
            return [
                f"Scheme {scheme} targets compute capability {minimum[0]}.{minimum[1]} "
                f"or newer, but this GPU is {major}.{minor}; the checkpoint may not be runnable."
            ]
    except Exception:
        return []
    return []


def _artifact_summary(output: Path) -> dict[str, Any]:
    try:
        result = verify_model(str(output))
    except Exception as exc:
        return {"path": str(output), "status": "unknown", "error": str(exc)}
    return {
        "path": str(output),
        "status": result["status"],
        "errors": result["errors"],
        "warnings": result["warnings"],
        "quantization": result.get("quantization"),
        "files": result.get("files"),
        "tensors": result.get("tensors"),
        "bytes": dir_size(output),
    }


def _execute_oneshot(
    *,
    model: str,
    recipe: Any,
    specs: list[dict[str, Any]],
    scheme: str | None,
    requires_calibration: bool,
    dataset_options: DatasetOptions,
    options: OneshotOptions,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    apply_offline(options.offline)
    _validate_dataset_requirement(
        requires_calibration,
        dataset_options,
        (extra or {}).get("methods"),
    )
    if options.pipeline == "datafree" and requires_calibration:
        raise InputError(
            "The 'datafree' pipeline cannot run modifiers that require calibration",
            hint="Use --pipeline auto or --pipeline sequential, or choose a data-free scheme such as FP8_DYNAMIC.",
        )
    output = _prepare_output(
        options.output_dir,
        model,
        overwrite=options.overwrite,
        dry_run=options.dry_run,
    )
    dataset_value, dataset_meta = resolve_calibration_dataset(dataset_options)
    recipe_data = recipe_to_dict(recipe)
    recipe_yaml = recipe_to_yaml(recipe)
    base = {
        "model": model,
        "output_dir": str(output),
        "scheme": scheme,
        "recipe": recipe_data,
        "recipe_yaml": recipe_yaml,
        "recipe_summary": recipe_summary(recipe),
        "dataset": dataset_meta,
        "requires_calibration": requires_calibration,
        "dry_run": options.dry_run,
        "options": {
            "precision": options.precision,
            "pipeline": options.pipeline or "auto",
            "seed": options.seed,
            "deterministic": options.deterministic,
        },
    }
    if extra:
        base.update(extra)
    base["run_warnings"] = _check_gpu_compatibility(scheme)
    if options.recipe_args:
        base["run_warnings"].append(
            "recipe_args are accepted but are not consumed by llm-compressor 0.15.0"
        )

    if options.dry_run:
        return base

    seed_everything(options.seed, options.deterministic)
    from llmc_cli.output import imported_quietly

    with imported_quietly():
        from llmcompressor import oneshot

    kwargs: dict[str, Any] = {
        "model": model,
        "recipe": recipe,
        "output_dir": str(output),
        "precision": options.precision,
        "trust_remote_code_model": options.trust_remote_code,
        "model_revision": options.revision,
        "tokenizer": options.tokenizer,
        "processor": options.processor,
        "config_name": options.config_name,
        "tie_word_embeddings": options.tie_word_embeddings,
        "save_compressed": options.save_compressed,
        "pipeline": options.pipeline,
        "sequential_targets": options.sequential_targets,
        "sequential_offload_device": options.sequential_offload_device,
        "stage_weights_in_pinned_memory": options.stage_weights_in_pinned_memory,
        "use_eager_attention": options.use_eager_attention,
        "propagate_error": options.propagate_error,
        "log_sequential_error": options.log_sequential_error,
        "recipe_args": options.recipe_args,
        "stage": options.stage,
        "clear_sparse_session": options.clear_sparse_session,
        "log_dir": options.log_dir,
    }
    if options.sequential_targets_per_subgraph is not None:
        kwargs["sequential_targets_per_subgraph"] = options.sequential_targets_per_subgraph
    kwargs.update(oneshot_dataset_kwargs(dataset_options, dataset_value, dataset_meta))
    kwargs = {key: value for key, value in kwargs.items() if value is not None or key == "pipeline"}

    started = time.monotonic()
    try:
        with _guard_stdout(options.json_mode), _capture_python_warnings() as caught:
            oneshot(**kwargs)
    except Exception as exc:
        if _is_oom(exc):
            raise RuntimeFailure(
                f"Out of memory while compressing {model}: {exc}",
                hint=(
                    "Reduce --batch-size or --max-seq-length, lower --num-samples, "
                    "or use --pipeline sequential with --sequential-offload-device cpu."
                ),
            ) from exc
        if "not divisible by group_size" in str(exc):
            raise RuntimeFailure(
                str(exc).splitlines()[0],
                hint=(
                    "Use --group-size with a divisor of the weight column count "
                    "(for example --group-size 64), add the listed layers to --ignore, "
                    "or pass --bypass-divisibility-checks."
                ),
            ) from exc
        raise
    base["elapsed_s"] = round(time.monotonic() - started, 3)
    base["run_warnings"].extend(str(w.message) for w in caught)
    base["output"] = _artifact_summary(output)
    return base


def _capture_python_warnings():
    import warnings

    class _Catcher:
        def __enter__(self):
            self._ctx = warnings.catch_warnings(record=True)
            self.caught = self._ctx.__enter__()
            warnings.simplefilter("default")
            return self.caught

        def __exit__(self, *args):
            return self._ctx.__exit__(*args)

    return _Catcher()


def _guard_stdout(enabled: bool):
    from llmc_cli.output import keep_stdout_clean

    return keep_stdout_clean(enabled)


def _is_oom(exc: BaseException) -> bool:
    try:
        import torch

        if isinstance(exc, torch.cuda.OutOfMemoryError):
            return True
    except Exception:
        pass
    text = str(exc).lower()
    return "out of memory" in text or "cuda error: out of memory" in text


def run_quantize(
    *,
    model: str,
    methods: list[str] | None,
    scheme: str | None,
    targets: list[str] | None,
    ignore: list[str] | None,
    kv_cache: str | None,
    kv_cache_json: str | None,
    group_size: int | None,
    method_options: dict[str, dict[str, Any]],
    dataset_options: DatasetOptions,
    options: OneshotOptions,
) -> dict[str, Any]:
    specs, resolved_scheme, requires_calibration = build_quantize_specs(
        methods,
        scheme,
        targets=targets,
        ignore=ignore,
        kv_cache=kv_cache,
        kv_cache_json=kv_cache_json,
        group_size=group_size,
        options=method_options,
    )
    recipe, specs = recipe_from_specs(specs, stage="quant")
    if options.pipeline is None and "qad" in [m.lower() for m in (methods or [])]:
        options.pipeline = "sequential"
    return _execute_oneshot(
        model=model,
        recipe=recipe,
        specs=specs,
        scheme=resolved_scheme,
        requires_calibration=requires_calibration,
        dataset_options=dataset_options,
        options=options,
        extra={
            "methods": methods or [],
            "targets": targets or ["Linear"],
            "ignore": ignore or ["lm_head"],
            "group_size": group_size,
        },
    )


def run_apply(
    *,
    model: str,
    recipe: Any,
    dataset_options: DatasetOptions,
    options: OneshotOptions,
) -> dict[str, Any]:
    summary = recipe_summary(recipe)
    return _execute_oneshot(
        model=model,
        recipe=recipe,
        specs=[],
        scheme=None,
        requires_calibration=summary["requires_calibration"],
        dataset_options=dataset_options,
        options=options,
        extra={"methods": ["recipe"]},
    )


def run_prune(
    *,
    model: str,
    specs: list[dict[str, Any]],
    dataset_options: DatasetOptions,
    options: OneshotOptions,
) -> dict[str, Any]:
    recipe, specs = recipe_from_specs(specs, stage="prune")
    return _execute_oneshot(
        model=model,
        recipe=recipe,
        specs=specs,
        scheme=None,
        requires_calibration=True,
        dataset_options=dataset_options,
        options=options,
        extra={"methods": [spec["type"] for spec in specs]},
    )


def run_model_free(
    *,
    model: str,
    output_dir: str,
    scheme: str | None,
    group_size: int | None,
    config_path: str | None,
    ignore: list[str] | None,
    max_workers: int | str,
    devices: list[str] | None,
    overwrite: bool,
    dry_run: bool,
    json_mode: bool,
) -> dict[str, Any]:
    if scheme and config_path:
        raise InputError("Pass either --scheme or --config, not both")
    if not scheme and not config_path:
        raise InputError(
            "model-free requires --scheme or --config",
            hint="Example: --scheme FP8_DYNAMIC (data-free schemes only).",
        )
    if group_size is not None and not scheme:
        raise InputError("--group-size requires --scheme")

    config = None
    scheme_name = None
    scheme_value: Any = None
    scheme_payload = None
    if scheme:
        scheme_name = scheme.upper()
        scheme_payload = scheme_details(scheme_name)
        if not scheme_payload["data_free_eligible"]:
            raise InputError(
                f"Scheme {scheme_name} needs calibration and cannot use model-free PTQ",
                hint="Use 'llmc quantize' with calibration data, or pick FP8_DYNAMIC/NVFP4A16/MXFP4A16.",
            )
        scheme_value = scheme_name
        if group_size is not None:
            from compressed_tensors.quantization import preset_name_to_scheme

            if group_size < 1:
                raise InputError("--group-size must be >= 1")
            scheme_obj = preset_name_to_scheme(scheme_name, ["Linear"])
            if scheme_obj.weights is not None:
                scheme_obj.weights.group_size = group_size
            if scheme_obj.input_activations is not None and scheme_obj.input_activations.strategy in {
                "group",
                "tensor_group",
            }:
                scheme_obj.input_activations.group_size = group_size
            scheme_value = scheme_obj
            scheme_payload = dict(scheme_payload)
            scheme_payload["group_size_override"] = group_size

    if config_path:
        from compressed_tensors.quantization import QuantizationConfig

        path = Path(config_path).expanduser()
        if not path.exists():
            raise InputError(f"Quantization config does not exist: {config_path}")
        try:
            config = QuantizationConfig.model_validate_json(path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise InputError(f"Invalid quantization config {config_path}: {exc}") from exc
        scheme_payload = {
            "config_path": str(path),
            "config_groups": {
                name: group.model_dump(mode="json") for name, group in config.config_groups.items()
            },
            "ignore": list(config.ignore or []),
        }

    output = _prepare_output(output_dir, model, overwrite=overwrite, dry_run=dry_run)
    result = {
        "model": model,
        "output_dir": str(output),
        "scheme": scheme_name,
        "group_size": group_size,
        "scheme_details": scheme_payload,
        "ignore": ignore or ["lm_head"],
        "max_workers": max_workers,
        "devices": devices,
        "dry_run": dry_run,
    }
    if dry_run:
        return result

    from llmc_cli.output import imported_quietly

    with imported_quietly():
        from llmcompressor import model_free_ptq

    started = time.monotonic()
    try:
        with _guard_stdout(json_mode), _capture_python_warnings() as caught:
            model_free_ptq(
                model_stub=model,
                save_directory=str(output),
                scheme=scheme_value,
                config=config,
                ignore=ignore or ["lm_head"],
                max_workers=max_workers,
                device=devices[0] if devices and len(devices) == 1 else devices,
            )
    except Exception as exc:
        if _is_oom(exc):
            raise RuntimeFailure(
                f"Out of memory in model-free PTQ: {exc}",
                hint="Lower --max-workers or use a smaller --device list.",
            ) from exc
        raise
    result["elapsed_s"] = round(time.monotonic() - started, 3)
    result["warnings"] = [str(w.message) for w in caught]
    result["output"] = _artifact_summary(output)
    return result


def run_expert_prune(
    *,
    model: str,
    output_dir: str,
    sparsity: float,
    metric: str,
    uniform: bool,
    router_pattern: str,
    expert_pattern: str,
    aux_pattern: str | None,
    index_pattern: str | None,
    num_experts_config_key: str | None,
    saliency_report_path: str | None,
    scheme: str | None,
    group_size: int | None,
    ignore: list[str] | None,
    max_workers: int | str,
    devices: list[str] | None,
    overwrite: bool,
    dry_run: bool,
    json_mode: bool,
) -> dict[str, Any]:
    if not 0.0 <= sparsity <= 1.0:
        raise InputError(f"--sparsity must be in [0, 1], got {sparsity}")
    if metric != "magnitude" and not saliency_report_path:
        raise InputError(
            f"--metric {metric} requires --saliency-report from a REAP run",
            hint="Run 'llmc prune --method reap --report-path reap.json', then pass it here.",
        )
    if saliency_report_path and not Path(saliency_report_path).expanduser().exists():
        raise InputError(f"Saliency report does not exist: {saliency_report_path}")

    scheme_name = scheme.upper() if scheme else None
    scheme_value: Any = scheme_name
    if scheme_name:
        info = scheme_details(scheme_name)
        if not info["data_free_eligible"]:
            raise InputError(
                f"Scheme {scheme_name} needs calibration; expert-prune can only chain data-free PTQ",
                hint="Prune first, then run 'llmc quantize' on the pruned checkpoint.",
            )
        if group_size is not None:
            from compressed_tensors.quantization import preset_name_to_scheme

            if group_size < 1:
                raise InputError("--group-size must be >= 1")
            scheme_obj = preset_name_to_scheme(scheme_name, ["Linear"])
            if scheme_obj.weights is not None:
                scheme_obj.weights.group_size = group_size
            if scheme_obj.input_activations is not None and scheme_obj.input_activations.strategy in {
                "group",
                "tensor_group",
            }:
                scheme_obj.input_activations.group_size = group_size
            scheme_value = scheme_obj
    elif group_size is not None:
        raise InputError("--group-size requires --scheme")

    output = _prepare_output(output_dir, model, overwrite=overwrite, dry_run=dry_run)
    result = {
        "model": model,
        "output_dir": str(output),
        "sparsity": sparsity,
        "metric": metric,
        "uniform": uniform,
        "saliency_report": saliency_report_path,
        "scheme": scheme_name,
        "group_size": group_size,
        "dry_run": dry_run,
    }
    if dry_run:
        return result

    from llmc_cli.output import imported_quietly

    with imported_quietly():
        from llmcompressor.entrypoints.converter import ExpertPruner

    started = time.monotonic()
    pruner = ExpertPruner.from_pretrained(
        model,
        sparsity=sparsity,
        metric=metric,
        uniform=uniform,
        router_pattern=router_pattern,
        expert_pattern=expert_pattern,
        aux_pattern=aux_pattern,
        index_pattern=index_pattern,
        num_experts_config_key=num_experts_config_key,
        saliency_report_path=saliency_report_path,
    )
    try:
        with _guard_stdout(json_mode), _capture_python_warnings() as caught:
            if scheme_name:
                from llmcompressor import model_free_ptq

                model_free_ptq(
                    model_stub=model,
                    save_directory=str(output),
                    scheme=scheme_value,
                    ignore=ignore or ["lm_head"],
                    max_workers=max_workers,
                    device=devices[0] if devices and len(devices) == 1 else devices,
                    converter=pruner,
                )
            else:
                from compressed_tensors.entrypoints.convert import convert_checkpoint

                convert_checkpoint(
                    model_stub=model,
                    save_directory=str(output),
                    converter=pruner,
                    max_workers=max_workers,
                    device=devices[0] if devices and len(devices) == 1 else devices,
                )
    except Exception as exc:
        if _is_oom(exc):
            raise RuntimeFailure(
                f"Out of memory during expert pruning: {exc}",
                hint="Lower --max-workers.",
            ) from exc
        raise
    result["elapsed_s"] = round(time.monotonic() - started, 3)
    result["run_warnings"] = [str(w.message) for w in caught]
    result["output"] = _artifact_summary(output)
    return result
