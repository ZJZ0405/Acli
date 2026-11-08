"""Environment, model, dataset, and artifact inspection."""

from __future__ import annotations

import json
import os
import platform
import shutil
import struct
import sys
from importlib import metadata as importlib_metadata
from pathlib import Path
from typing import Any

from llmc_cli import __version__
from llmc_cli.errors import EnvironmentError_, InputError
from llmc_cli.utils import human_bytes, read_json

PACKAGE_NAMES = [
    "llmc-cli",
    "llmcompressor",
    "compressed-tensors",
    "transformers",
    "torch",
    "datasets",
    "accelerate",
    "auto-round",
    "safetensors",
    "huggingface-hub",
    "vllm",
]


def _package_version(name: str) -> str | None:
    try:
        return importlib_metadata.version(name)
    except importlib_metadata.PackageNotFoundError:
        return None


def _disk(path: str | Path) -> dict[str, Any]:
    try:
        usage = shutil.disk_usage(path)
        return {"path": str(path), "total_bytes": usage.total, "free_bytes": usage.free}
    except OSError as exc:
        return {"path": str(path), "error": str(exc)}


def doctor() -> dict[str, Any]:
    import torch

    packages = {name: _package_version(name) for name in PACKAGE_NAMES}
    packages["llmc-cli"] = packages.get("llmc-cli") or __version__

    gpu: dict[str, Any] = {
        "torch_cuda_version": torch.version.cuda,
        "cudnn_version": torch.backends.cudnn.version() if torch.backends.cudnn.is_available() else None,
        "available": torch.cuda.is_available(),
        "device_count": torch.cuda.device_count() if torch.cuda.is_available() else 0,
        "devices": [],
    }
    if torch.cuda.is_available():
        for index in range(torch.cuda.device_count()):
            props = torch.cuda.get_device_properties(index)
            gpu["devices"].append(
                {
                    "index": index,
                    "name": props.name,
                    "capability": f"{props.major}.{props.minor}",
                    "major": props.major,
                    "minor": props.minor,
                    "total_memory_bytes": props.total_memory,
                }
            )
        try:
            gpu["bf16_supported"] = torch.cuda.is_bf16_supported()
        except Exception:
            gpu["bf16_supported"] = None

    hf_home = os.environ.get("HF_HOME") or str(Path.home() / ".cache" / "huggingface")
    env = {
        "HF_HOME": os.environ.get("HF_HOME"),
        "HF_HUB_CACHE": os.environ.get("HF_HUB_CACHE"),
        "HF_TOKEN": bool(os.environ.get("HF_TOKEN") or os.environ.get("HUGGING_FACE_HUB_TOKEN")),
        "HF_HUB_OFFLINE": os.environ.get("HF_HUB_OFFLINE"),
        "TRANSFORMERS_OFFLINE": os.environ.get("TRANSFORMERS_OFFLINE"),
        "CUDA_VISIBLE_DEVICES": os.environ.get("CUDA_VISIBLE_DEVICES"),
    }

    issues: list[str] = []
    if not gpu["available"]:
        issues.append("CUDA is not available; only CPU-capable/data-free paths will work.")
    elif gpu["devices"]:
        major, minor = gpu["devices"][0]["major"], gpu["devices"][0]["minor"]
        if (major, minor) < (8, 9):
            issues.append(
                "GPU compute capability is below 8.9; FP8 and FP4 schemes may not be runnable."
            )
    if packages.get("llmcompressor") is None:
        issues.append("llmcompressor is not installed in this interpreter.")
    free = _disk(Path.cwd()).get("free_bytes")
    if free is not None and free < 10 * 1024**3:
        issues.append("Less than 10 GiB free on the current filesystem.")

    return {
        "status": "warning" if issues else "ok",
        "issues": issues,
        "python": {
            "version": sys.version.split()[0],
            "executable": sys.executable,
            "platform": platform.platform(),
        },
        "packages": packages,
        "gpu": gpu,
        "memory": {
            "total_bytes": _total_ram(),
        },
        "disk": {
            "cwd": _disk(Path.cwd()),
            "hf_home": _disk(hf_home),
        },
        "env": env,
        "cwd": str(Path.cwd()),
    }


def _total_ram() -> int | None:
    try:
        with open("/proc/meminfo", "r", encoding="utf-8") as handle:
            for line in handle:
                if line.startswith("MemTotal:"):
                    return int(line.split()[1]) * 1024
    except OSError:
        return None
    return None


