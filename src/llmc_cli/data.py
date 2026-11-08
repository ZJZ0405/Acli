"""Calibration dataset resolution, including local files and saved datasets."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from llmc_cli.errors import InputError


@dataclass
class DatasetOptions:
    dataset: str | None = None
    dataset_path: str | None = None
    split: str | None = None
    config_name: str | None = None
    num_samples: int = 512
    max_seq_length: int | None = 2048
    batch_size: int = 1
    text_column: str = "text"
    data_collator: str = "truncation"
    shuffle: bool = True
    streaming: bool = False
    overwrite_cache: bool = False
    preprocessing_workers: int | None = None
    dataloader_workers: int = 0
    concatenate: bool = False
    pad_to_max_length: bool = False
    moe_calibrate_all_experts: bool = True
    min_tokens_per_module: float | None = None


LOADERS = {
    ".json": "json",
    ".jsonl": "json",
    ".csv": "csv",
    ".parquet": "parquet",
    ".arrow": "arrow",
}

# llm-compressor 0.15.0 resolves dataset= through its own registry, not through
# datasets.load_dataset. These are the registered names and the repositories
# they load behind the scenes.
BUILTIN_DATASETS: dict[str, dict[str, Any]] = {
    "c4": {"repo": "allenai/c4", "config": "en", "splits": ["train", "validation"]},
    "cnn_dailymail": {"repo": "cnn_dailymail", "config": "3.0.0", "splits": ["train", "validation", "test"]},
    "evolcodealpaca": {"repo": "theblackcat102/evol-codealpaca-v1", "splits": ["train"]},
    "flickr": {"repo": "lmms-lab/flickr30k", "aliases": ["flickr30k"], "splits": ["test"]},
    "gsm8k": {"repo": "openai/gsm8k", "config": "main", "splits": ["train", "test"]},
    "open_platypus": {"repo": "garage-bAInd/Open-Platypus", "splits": ["train"]},
    "peoples_speech": {"repo": "MLCommons/peoples_speech", "config": "test", "splits": ["test"]},
    "perfectblend": {"repo": "mlabonne/open-perfectblend", "splits": ["train"]},
    "ultrachat_200k": {
        "repo": "HuggingFaceH4/ultrachat_200k",
        "aliases": ["ultrachat"],
        "splits": ["train_sft", "test_sft", "train_gen", "test_gen"],
        "default_split": "train_sft",
    },
    "wikitext": {"repo": "Salesforce/wikitext", "config": "wikitext-2-raw-v1", "splits": ["train", "validation", "test"]},
}


def _normalized_dataset_name(name: str) -> str:
    return name.strip().lower().replace("-", "_")


def lookup_builtin_dataset(name: str) -> tuple[str, dict[str, Any]] | None:
    normalized = _normalized_dataset_name(name)
    for key, info in BUILTIN_DATASETS.items():
        if normalized == key:
            return key, info
        if normalized in {_normalized_dataset_name(alias) for alias in info.get("aliases", [])}:
            return key, info
    return None


def builtin_dataset_list() -> list[dict[str, Any]]:
    return [
        {
            "registry_name": key,
            "aliases": info.get("aliases", []),
            "repo": info["repo"],
            "config": info.get("config"),
            "splits": info.get("splits", []),
            "default_split": info.get("default_split"),
        }
        for key, info in sorted(BUILTIN_DATASETS.items())
    ]


def load_hf_dataset(
    name: str,
    *,
    config_name: str | None = None,
    split: str | None = None,
) -> Any:
    """Load an arbitrary Hugging Face dataset into memory for oneshot.

    llm-compressor's registry does not accept arbitrary repo ids, so the CLI
    loads them itself and passes a Dataset object.
    """
    from datasets import get_dataset_split_names, load_dataset

    target_split = split
    if target_split is None:
        try:
            available = list(get_dataset_split_names(name, config_name=config_name))
        except Exception as exc:
            raise InputError(
                f"Could not resolve dataset {name!r}: {exc}",
                hint="Check the dataset id, or pass --dataset-config / --split.",
            ) from exc
        if not available:
            raise InputError(f"Dataset {name!r} has no splits")
        target_split = "train" if "train" in available else available[0]
    try:
        return load_dataset(name, name=config_name, split=target_split)
    except Exception as exc:
        raise InputError(
            f"Could not load dataset {name!r} (config={config_name!r}, split={target_split!r}): {exc}",
            hint="Pass --dataset-config and --split explicitly, or use --dataset-path for a local file.",
        ) from exc


def normalize_text_dataset(dataset: Any, text_column: str) -> tuple[Any, str]:
    """Make a plain-text column named 'text' when possible."""
    from datasets import Dataset

    if not isinstance(dataset, Dataset):
        return dataset, text_column
    columns = list(dataset.column_names)
    if "input_ids" in columns:
        dataset = dataset.filter(
            lambda row: len(row.get("input_ids") or []) > 0,
            desc="Filtering empty samples",
        )
        return dataset, text_column
    if "text" in columns:
        dataset = _filter_empty_text(dataset)
        return dataset, "text"
    if text_column in columns:
        dataset = _filter_empty_text(dataset.rename_column(text_column, "text"))
        return dataset, "text"
    if "prompt" in columns:
        dataset = _filter_empty_text(dataset.rename_column("prompt", "text"))
        return dataset, "text"
    if "content" in columns:
        dataset = _filter_empty_text(dataset.rename_column("content", "text"))
        return dataset, "text"
    if "messages" in columns:
        def messages_to_text(row):
            messages = row.get("messages") or []
            parts = []
            for message in messages:
                if isinstance(message, dict) and message.get("content") is not None:
                    parts.append(str(message["content"]))
            return {"text": "\n".join(parts)}

        dataset = dataset.map(messages_to_text, remove_columns=columns)
        return _filter_empty_text(dataset), "text"
    if "conversations" in columns:
        def conversations_to_text(row):
            parts = []
            for message in row.get("conversations") or []:
                if isinstance(message, dict) and message.get("value") is not None:
                    parts.append(str(message["value"]))
            return {"text": "\n".join(parts)}

        dataset = dataset.map(conversations_to_text, remove_columns=columns)
        return _filter_empty_text(dataset), "text"
    raise InputError(
        f"Dataset has no usable text column (columns: {', '.join(columns) or 'none'})",
        hint="Pass --text-column NAME, or pass a pre-tokenized dataset containing input_ids.",
    )


def _filter_empty_text(dataset: Any) -> Any:
    return dataset.filter(
        lambda row: bool(str(row.get("text") or "").strip()),
        desc="Filtering empty text samples",
    )


def _pick_split(dataset: Any, split: str | None) -> Any:
    from datasets import Dataset, DatasetDict

    if isinstance(dataset, Dataset):
        return dataset
    if isinstance(dataset, DatasetDict):
        if split and split in dataset:
            return dataset[split]
        if "train" in dataset:
            return dataset["train"]
        if len(dataset) > 0:
            return next(iter(dataset.values()))
        raise InputError("Dataset has no splits")
    return dataset


def load_local_dataset(path: str | Path, *, split: str | None = None, streaming: bool = False) -> Any:
    from datasets import Dataset, load_dataset, load_from_disk

    root = Path(path).expanduser()
    if not root.exists():
        raise InputError(f"Dataset path does not exist: {path}")

    if root.is_dir() and ((root / "state.json").exists() or (root / "dataset_info.json").exists()):
        dataset = load_from_disk(str(root))
        return _pick_split(dataset, split)

    if root.is_file():
        suffix = root.suffix.lower()
        loader = LOADERS.get(suffix)
        if loader is None:
            raise InputError(
                f"Unsupported dataset file type {suffix!r}",
                hint="Supported: .json, .jsonl, .csv, .parquet, .arrow, or a saved datasets directory.",
            )
        if loader == "arrow":
            return _pick_split(Dataset.from_file(str(root)), split)
        return load_dataset(
            loader,
            data_files=str(root),
            split=split or "train",
            streaming=streaming,
        )

    candidates: list[Path] = []
    for suffix in LOADERS:
        candidates.extend(sorted(root.glob(f"*{suffix}")))
    if not candidates:
        raise InputError(
            f"No supported dataset files in directory: {path}",
            hint="Expected .json/.jsonl/.csv/.parquet/.arrow files or a saved datasets directory.",
        )
    suffixes = {p.suffix.lower() for p in candidates}
    if len(suffixes) > 1:
        raise InputError(
            f"Directory contains multiple dataset file types: {sorted(suffixes)}",
            hint="Keep a single file type per directory or pass a specific file.",
        )
    suffix = candidates[0].suffix.lower()
    loader = LOADERS[suffix]
    if loader == "arrow":
        raise InputError("Pass a single .arrow file, not a directory of arrow files")
    dataset = load_dataset(
        loader,
        data_files=[str(p) for p in candidates],
        split=split or "train",
        streaming=streaming,
    )
    return dataset


def resolve_calibration_dataset(options: DatasetOptions) -> tuple[Any | None, dict[str, Any]]:
    """Return a value suitable for oneshot(dataset=...) plus metadata."""
    if options.dataset and options.dataset_path:
        raise InputError("Pass either --dataset or --dataset-path, not both")
    if options.streaming:
        raise InputError(
            "Streaming calibration datasets are not supported by llm-compressor 0.15.0",
            hint="Download the dataset first or pass a local --dataset-path file.",
        )

    if options.dataset_path:
        dataset = load_local_dataset(
            options.dataset_path,
            split=options.split,
            streaming=False,
        )
        dataset, text_column = normalize_text_dataset(dataset, options.text_column)
        rows = getattr(dataset, "num_rows", None)
        metadata = {
            "source": "local",
            "path": str(Path(options.dataset_path).expanduser()),
            "split": options.split or "train",
            "rows": rows,
            "text_column": text_column,
        }
        if options.num_samples and rows is not None and options.num_samples > rows:
            metadata["requested_samples"] = options.num_samples
            metadata["effective_samples"] = rows
        return dataset, metadata

    if options.dataset:
        candidate_path = Path(options.dataset).expanduser()
        if candidate_path.exists():
            dataset = load_local_dataset(
                candidate_path,
                split=options.split,
                streaming=False,
            )
            dataset, text_column = normalize_text_dataset(dataset, options.text_column)
            return dataset, {
                "source": "local",
                "path": str(candidate_path),
                "split": options.split or "train",
                "rows": getattr(dataset, "num_rows", None),
                "text_column": text_column,
            }

        builtin = lookup_builtin_dataset(options.dataset)
        if builtin is not None:
            registry_name, info = builtin
            return options.dataset, {
                "source": "llmcompressor-registry",
                "name": options.dataset,
                "registry_name": registry_name,
                "repo": info["repo"],
                "config_name": options.config_name,
                "split": options.split,
                "text_column": options.text_column,
            }

        # Arbitrary HF repo ids are not understood by llm-compressor's registry;
        # load them here and hand oneshot a Dataset object.
        dataset = load_hf_dataset(
            options.dataset,
            config_name=options.config_name,
            split=options.split,
        )
        dataset, text_column = normalize_text_dataset(dataset, options.text_column)
        return dataset, {
            "source": "huggingface",
            "name": options.dataset,
            "config_name": options.config_name,
            "split": options.split or "auto",
            "rows": getattr(dataset, "num_rows", None),
            "text_column": text_column,
            "loaded_by_cli": True,
        }

    return None, {"source": "none"}


def oneshot_dataset_kwargs(
    options: DatasetOptions,
    dataset_value: Any,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the dataset-related subset of oneshot() kwargs."""
    metadata = metadata or {}
    local_object = not isinstance(dataset_value, (str, type(None)))
    kwargs: dict[str, Any] = {
        "dataset": dataset_value,
        "dataset_config_name": options.config_name if not local_object else None,
        "splits": None if local_object else options.split,
        "num_calibration_samples": options.num_samples,
        "max_seq_length": options.max_seq_length,
        "batch_size": options.batch_size,
        "text_column": metadata.get("text_column", options.text_column),
        "data_collator": options.data_collator,
        "shuffle_calibration_samples": options.shuffle,
        "streaming": False,
        "overwrite_cache": options.overwrite_cache,
        "preprocessing_num_workers": options.preprocessing_workers,
        "dataloader_num_workers": options.dataloader_workers,
        "concatenate_data": options.concatenate,
        "pad_to_max_length": options.pad_to_max_length,
        "moe_calibrate_all_experts": options.moe_calibrate_all_experts,
        "min_tokens_per_module": options.min_tokens_per_module,
    }
    return {key: value for key, value in kwargs.items() if value is not None}
