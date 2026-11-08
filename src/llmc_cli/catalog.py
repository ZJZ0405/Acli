"""Static metadata and runtime introspection for schemes and modifiers."""

from __future__ import annotations

from typing import Any

from llmc_cli.errors import InputError

# Compute-capability hints are advisory; llm-compressor itself does not enforce
# them at recipe time, but the generated checkpoint may not run on old GPUs.
SCHEME_NOTES: dict[str, dict[str, Any]] = {
    "FP8": {
        "notes": "Static per-tensor FP8 weights and activations; needs calibration.",
        "min_compute_capability": (8, 9),
    },
    "FP8_BLOCK": {
        "notes": "Block-wise FP8 weights, dynamic per-group activations; needs calibration for weights only.",
        "min_compute_capability": (8, 9),
    },
    "FP8_DYNAMIC": {
        "notes": "Per-channel FP8 weights with dynamic per-token activations; data-free.",
        "min_compute_capability": (8, 9),
    },
    "INT8": {
        "notes": "Per-channel INT8 weights with dynamic per-token INT8 activations.",
        "min_compute_capability": (7, 0),
    },
    "MXFP4": {
        "notes": "Microscaling FP4 weights and activations (E2M1, group 32). Ideal on Blackwell.",
        "min_compute_capability": (8, 9),
    },
    "MXFP4A16": {
        "notes": "Microscaling FP4 weight-only with 16-bit activations.",
        "min_compute_capability": (8, 9),
    },
    "MXFP4XFP8": {
        "notes": "Microscaling FP4 weights with microscaling FP8 activations.",
        "min_compute_capability": (8, 9),
    },
    "MXFP6A16": {
        "notes": "Microscaling FP6 weight-only with 16-bit activations.",
        "min_compute_capability": (8, 9),
    },
    "MXFP6A8": {
        "notes": "Microscaling FP6 weights with microscaling FP8 activations.",
        "min_compute_capability": (8, 9),
    },
    "MXFP8": {
        "notes": "Microscaling FP8 weights and activations (group 32).",
        "min_compute_capability": (8, 9),
    },
    "MXFP8A16": {
        "notes": "Microscaling FP8 weight-only with 16-bit activations.",
        "min_compute_capability": (8, 9),
    },
    "NVFP4": {
        "notes": "NVIDIA FP4 weights and activations (group 16); Blackwell-native.",
        "min_compute_capability": (10, 0),
    },
    "NVFP4A16": {
        "notes": "NVIDIA FP4 weight-only (group 16) with 16-bit activations; Blackwell-native.",
        "min_compute_capability": (10, 0),
    },
    "UNQUANTIZED": {"notes": "No weight or activation quantization.", "min_compute_capability": (0, 0)},
}

DEFAULT_SCHEME_BY_METHOD = {
    "gptq": "W4A16",
    "rtn": "FP8_DYNAMIC",
    "awq": "W4A16_ASYM",
    "smoothquant": "W8A8",
    "autoround": "W4A16",
    "quip": "W4A16",
    "spinquant": "W4A16",
    "log-equalization": "W8A8",
    "qad": "W4A16",
}

# Methods that only make quantization easier and must be paired with a
# quantizer modifier.
TRANSFORM_METHODS = {"awq", "smoothquant", "quip", "spinquant", "log-equalization"}
QUANTIZER_METHODS = {"gptq", "rtn", "autoround"}
REFINER_METHODS = {"qad"}