def _read_safetensors_header(path: Path) -> dict[str, Any]:
    with open(path, "rb") as handle:
        raw = handle.read(8)
        if len(raw) != 8:
            raise ValueError("truncated header")
        (length,) = struct.unpack("<Q", raw)
        if length <= 0 or length > 100 * 1024 * 1024:
            raise ValueError(f"implausible header length {length}")
        header = json.loads(handle.read(length).decode("utf-8"))
    tensors = {key: value for key, value in header.items() if key != "__metadata__"}
    return {"metadata": header.get("__metadata__", {}), "tensors": tensors}


def _scan_local_weights(path: Path) -> dict[str, Any]:
    files = [p for p in path.rglob("*") if p.is_file()]
    weight_files = [p for p in files if p.suffix == ".safetensors"]
    total_bytes = sum(p.stat().st_size for p in files)
    weight_bytes = sum(p.stat().st_size for p in weight_files)
    params = 0
    by_dtype: dict[str, int] = {}
    tensor_count = 0
    errors: list[str] = []
    for weight_file in weight_files:
        try:
            header = _read_safetensors_header(weight_file)
        except Exception as exc:
            errors.append(f"{weight_file.name}: {exc}")
            continue
        for tensor in header["tensors"].values():
            tensor_count += 1
            shape = tensor.get("shape") or []
            count = 1
            for dim in shape:
                count *= int(dim)
            params += count
            dtype = str(tensor.get("dtype", "unknown"))
            by_dtype[dtype] = by_dtype.get(dtype, 0) + count
    return {
        "path": str(path),
        "files": len(files),
        "file_names": [p.name for p in files],
        "weight_files": [p.name for p in sorted(weight_files)],
        "weight_file_count": len(weight_files),
        "total_bytes": total_bytes,
        "weight_bytes": weight_bytes,
        "parameters": params if weight_files else None,
        "parameters_by_dtype": by_dtype if by_dtype else None,
        "tensor_count": tensor_count if weight_files else None,
        "errors": errors,
    }


def _remote_model_metadata(model: str, revision: str, token: str | None) -> dict[str, Any]:
    from huggingface_hub import HfApi

    try:
        info = HfApi(token=token).model_info(model, revision=revision, files_metadata=True)
    except Exception as exc:
        raise EnvironmentError_(
            f"Could not fetch Hugging Face metadata for {model}: {exc}",
            hint="Check the model id, your network, and HF_TOKEN if the repo is gated.",
        ) from exc
    siblings = list(info.siblings or [])
    safetensors = info.safetensors
    weight_files = [
        s for s in siblings if s.rfilename.endswith(".safetensors") or s.rfilename.endswith(".bin")
    ]
    return {
        "model_id": info.id,
        "revision": info.sha,
        "pipeline_tag": info.pipeline_tag,
        "library_name": info.library_name,
        "gated": bool(info.gated),
        "private": bool(info.private),
        "total_bytes": sum(int(s.size or 0) for s in siblings),
        "weight_bytes": sum(int(s.size or 0) for s in weight_files),
        "files": [s.rfilename for s in siblings],
        "weight_files": [s.rfilename for s in weight_files if s.rfilename.endswith(".safetensors")],
        "parameters": safetensors.total if safetensors else None,
        "parameters_by_dtype": safetensors.parameters if safetensors else None,
    }


def _config_summary(config) -> dict[str, Any]:
    config_dict = config.to_dict()
    keys = [
        "model_type",
        "architectures",
        "hidden_size",
        "intermediate_size",
        "num_hidden_layers",
        "num_attention_heads",
        "num_key_value_heads",
        "vocab_size",
        "max_position_embeddings",
        "tie_word_embeddings",
    ]
    summary = {key: _plain(config_dict.get(key)) for key in keys if config_dict.get(key) is not None}
    dtype = config_dict.get("dtype") or config_dict.get("torch_dtype")
    summary["dtype"] = str(dtype) if dtype is not None else None
    return summary


def _plain(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items()}
    return str(value)


