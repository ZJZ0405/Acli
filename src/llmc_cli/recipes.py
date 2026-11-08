"""Recipe construction, loading, validation, and YAML serialization."""

from __future__ import annotations

import contextlib
import copy
import sys
from pathlib import Path
from typing import Any, Iterable

from llmc_cli.catalog import (
    DEFAULT_SCHEME_BY_METHOD,
    QUANTIZER_METHODS,
    RECIPE_PRESETS,
    REFINER_METHODS,
    TRANSFORM_METHODS,
    get_modifier_class,
    scheme_details,
)
from llmc_cli.errors import InputError


def _clean(kwargs: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in kwargs.items() if value is not None}


def build_modifier(name: str, kwargs: dict[str, Any]):
    cls = get_modifier_class(name)
    cleaned = _clean(kwargs)
    # AWQ's default offload_device is a Sentinel that PyYAML cannot round-trip.
    if name == "AWQModifier" and "offload_device" not in cleaned:
        cleaned["offload_device"] = None
    try:
        return cls(**cleaned)
    except Exception as exc:
        raise InputError(
            f"Invalid {name} arguments: {exc}",
            hint=f"Run 'llmc modifiers --name {name}' to list supported fields.",
        ) from exc


def recipe_from_specs(specs: list[dict[str, Any]], stage: str = "quant") -> tuple[Any, list[dict[str, Any]]]:
    from llmc_cli.output import imported_quietly

    with imported_quietly():
        from llmcompressor.recipe import Recipe

    if not specs:
        raise InputError("Recipe contains no modifiers")
    modifiers = [build_modifier(spec["type"], spec.get("kwargs", {})) for spec in specs]
    with contextlib.redirect_stdout(sys.stderr):
        recipe = Recipe.from_modifiers(modifiers, modifier_group_name=stage)
    return recipe, specs


def recipe_to_yaml(recipe: Any) -> str:
    return recipe.yaml()


def recipe_to_dict(recipe: Any) -> dict[str, Any]:
    return recipe.to_dict()