MODIFIER_SPECS: dict[str, dict[str, Any]] = {
    "GPTQModifier": {
        "module": "llmcompressor.modifiers.gptq",
        "category": "quantization",
        "calibration": True,
        "summary": "GPTQ post-training quantization with Hessian-based error compensation.",
    },
    "QuantizationModifier": {
        "module": "llmcompressor.modifiers.quantization",
        "category": "quantization",
        "calibration": False,
        "summary": "Round-to-nearest quantization; data-free for weight-only and dynamic schemes.",
    },
    "AutoRoundModifier": {
        "module": "llmcompressor.modifiers.autoround",
        "category": "quantization",
        "calibration": True,
        "summary": "AutoRound signed-gradient weight rounding search.",
    },
    "AWQModifier": {
        "module": "llmcompressor.modifiers.transform",
        "category": "transform",
        "calibration": True,
        "summary": "Activation-aware weight scaling; combine with a quantizer.",
    },
    "SmoothQuantModifier": {
        "module": "llmcompressor.modifiers.transform",
        "category": "transform",
        "calibration": True,
        "summary": "Smooth activation outliers into weights; combine with a quantizer.",
    },
    "QuIPModifier": {
        "module": "llmcompressor.modifiers.transform",
        "category": "transform",
        "calibration": False,
        "summary": "Hadamard / random rotation transforms for quantization-friendly weights.",
    },
    "SpinQuantModifier": {
        "module": "llmcompressor.modifiers.transform",
        "category": "transform",
        "calibration": False,
        "summary": "Learned-rotation-style R1/R2/R4 transforms for quantization.",
    },
    "LogarithmicEqualizationModifier": {
        "module": "llmcompressor.modifiers.logarithmic_equalization",
        "category": "transform",
        "calibration": True,
        "summary": "Logarithmic equalization variant of SmoothQuant.",
    },
    "QADModifier": {
        "module": "llmcompressor.modifiers.qad",
        "category": "refinement",
        "calibration": True,
        "summary": "Quantization-aware distillation refinement applied after a quantizer.",
    },
    "REAPPruningModifier": {
        "module": "llmcompressor.modifiers.pruning",
        "category": "pruning",
        "calibration": True,
        "summary": "REAP router-weighted expert pruning for MoE models.",
    },
    "MoNePruningModifier": {
        "module": "llmcompressor.modifiers.pruning",
        "category": "pruning",
        "calibration": True,
        "summary": "MoNe expert collapsing for MoE models.",
    },
}

INTERNAL_MODIFIER_FIELDS = {
    "requires_calibration_data",
    "index",
    "group",
    "start",
    "end",
    "update",
    "initialized_",
    "finalized_",
    "started_",
    "ended_",
    "resolved_mappings_",
    "scales_",
}


def _import_preset_module():
    from compressed_tensors.quantization import quant_scheme

    return quant_scheme


def scheme_names() -> list[str]:
    return sorted(_import_preset_module().PRESET_SCHEMES)


def scheme_details(name: str, targets: list[str] | None = None) -> dict[str, Any]:
    qs = _import_preset_module()
    key = name.upper()
    if key not in qs.PRESET_SCHEMES:
        raise InputError(
            f"Unknown quantization scheme {name!r}",
            hint=f"Available schemes: {', '.join(scheme_names())}",
        )
    scheme = qs.preset_name_to_scheme(key, targets or ["Linear"])
    weights = scheme.weights
    inputs = scheme.input_activations
    requires_calibration = bool(
        inputs is not None and inputs.dynamic is not True
    ) or bool(scheme.output_activations is not None and inputs is None)
    note = SCHEME_NOTES.get(key, {})
    return {
        "name": key,
        "targets": scheme.targets,
        "weights": _quant_args_dict(weights),
        "input_activations": _quant_args_dict(inputs),
        "output_activations": _quant_args_dict(scheme.output_activations),
        "format": scheme.format.value if hasattr(scheme.format, "value") else scheme.format,
        "requires_calibration": requires_calibration,
        "data_free_eligible": not requires_calibration,
        "notes": note.get("notes", ""),
        "min_compute_capability": list(note.get("min_compute_capability", (0, 0))),
    }


def _quant_args_dict(args) -> dict[str, Any] | None:
    if args is None:
        return None
    payload = args.model_dump(mode="json")
    for key in ("scale_dtype", "zp_dtype"):
        if payload.get(key) is not None:
            payload[key] = str(payload[key])
    return payload


def list_schemes() -> list[dict[str, Any]]:
    return [scheme_details(name) for name in scheme_names()]