def _quantization_summary(config) -> dict[str, Any] | None:
    config_dict = config.to_dict() if hasattr(config, "to_dict") else config
    if not isinstance(config_dict, dict):
        return None
    quant = config_dict.get("quantization_config")
    if quant is None:
        text_config = config_dict.get("text_config") or {}
        if isinstance(text_config, dict):
            quant = text_config.get("quantization_config")
    if quant is None:
        quant = config_dict.get("compression_config")
    if quant is None:
        return None
    if hasattr(quant, "to_dict"):
        quant = quant.to_dict()
    if not isinstance(quant, dict):
        return {"raw": str(quant)}
    keys = [
        "quant_method",
        "format",
        "quantization_status",
        "config_groups",
        "ignore",
        "kv_cache_scheme",
        "transform_config",
    ]
    return {key: _plain(quant.get(key)) for key in keys if key in quant}


def inspect_model(
    model: str,
    *,
    revision: str = "main",
    trust_remote_code: bool = False,
    local_files_only: bool = False,
    token: str | None = None,
) -> dict[str, Any]:
    from transformers import AutoConfig

    path = Path(model).expanduser()
    is_local = path.exists()
    try:
        config = AutoConfig.from_pretrained(
            str(path) if is_local else model,
            revision=revision,
            trust_remote_code=trust_remote_code,
            local_files_only=local_files_only,
            token=token,
        )
    except Exception as exc:
        raise EnvironmentError_(
            f"Could not load model config for {model}: {exc}",
            hint="For gated repositories set HF_TOKEN. For offline use pass --local-files-only.",
        ) from exc

    file_info = (
        _scan_local_weights(path)
        if is_local
        else _remote_model_metadata(model, revision, token)
    )
    file_list = file_info.get("files", [])
    if is_local:
        names = set(file_info.get("file_names", []))
        file_list = list(file_info.get("weight_files", [])) + [
            p.name for p in path.iterdir() if p.is_file()
        ]
    else:
        names = set(file_list)
    tokenizer_files = [
        name
        for name in (
            "tokenizer.json",
            "tokenizer_config.json",
            "special_tokens_map.json",
            "processor_config.json",
            "preprocessor_config.json",
            "chat_template.jinja",
        )
        if name in names
    ]

    params = file_info.get("parameters")
    dtype = _config_summary(config).get("dtype")
    full_precision_bytes = None
    if params:
        bytes_per_param = {
            "torch.float32": 4,
            "torch.float16": 2,
            "torch.bfloat16": 2,
            "torch.float8_e4m3fn": 1,
            "torch.float8_e5m2": 1,
        }.get(str(dtype), 2)
        full_precision_bytes = params * bytes_per_param

    return {
        "model": model,
        "source": "local" if is_local else "huggingface",
        "revision": file_info.get("revision", revision),
        "config": _config_summary(config),
        "quantization": _quantization_summary(config),
        "tokenizer_files": tokenizer_files,
        "files": {
            "total_bytes": file_info.get("total_bytes"),
            "weight_bytes": file_info.get("weight_bytes"),
            "weight_file_count": len(file_info.get("weight_files", [])),
            "weight_files": file_info.get("weight_files", [])[:200],
            "tensor_count": file_info.get("tensor_count"),
            "errors": file_info.get("errors", []),
        },
        "parameters": params,
        "parameters_by_dtype": file_info.get("parameters_by_dtype"),
        "estimated_full_precision_bytes": full_precision_bytes,
        "huggingface": (
            None
            if is_local
            else {
                "pipeline_tag": file_info.get("pipeline_tag"),
                "library_name": file_info.get("library_name"),
                "gated": file_info.get("gated"),
                "private": file_info.get("private"),
            }
        ),
    }