def _normalize_specs(raw_specs: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    specs = []
    for spec in raw_specs:
        specs.append({"type": spec["type"], "kwargs": copy.deepcopy(spec.get("kwargs", {}))})
    return specs


def specs_from_preset(name: str) -> list[dict[str, Any]]:
    if name not in RECIPE_PRESETS:
        raise InputError(
            f"Unknown recipe preset {name!r}",
            hint=f"Available presets: {', '.join(sorted(RECIPE_PRESETS))}",
        )
    return _normalize_specs(RECIPE_PRESETS[name]["modifiers"])


def recipe_from_preset(name: str) -> tuple[Any, list[dict[str, Any]], str]:
    preset = RECIPE_PRESETS[name]
    specs = specs_from_preset(name)
    recipe, specs = recipe_from_specs(specs, stage=preset.get("stage", "quant"))
    return recipe, specs, preset.get("stage", "quant")


def build_kv_cache_scheme(value: str | None) -> Any:
    if not value or value == "none":
        return None
    from compressed_tensors.quantization import QuantizationArgs

    normalized = value.strip().lower()
    if normalized == "fp8":
        return QuantizationArgs(
            num_bits=8,
            type="float",
            strategy="tensor",
            symmetric=True,
            dynamic=False,
        )
    if normalized == "int8":
        return QuantizationArgs(
            num_bits=8,
            type="int",
            strategy="tensor",
            symmetric=True,
            dynamic=False,
        )
    raise InputError(
        f"Unknown KV-cache quantization {value!r}",
        hint="Use one of: fp8, int8, none, or pass raw JSON via --kv-cache-json.",
    )


def build_quantize_specs(
    methods: list[str] | None,
    scheme: str | None = None,
    *,
    targets: list[str] | None = None,
    ignore: list[str] | None = None,
    kv_cache: str | None = None,
    kv_cache_json: str | None = None,
    group_size: int | None = None,
    options: dict[str, dict[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], str, bool]:
    """Build modifier specs for a quantization run.

    Returns (specs, resolved_scheme, requires_calibration).
    """
    from compressed_tensors.quantization import QuantizationArgs

    options = options or {}
    methods = [m.strip().lower() for m in (methods or []) if m.strip()]
    known = QUANTIZER_METHODS | TRANSFORM_METHODS | REFINER_METHODS
    unknown = [m for m in methods if m not in known]
    if unknown:
        raise InputError(
            f"Unknown quantization method(s): {', '.join(unknown)}",
            hint=f"Known methods: {', '.join(sorted(known))}",
        )

    quantizers = [m for m in methods if m in QUANTIZER_METHODS]
    if len(quantizers) > 1:
        raise InputError(
            f"Only one quantizer method may be selected, got: {', '.join(quantizers)}",
            hint="Pick one of gptq, rtn, autoround; transforms can be combined with it.",
        )
    transforms = [m for m in methods if m in TRANSFORM_METHODS]
    refiners = [m for m in methods if m in REFINER_METHODS]
    quantizer = quantizers[0] if quantizers else ("rtn" if (transforms or refiners) else None)
    if quantizer is None:
        raise InputError(
            "No quantization method selected",
            hint="Pass --method gptq|rtn|autoround, optionally with a transform such as --method awq.",
        )

    resolved_scheme = (scheme or DEFAULT_SCHEME_BY_METHOD[quantizer]).upper()
    scheme_info = scheme_details(resolved_scheme, targets or ["Linear"])
    if scheme_info["weights"] is None:
        raise InputError(f"Scheme {resolved_scheme} has no weight quantization")

    targets = targets or ["Linear"]
    ignore = ignore if ignore is not None else ["lm_head"]

    scheme_value: Any = resolved_scheme
    config_groups = None
    if group_size is not None:
        from compressed_tensors.quantization import preset_name_to_scheme

        if group_size < 1:
            raise InputError("--group-size must be >= 1")
        scheme_obj = preset_name_to_scheme(resolved_scheme, targets)
        if scheme_obj.weights is not None:
            scheme_obj.weights.group_size = group_size
        if scheme_obj.input_activations is not None and scheme_obj.input_activations.strategy in {
            "group",
            "tensor_group",
        }:
            scheme_obj.input_activations.group_size = group_size
        scheme_value = None
        config_groups = {"group_0": scheme_obj}

    if kv_cache_json:
        import json

        try:
            kv_payload = json.loads(kv_cache_json)
        except json.JSONDecodeError as exc:
            raise InputError(f"Invalid --kv-cache-json: {exc}") from exc
        kv_cache_scheme = QuantizationArgs.model_validate(kv_payload)
    else:
        kv_cache_scheme = build_kv_cache_scheme(kv_cache)

    common = {
        "targets": targets,
        "scheme": scheme_value,
        "config_groups": config_groups,
        "ignore": ignore,
        "kv_cache_scheme": kv_cache_scheme,
    }
    specs: list[dict[str, Any]] = []

    for method in transforms:
        if method == "awq":
            kwargs = {
                "duo_scaling": options.get("awq", {}).get("duo_scaling"),
                "n_grid": options.get("awq", {}).get("n_grid"),
                "offload_device": options.get("awq", {}).get("offload_device"),
            }
            specs.append({"type": "AWQModifier", "kwargs": _clean(kwargs)})
        elif method in {"smoothquant", "log-equalization"}:
            type_name = (
                "SmoothQuantModifier"
                if method == "smoothquant"
                else "LogarithmicEqualizationModifier"
            )
            source = options.get(method) or (
                options.get("smoothquant", {}) if method == "log-equalization" else {}
            )
            kwargs = {
                "smoothing_strength": source.get("smoothing_strength"),
                "ignore": ignore,
                "num_calibration_steps": source.get("num_calibration_steps"),
                "algorithm": source.get("algorithm"),
            }
            specs.append({"type": type_name, "kwargs": _clean(kwargs)})
        elif method == "quip":
            kwargs = {
                "rotations": options.get("quip", {}).get("rotations"),
                "transform_type": options.get("quip", {}).get("transform_type"),
                "transform_block_size": options.get("quip", {}).get("transform_block_size"),
                "randomize": options.get("quip", {}).get("randomize"),
                "learnable": options.get("quip", {}).get("learnable"),
                "targets": targets,
                "ignore": ignore,
            }
            specs.append({"type": "QuIPModifier", "kwargs": _clean(kwargs)})
        elif method == "spinquant":
            kwargs = {
                "rotations": options.get("spinquant", {}).get("rotations"),
                "transform_type": options.get("spinquant", {}).get("transform_type"),
                "transform_block_size": options.get("spinquant", {}).get("transform_block_size"),
                "randomize": options.get("spinquant", {}).get("randomize"),
                "learnable": options.get("spinquant", {}).get("learnable"),
            }
            specs.append({"type": "SpinQuantModifier", "kwargs": _clean(kwargs)})

    quant_kwargs = dict(common)
    quant_kwargs.update(
        {
            "bypass_divisibility_checks": options.get("quant", {}).get("bypass_divisibility_checks"),
            "weight_observer": options.get("quant", {}).get("weight_observer"),
            "input_observer": options.get("quant", {}).get("input_observer"),
            "output_observer": options.get("quant", {}).get("output_observer"),
            "observer": options.get("quant", {}).get("observer"),
        }
    )
    if quantizer == "gptq":
        quant_kwargs.update(
            {
                "block_size": options.get("gptq", {}).get("block_size"),
                "dampening_frac": options.get("gptq", {}).get("dampening_frac"),
                "actorder": options.get("gptq", {}).get("actorder"),
                "batched_quantization": options.get("gptq", {}).get("batched_quantization"),
            }
        )
        specs.append({"type": "GPTQModifier", "kwargs": _clean(quant_kwargs)})
    elif quantizer == "autoround":
        quant_kwargs.update(
            {
                "iters": options.get("autoround", {}).get("iters"),
                "enable_torch_compile": options.get("autoround", {}).get("enable_torch_compile"),
                "batch_size": options.get("autoround", {}).get("batch_size"),
                "lr": options.get("autoround", {}).get("lr"),
                "device_ids": options.get("autoround", {}).get("device_ids"),
                "disable_opt_rtn": options.get("autoround", {}).get("disable_opt_rtn"),
            }
        )
        specs.append({"type": "AutoRoundModifier", "kwargs": _clean(quant_kwargs)})
    else:
        specs.append({"type": "QuantizationModifier", "kwargs": _clean(quant_kwargs)})

    if refiners:
        qad_kwargs = {
            "num_epochs": options.get("qad", {}).get("num_epochs"),
            "lr": options.get("qad", {}).get("lr"),
            "weight_decay": options.get("qad", {}).get("weight_decay"),
            "gradient_accumulation_steps": options.get("qad", {}).get("gradient_accumulation_steps"),
            "max_grad_norm": options.get("qad", {}).get("max_grad_norm"),
            "seed": options.get("qad", {}).get("seed"),
            "offload_device": options.get("qad", {}).get("offload_device"),
            "validation_fraction": options.get("qad", {}).get("validation_fraction"),
            "reobserve_weights": options.get("qad", {}).get("reobserve_weights"),
        }
        specs.append({"type": "QADModifier", "kwargs": _clean(qad_kwargs)})

    requires_calibration = _requires_calibration(methods, quantizer, scheme_info)
    if kv_cache_scheme is not None:
        requires_calibration = True
    return specs, resolved_scheme, requires_calibration


def _requires_calibration(methods: list[str], quantizer: str, scheme_info: dict[str, Any]) -> bool:
    if quantizer in {"gptq", "autoround"}:
        return True
    if any(method in {"awq", "smoothquant", "log-equalization", "qad"} for method in methods):
        return True
    return bool(scheme_info.get("requires_calibration"))


def build_prune_specs(
    method: str,
    sparsity: float,
    *,
    ignore: list[str] | None = None,
    report_path: str | None = None,
    quant_method: str | None = None,
    scheme: str | None = None,
    targets: list[str] | None = None,
    group_size: int | None = None,
) -> list[dict[str, Any]]:
    method = method.lower()
    if method not in {"reap", "mone"}:
        raise InputError(
            f"Unknown pruning method {method!r}",
            hint="Use 'reap' or 'mone'.",
        )
    if not 0.0 <= sparsity < 1.0:
        raise InputError(f"--sparsity must be in [0, 1), got {sparsity}")
    type_name = "REAPPruningModifier" if method == "reap" else "MoNePruningModifier"
    prune_kwargs = _clean(
        {
            "sparsity": sparsity,
            "ignore": ignore,
            "report_path": report_path,
        }
    )
    specs = [{"type": type_name, "kwargs": prune_kwargs}]
    if quant_method or scheme:
        quant_specs, _resolved, _needs = build_quantize_specs(
            [quant_method or "rtn"],
            scheme,
            targets=targets,
            ignore=["lm_head"],
            group_size=group_size,
        )
        specs.extend(quant_specs)
    return specs


def load_recipe(source: str, stage: str | None = None):
    """Load a recipe from a path; '-' reads YAML/JSON from stdin."""
    from llmc_cli.output import imported_quietly

    with imported_quietly():
        from llmcompressor.recipe import Recipe

    if source == "-":
        content = sys.stdin.read()
        if not content.strip():
            raise InputError("No recipe content received on stdin")
        try:
            with contextlib.redirect_stdout(sys.stderr):
                return Recipe.create_instance(content, target_stage=stage)
        except Exception as exc:
            raise InputError(f"Invalid recipe on stdin: {exc}") from exc

    path = Path(source).expanduser()
    if not path.exists():
        raise InputError(
            f"Recipe file does not exist: {source}",
            hint="Pass a .yaml/.yml/.json/.md recipe, or '-' to read from stdin.",
        )
    try:
        with contextlib.redirect_stdout(sys.stderr):
            return Recipe.create_instance(str(path), target_stage=stage)
    except Exception as exc:
        raise InputError(f"Invalid recipe {source}: {exc}") from exc


def recipe_summary(recipe: Any) -> dict[str, Any]:
    modifiers = []
    for modifier in recipe.modifiers:
        modifiers.append(
            {
                "type": type(modifier).__name__,
                "group": getattr(modifier, "group", None),
                "requires_calibration": bool(getattr(modifier, "requires_calibration_data", False)),
            }
        )
    return {
        "stage": recipe.stage,
        "modifiers": modifiers,
        "requires_calibration": any(m["requires_calibration"] for m in modifiers),
        "modifier_count": len(modifiers),
    }