def get_modifier_class(name: str):
    spec = MODIFIER_SPECS.get(name)
    if spec is None:
        # Accept the import path form used in recipes.
        raise InputError(
            f"Unknown modifier {name!r}",
            hint=f"Known modifiers: {', '.join(sorted(MODIFIER_SPECS))}",
        )
    import importlib

    from llmc_cli.output import imported_quietly

    with imported_quietly():
        module = importlib.import_module(spec["module"])
    try:
        return getattr(module, name)
    except AttributeError as exc:
        raise InputError(f"Modifier {name} is not exported by {spec['module']}") from exc


def modifier_details(name: str, *, include_internal: bool = False) -> dict[str, Any]:
    spec = MODIFIER_SPECS[name]
    cls = get_modifier_class(name)
    fields = []
    for field_name, field in cls.model_fields.items():
        is_internal = field_name in INTERNAL_MODIFIER_FIELDS
        if is_internal and not include_internal:
            continue
        default: Any = None
        required = field.is_required()
        if field.default_factory is not None:
            try:
                default = field.default_factory()
            except Exception:
                default = "<factory>"
        elif not required:
            default = field.default
        fields.append(
            {
                "name": field_name,
                "type": _type_name(field.annotation),
                "required": required,
                "default": _safe_default(default),
                "internal": is_internal,
            }
        )
    doc = (cls.__doc__ or "").strip().splitlines()
    return {
        "name": name,
        "module": spec["module"],
        "category": spec["category"],
        "requires_calibration": spec["calibration"],
        "summary": spec["summary"],
        "description": doc[0] if doc else spec["summary"],
        "fields": fields,
    }


def list_modifiers(*, include_internal: bool = False) -> list[dict[str, Any]]:
    return [
        modifier_details(name, include_internal=include_internal)
        for name in sorted(MODIFIER_SPECS)
    ]


def _type_name(annotation: Any) -> str:
    if annotation is None:
        return "Any"
    name = getattr(annotation, "__name__", None)
    if name:
        return name
    text = str(annotation)
    return text.replace("typing.", "").replace("llmcompressor.", "")


def _safe_default(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (list, tuple, dict)):
        return value
    return str(value)