def inspect_dataset(
    dataset: str,
    *,
    config_name: str | None = None,
    split: str | None = None,
    streaming: bool = False,
    revision: str | None = None,
    sample_rows: int = 3,
    text_column: str | None = None,
) -> dict[str, Any]:
    from datasets import load_dataset, load_dataset_builder

    from llmc_cli.data import lookup_builtin_dataset

    requested_name = dataset
    registry_name = None
    path = Path(dataset).expanduser()
    is_local = path.exists()
    if not is_local:
        builtin = lookup_builtin_dataset(dataset)
        if builtin is not None:
            registry_name, info = builtin
            dataset = info["repo"]
            config_name = config_name or info.get("config")
            if split is None:
                split = info.get("default_split")
    if is_local:
        suffix = path.suffix.lower()
        loader = {
            ".json": "json",
            ".jsonl": "json",
            ".csv": "csv",
            ".parquet": "parquet",
        }.get(suffix)
        if loader is None:
            raise InputError(
                f"Unsupported dataset file type {suffix!r}",
                hint="Supported local formats: .json, .jsonl, .csv, .parquet.",
            )
        loaded = load_dataset(
            loader,
            data_files=str(path),
            split=split or "train",
            streaming=streaming,
        )
        info = None
        builder = None
    else:
        builder = load_dataset_builder(
            dataset,
            name=config_name,
            revision=revision,
        )
        info = builder.info
        target_split = split
        if target_split is None and info.splits:
            target_split = "train" if "train" in info.splits else next(iter(info.splits))
        loaded = load_dataset(
            dataset,
            name=config_name,
            split=target_split or "train",
            streaming=True,
            revision=revision,
        )
        split = target_split
    columns = list(loaded.column_names) if hasattr(loaded, "column_names") else []
    num_rows = None if streaming else getattr(loaded, "num_rows", None)
    samples = []
    if sample_rows > 0:
        try:
            iterator = iter(loaded)
            for _ in range(sample_rows):
                samples.append(_plain(next(iterator)))
        except StopIteration:
            pass
        except Exception as exc:
            samples = [{"error": str(exc)}]

    splits: dict[str, Any] = {}
    if info is not None and info.splits:
        for name, split_info in info.splits.items():
            splits[name] = {
                "num_examples": split_info.num_examples,
                "num_bytes": split_info.num_bytes,
            }
    elif num_rows is not None:
        splits[split or "train"] = {"num_examples": num_rows, "num_bytes": None}

    features = {}
    if info is not None and info.features is not None:
        features = {name: str(dtype) for name, dtype in info.features.items()}
    elif columns:
        features = {name: "<unknown>" for name in columns}

    return {
        "dataset": requested_name,
        "resolved_repo": dataset if not is_local else None,
        "registry_name": registry_name,
        "source": "local" if is_local else "huggingface",
        "config_name": config_name,
        "split": split,
        "streaming": streaming,
        "columns": columns or list(features),
        "features": features,
        "splits": splits,
        "text_column_candidates": [
            name
            for name in (columns or list(features))
            if name in {"text", "content", "prompt", "messages", "conversations"}
        ],
        "requested_text_column": text_column,
        "samples": samples,
    }


def verify_model(
    path: str,
    *,
    strict: bool = False,
    load_check: bool = False,
    token: str | None = None,
) -> dict[str, Any]:
    """Cheap structural verification of a compressed-tensors checkpoint."""
    root = Path(path).expanduser()
    if not root.exists():
        raise InputError(f"Model directory does not exist: {path}")
    if not root.is_dir():
        raise InputError(f"Expected a directory, got file: {path}")

    errors: list[str] = []
    warnings: list[str] = []
    config_path = root / "config.json"
    if not config_path.exists():
        raise InputError(f"No config.json in {path}")

    try:
        config = read_json(config_path)
    except Exception as exc:
        raise InputError(f"Invalid config.json: {exc}") from exc

    quant_config = config.get("quantization_config")
    if quant_config is None:
        try:
            from compressed_tensors.utils.safetensors_load import get_quantization_config

            quant_config = get_quantization_config(str(config_path))
        except Exception:
            pass
    quant_summary = _verify_quant_config(quant_config, errors, warnings)

    weight_files = sorted(root.glob("*.safetensors"))
    if not weight_files:
        errors.append("No *.safetensors weight files found")
    tensor_names: set[str] = set()
    tensor_count = 0
    total_params = 0
    tensor_data_bytes = 0
    dtype_counts: dict[str, int] = {}
    file_keys: dict[str, set[str]] = {}
    total_weight_bytes = 0
    for weight_file in weight_files:
        total_weight_bytes += weight_file.stat().st_size
        try:
            header = _read_safetensors_header(weight_file)
        except Exception as exc:
            errors.append(f"{weight_file.name}: unreadable safetensors header ({exc})")
            continue
        names = set(header["tensors"])
        file_keys[weight_file.name] = names
        tensor_names.update(names)
        for name, tensor in header["tensors"].items():
            tensor_count += 1
            count = 1
            for dim in tensor.get("shape") or []:
                count *= int(dim)
            total_params += count
            offsets = tensor.get("data_offsets")
            if offsets and len(offsets) == 2:
                tensor_data_bytes += int(offsets[1]) - int(offsets[0])
            dtype = str(tensor.get("dtype", "unknown"))
            dtype_counts[dtype] = dtype_counts.get(dtype, 0) + count

    index_path = root / "model.safetensors.index.json"
    if not index_path.exists():
        candidates = sorted(root.glob("*.safetensors.index.json"))
        index_path = candidates[0] if candidates else None
    index_summary = None
    if index_path is not None:
        try:
            index = read_json(index_path)
            weight_map = index.get("weight_map", {})
            index_summary = {"file": index_path.name, "entries": len(weight_map)}
            metadata = index.get("metadata") or {}
            total_size = metadata.get("total_size")
            if total_size is not None:
                index_summary["total_size"] = total_size
                matches_file = abs(int(total_size) - total_weight_bytes) <= 1024
                matches_data = abs(int(total_size) - tensor_data_bytes) <= 1024
                if not (matches_file or matches_data):
                    warnings.append(
                        "index metadata.total_size does not match either shard file sizes "
                        "or tensor data sizes"
                    )
            referenced_files = set(weight_map.values())
            for filename in sorted(referenced_files):
                if not (root / filename).exists():
                    errors.append(f"index references missing shard: {filename}")
            for filename, keys in file_keys.items():
                if filename not in referenced_files:
                    warnings.append(f"shard not referenced by index: {filename}")
            indexed_names = set(weight_map)
            missing = indexed_names - tensor_names
            extra = tensor_names - indexed_names
            if missing:
                errors.append(f"{len(missing)} tensors in index are missing from shards")
            if extra:
                warnings.append(f"{len(extra)} tensors in shards are absent from index")
        except Exception as exc:
            errors.append(f"Invalid safetensors index: {exc}")

    coverage = _verify_quant_tensors(quant_config, tensor_names, errors, warnings)
    tokenizer_files = [
        name
        for name in ("tokenizer.json", "tokenizer_config.json", "special_tokens_map.json")
        if (root / name).exists()
    ]
    if not tokenizer_files:
        warnings.append("No tokenizer files found")

    load_check_result = None
    if load_check:
        load_check_result = _load_check(root, errors, warnings, token=token)

    if strict:
        errors.extend(warnings)
        warnings = []

    status = "ok" if not errors else "error"
    if status == "ok" and warnings:
        status = "warning"

    return {
        "path": str(root),
        "status": status,
        "errors": errors,
        "warnings": warnings,
        "config": {
            "model_type": config.get("model_type"),
            "architectures": config.get("architectures"),
            "dtype": str(config.get("dtype") or config.get("torch_dtype")),
        },
        "quantization": quant_summary,
        "files": {
            "weight_file_count": len(weight_files),
            "weight_bytes": total_weight_bytes,
            "index": index_summary,
            "tokenizer_files": tokenizer_files,
            "directory_bytes": sum(p.stat().st_size for p in root.rglob("*") if p.is_file()),
        },
        "tensors": {
            "count": tensor_count,
            "parameters": total_params,
            "dtypes": dtype_counts,
        },
        "coverage": coverage,
        "load_check": load_check_result,
    }


def _verify_quant_config(
    quant_config: Any,
    errors: list[str],
    warnings: list[str],
) -> dict[str, Any] | None:
    if quant_config is None:
        warnings.append("No quantization_config in config.json (this may be a transform-only model)")
        return None
    if not isinstance(quant_config, dict):
        errors.append("quantization_config is not an object")
        return None
    summary = {
        key: _plain(quant_config.get(key))
        for key in (
            "quant_method",
            "format",
            "quantization_status",
            "config_groups",
            "ignore",
            "kv_cache_scheme",
            "version",
            "global_compression_ratio",
        )
        if key in quant_config
    }
    method = quant_config.get("quant_method")
    if method not in (None, "compressed-tensors"):
        warnings.append(f"Unexpected quant_method: {method}")
    status = quant_config.get("quantization_status")
    if status not in (None, "compressed", "frozen"):
        warnings.append(f"quantization_status is {status!r}, expected 'compressed'")

    if status == "compressed" and quant_config.get("format"):
        format_name = quant_config.get("format")
        groups = quant_config.get("config_groups") or {}
        if format_name == "fakequant":
            warnings.append(
                "format 'fakequant' indicates an unquantized or sparsity-only checkpoint"
            )
        elif groups:
            try:
                from compressed_tensors.config import CompressionFormat

                CompressionFormat(format_name)
            except Exception as exc:
                errors.append(f"Unknown compression format {format_name!r}: {exc}")

    try:
        from compressed_tensors.quantization import QuantizationConfig

        QuantizationConfig.model_validate(quant_config)
    except Exception as exc:
        warnings.append(f"compressed-tensors config validation warning: {exc}")

    transform_config = quant_config.get("transform_config")
    if transform_config:
        try:
            from compressed_tensors.transform import TransformConfig

            TransformConfig.model_validate(transform_config)
        except Exception as exc:
            warnings.append(f"transform_config validation warning: {exc}")

    return summary