RECIPE_PRESETS: dict[str, dict[str, Any]] = {
    "w4a16-gptq": {
        "description": "4-bit weight-only GPTQ, group size 128 (the standard vLLM W4A16 recipe).",
        "stage": "quant",
        "requires_calibration": True,
        "modifiers": [
            {"type": "GPTQModifier", "kwargs": {"targets": "Linear", "scheme": "W4A16", "ignore": ["lm_head"]}},
        ],
    },
    "w4a16-awq": {
        "description": "AWQ activation-aware scaling followed by asymmetric 4-bit weight quantization.",
        "stage": "quant",
        "requires_calibration": True,
        "modifiers": [
            {"type": "AWQModifier", "kwargs": {"duo_scaling": "both"}},
            {"type": "QuantizationModifier", "kwargs": {"targets": "Linear", "scheme": "W4A16_ASYM", "ignore": ["lm_head"]}},
        ],
    },
    "w4a16-autoround": {
        "description": "AutoRound signed-gradient rounding for 4-bit weights.",
        "stage": "quant",
        "requires_calibration": True,
        "modifiers": [
            {"type": "AutoRoundModifier", "kwargs": {"targets": "Linear", "scheme": "W4A16", "ignore": ["lm_head"]}},
        ],
    },
    "w8a8-int8-gptq": {
        "description": "INT8 weight and activation quantization with GPTQ calibration.",
        "stage": "quant",
        "requires_calibration": True,
        "modifiers": [
            {"type": "GPTQModifier", "kwargs": {"targets": "Linear", "scheme": "W8A8", "ignore": ["lm_head"]}},
        ],
    },
    "w8a8-int8-smoothquant": {
        "description": "SmoothQuant scaling followed by INT8 weight/activation RTN quantization.",
        "stage": "quant",
        "requires_calibration": True,
        "modifiers": [
            {"type": "SmoothQuantModifier", "kwargs": {"smoothing_strength": 0.8}},
            {"type": "QuantizationModifier", "kwargs": {"targets": "Linear", "scheme": "W8A8", "ignore": ["lm_head"]}},
        ],
    },
    "fp8-dynamic": {
        "description": "FP8 per-channel weights with dynamic per-token activations; no calibration data.",
        "stage": "quant",
        "requires_calibration": False,
        "modifiers": [
            {"type": "QuantizationModifier", "kwargs": {"targets": "Linear", "scheme": "FP8_DYNAMIC", "ignore": ["lm_head"]}},
        ],
    },
    "fp8-block": {
        "description": "Block-wise FP8 weights with dynamic FP8 activations.",
        "stage": "quant",
        "requires_calibration": True,
        "modifiers": [
            {"type": "QuantizationModifier", "kwargs": {"targets": "Linear", "scheme": "FP8_BLOCK", "ignore": ["lm_head"]}},
        ],
    },
    "nvfp4": {
        "description": "NVIDIA FP4 weights and activations for Blackwell GPUs.",
        "stage": "quant",
        "requires_calibration": True,
        "modifiers": [
            {"type": "QuantizationModifier", "kwargs": {"targets": "Linear", "scheme": "NVFP4", "ignore": ["lm_head"]}},
        ],
    },
    "nvfp4a16": {
        "description": "NVIDIA FP4 weight-only quantization for Blackwell GPUs.",
        "stage": "quant",
        "requires_calibration": False,
        "modifiers": [
            {"type": "QuantizationModifier", "kwargs": {"targets": "Linear", "scheme": "NVFP4A16", "ignore": ["lm_head"]}},
        ],
    },
    "mxfp4": {
        "description": "Microscaling FP4 weights and activations.",
        "stage": "quant",
        "requires_calibration": False,
        "modifiers": [
            {"type": "QuantizationModifier", "kwargs": {"targets": "Linear", "scheme": "MXFP4", "ignore": ["lm_head"]}},
        ],
    },
    "mxfp4a16": {
        "description": "Microscaling FP4 weight-only quantization.",
        "stage": "quant",
        "requires_calibration": False,
        "modifiers": [
            {"type": "QuantizationModifier", "kwargs": {"targets": "Linear", "scheme": "MXFP4A16", "ignore": ["lm_head"]}},
        ],
    },
    "quip-w4a16": {
        "description": "QuIP Hadamard transforms plus 4-bit weight-only RTN.",
        "stage": "quant",
        "requires_calibration": False,
        "modifiers": [
            {"type": "QuIPModifier", "kwargs": {"rotations": ["v"], "transform_type": "hadamard", "transform_block_size": 128}},
            {"type": "QuantizationModifier", "kwargs": {"targets": "Linear", "scheme": "W4A16", "ignore": ["lm_head"]}},
        ],
    },
    "spinquant-w4a16": {
        "description": "SpinQuant R1/R2/R4 rotations plus 4-bit weight-only RTN.",
        "stage": "quant",
        "requires_calibration": False,
        "modifiers": [
            {"type": "SpinQuantModifier", "kwargs": {"rotations": ["R1", "R2", "R4"], "transform_type": "hadamard", "transform_block_size": 128}},
            {"type": "QuantizationModifier", "kwargs": {"targets": "Linear", "scheme": "W4A16", "ignore": ["lm_head"]}},
        ],
    },
    "reap-25": {
        "description": "REAP expert pruning with 25% sparsity (MoE models, needs calibration).",
        "stage": "prune",
        "requires_calibration": True,
        "modifiers": [
            {"type": "REAPPruningModifier", "kwargs": {"sparsity": 0.25}},
        ],
    },
    "mone-25": {
        "description": "MoNe expert collapsing with 25% sparsity (MoE models, needs calibration).",
        "stage": "prune",
        "requires_calibration": True,
        "modifiers": [
            {"type": "MoNePruningModifier", "kwargs": {"sparsity": 0.25}},
        ],
    },
}


def preset_details(name: str) -> dict[str, Any]:
    if name not in RECIPE_PRESETS:
        raise InputError(
            f"Unknown recipe preset {name!r}",
            hint=f"Available presets: {', '.join(sorted(RECIPE_PRESETS))}",
        )
    return {"name": name, **RECIPE_PRESETS[name]}


def list_presets() -> list[dict[str, Any]]:
    return [preset_details(name) for name in sorted(RECIPE_PRESETS)]