def _verify_quant_tensors(
    quant_config: Any,
    names: set[str],
    errors: list[str],
    warnings: list[str],
) -> dict[str, Any]:
    if not isinstance(quant_config, dict):
        return {}
    packed = {name for name in names if name.endswith(".weight_packed")}
    for name in sorted(packed):
        scale = name[: -len("weight_packed")] + "weight_scale"
        if scale not in names:
            errors.append(f"packed weight without scale: {name}")

    groups = quant_config.get("config_groups") or {}
    static_activations = False
    for group in groups.values() if isinstance(groups, dict) else []:
        if not isinstance(group, dict):
            continue
        for key in ("input_activations", "output_activations"):
            args = group.get(key)
            if isinstance(args, dict) and args.get("dynamic") is False:
                static_activations = True
    if static_activations and not any(name.endswith(".input_scale") for name in names):
        warnings.append("Static activation quantization configured but no input_scale tensors found")

    fmt = str(quant_config.get("format") or "").lower()
    has_global_scale = any(
        name.endswith(".weight_global_scale") or name.endswith(".input_global_scale")
        for name in names
    )
    if "nvfp4" in fmt and not has_global_scale:
        warnings.append("NVFP4 format configured but no global-scale tensors found")
    has_weight_shape = any(name.endswith(".weight_shape") for name in names)
    if packed and not has_weight_shape:
        warnings.append("Packed weights found without weight_shape tensors")

    return {
        "packed_weights": len(packed),
        "scaled_weights": sum(name.endswith(".weight_scale") for name in names),
        "input_scales": sum(name.endswith(".input_scale") for name in names),
        "weight_shapes": sum(name.endswith(".weight_shape") for name in names),
        "global_scales": sum(
            name.endswith(".weight_global_scale") or name.endswith(".input_global_scale")
            for name in names
        ),
        "static_activations": static_activations,
    }


def _load_check(root: Path, errors: list[str], warnings: list[str], token: str | None) -> dict[str, Any]:
    result: dict[str, Any] = {"ok": False, "config": False, "tokenizer": False, "model": False}
    try:
        from transformers import AutoConfig

        AutoConfig.from_pretrained(str(root), local_files_only=True)
        result["config"] = True
    except Exception as exc:
        errors.append(f"AutoConfig failed: {exc}")
        return result
    try:
        from transformers import AutoTokenizer

        AutoTokenizer.from_pretrained(str(root), local_files_only=True, token=token)
        result["tokenizer"] = True
    except Exception as exc:
        warnings.append(f"AutoTokenizer failed: {exc}")
    try:
        from transformers import AutoModelForCausalLM

        AutoModelForCausalLM.from_pretrained(
            str(root),
            local_files_only=True,
            low_cpu_mem_usage=True,
            device_map="meta",
        )
        result["model"] = True
    except Exception as exc:
        errors.append(f"AutoModelForCausalLM meta-device load failed: {exc}")
    result["ok"] = result["config"] and result["model"]
    return result


def human_model_summary(info: dict[str, Any]) -> str:
    config = info["config"]
    lines = [
        f"model        {info['model']}",
        f"source       {info['source']} (revision {info.get('revision')})",
        f"architecture {', '.join(config.get('architectures') or []) or config.get('model_type')}",
        f"parameters   {_human_int(info.get('parameters'))}",
        f"weights      {human_bytes(info['files'].get('weight_bytes') or info['files'].get('total_bytes'))} "
        f"in {info['files'].get('weight_file_count')} shard(s)",
        f"dtype        {config.get('dtype')}",
        f"layers       {config.get('num_hidden_layers')}",
    ]
    quant = info.get("quantization")
    if quant:
        lines.append(
            "quantization "
            f"{quant.get('quant_method')} / {quant.get('format')} / {quant.get('quantization_status')}"
        )
    else:
        lines.append("quantization none")
    if info.get("tokenizer_files"):
        lines.append(f"tokenizer    {', '.join(info['tokenizer_files'])}")
    return "\n".join(lines)


def _human_int(value: Any) -> str:
    if value is None:
        return "unknown"
    return f"{int(value):,}"
