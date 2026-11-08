"""Human-readable renderers kept separate from the JSON contract."""

from __future__ import annotations

from typing import Any

from llmc_cli.utils import human_bytes, human_count


def fmt_version(data: dict[str, Any]) -> str:
    return "\n".join(f"{key:<16} {value}" for key, value in data.items())


def fmt_doctor(data: dict[str, Any]) -> str:
    lines = [f"status           {data['status']}"]
    py = data["python"]
    lines.append(f"python           {py['version']} ({py['executable']})")
    lines.append(f"platform         {py['platform']}")
    packages = data["packages"]
    lines.append(
        "packages         "
        + ", ".join(f"{name}={version}" for name, version in packages.items() if version)
    )
    missing = [name for name, version in packages.items() if version is None]
    if missing:
        lines.append(f"missing          {', '.join(missing)}")
    gpu = data["gpu"]
    lines.append(
        f"cuda             available={gpu['available']} torch_cuda={gpu.get('torch_cuda_version')} "
        f"cudnn={gpu.get('cudnn_version')}"
    )
    for device in gpu.get("devices", []):
        lines.append(
            f"gpu[{device['index']}]          {device['name']} "
            f"cc {device['capability']} vram {human_bytes(device['total_memory_bytes'])}"
        )
    if gpu.get("bf16_supported") is not None:
        lines.append(f"bf16             {gpu['bf16_supported']}")
    memory = data.get("memory", {}).get("total_bytes")
    lines.append(f"ram              {human_bytes(memory)}")
    disk = data["disk"]
    lines.append(
        f"disk cwd         {human_bytes(disk['cwd'].get('free_bytes'))} free of "
        f"{human_bytes(disk['cwd'].get('total_bytes'))}"
    )
    hf = disk.get("hf_home", {})
    if hf.get("free_bytes") is not None:
        lines.append(f"disk hf_home     {human_bytes(hf['free_bytes'])} free")
    env = data["env"]
    flags = [key for key in ("HF_TOKEN", "HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE") if env.get(key)]
    if flags:
        lines.append(f"env              {', '.join(flags)}")
    if data["issues"]:
        lines.append("issues:")
        lines.extend(f"  - {issue}" for issue in data["issues"])
    return "\n".join(lines)


def _quant_args_label(args: dict[str, Any] | None) -> str:
    if not args:
        return "-"
    bits = args.get("num_bits")
    typ = args.get("type")
    strategy = args.get("strategy")
    group = args.get("group_size")
    dynamic = args.get("dynamic")
    parts = [f"{bits}-bit {typ}"]
    if strategy:
        parts.append(str(strategy))
    if group:
        parts.append(f"g{group}")
    if dynamic is True:
        parts.append("dyn")
    elif dynamic is False:
        parts.append("static")
    elif dynamic:
        parts.append(str(dynamic))
    return " ".join(parts)


def fmt_schemes(data: list[dict[str, Any]]) -> str:
    rows = [
        (
            scheme["name"],
            _quant_args_label(scheme.get("weights")),
            _quant_args_label(scheme.get("input_activations")),
            "yes" if scheme["requires_calibration"] else "no",
        )
        for scheme in data
    ]
    widths = [max(len(row[i]) for row in rows + [("NAME", "WEIGHTS", "ACTIVATIONS", "CAL")]) for i in range(4)]
    lines = [
        f"{'NAME':<{widths[0]}}  {'WEIGHTS':<{widths[1]}}  {'ACTIVATIONS':<{widths[2]}}  CAL"
    ]
    for row in rows:
        lines.append(
            f"{row[0]:<{widths[0]}}  {row[1]:<{widths[1]}}  {row[2]:<{widths[2]}}  {row[3]}"
        )
    return "\n".join(lines)


def fmt_scheme_detail(data: dict[str, Any]) -> str:
    lines = [
        f"scheme         {data['name']}",
        f"targets        {', '.join(data['targets'])}",
        f"weights        {_quant_args_label(data.get('weights'))}",
        f"activations    {_quant_args_label(data.get('input_activations'))}",
        f"calibration    {'required' if data['requires_calibration'] else 'not required'}",
        f"data-free      {data['data_free_eligible']}",
        f"min compute    {data['min_compute_capability'][0]}.{data['min_compute_capability'][1]}",
    ]
    if data.get("notes"):
        lines.append(f"notes          {data['notes']}")
    return "\n".join(lines)


def fmt_modifiers(data: list[dict[str, Any]]) -> str:
    rows = [
        (
            item["name"],
            item["category"],
            "yes" if item["requires_calibration"] else "no",
            item["summary"],
        )
        for item in data
    ]
    widths = [max(len(row[i]) for row in rows + [("NAME", "CATEGORY", "CAL", "SUMMARY")]) for i in range(4)]
    lines = [f"{'NAME':<{widths[0]}}  {'CATEGORY':<{widths[1]}}  CAL  SUMMARY"]
    for row in rows:
        lines.append(
            f"{row[0]:<{widths[0]}}  {row[1]:<{widths[1]}}  {row[2]:<3}  {row[3]}"
        )
    return "\n".join(lines)


def fmt_modifier_detail(data: dict[str, Any]) -> str:
    lines = [
        f"modifier       {data['name']}",
        f"module         {data['module']}",
        f"category       {data['category']}",
        f"calibration    {'required' if data['requires_calibration'] else 'not required'}",
        f"summary        {data['summary']}",
        "fields:",
    ]
    for field in data["fields"]:
        required = " required" if field["required"] else ""
        default = "" if field["required"] else f" = {field['default']!r}"
        lines.append(f"  {field['name']:<28} {field['type']}{required}{default}")
    return "\n".join(lines)


def fmt_dataset(data: dict[str, Any]) -> str:
    lines = [
        f"dataset        {data['dataset']} ({data['source']})",
        f"resolved repo  {data.get('resolved_repo')}",
        f"registry name  {data.get('registry_name')}",
        f"config         {data.get('config_name')}",
        f"split          {data.get('split')}",
        f"streaming      {data.get('streaming')}",
        f"columns        {', '.join(data.get('columns') or [])}",
    ]
    if data.get("splits"):
        lines.append("splits:")
        for name, info in data["splits"].items():
            lines.append(
                f"  {name:<20} {human_count(info.get('num_examples'))} rows, "
                f"{human_bytes(info.get('num_bytes'))}"
            )
    if data.get("text_column_candidates"):
        lines.append(f"text columns   {', '.join(data['text_column_candidates'])}")
    if data.get("samples"):
        lines.append("samples:")
        for index, sample in enumerate(data["samples"], start=1):
            text = str(sample)
            if len(text) > 200:
                text = text[:197] + "..."
            lines.append(f"  [{index}] {text}")
    return "\n".join(lines)


def fmt_dataset_list(data: list[dict[str, Any]]) -> str:
    rows = [
        (
            item["registry_name"],
            item["repo"],
            ",".join(item.get("aliases") or []) or "-",
            ",".join(item.get("splits") or []) or "-",
        )
        for item in data
    ]
    widths = [max(len(str(row[i])) for row in rows + [("NAME", "REPO", "ALIASES", "SPLITS")]) for i in range(4)]
    lines = [f"{'NAME':<{widths[0]}}  {'REPO':<{widths[1]}}  {'ALIASES':<{widths[2]}}  SPLITS"]
    for row in rows:
        lines.append(f"{row[0]:<{widths[0]}}  {row[1]:<{widths[1]}}  {row[2]:<{widths[2]}}  {row[3]}")
    return "\n".join(lines)


def fmt_recipe_validate(data: dict[str, Any]) -> str:
    lines = [
        f"stage          {data['stage']}",
        f"modifiers      {data['modifier_count']}",
        f"calibration    {'required' if data['requires_calibration'] else 'not required'}",
    ]
    for modifier in data["modifiers"]:
        cal = " (calibration)" if modifier["requires_calibration"] else ""
        lines.append(f"  - {modifier['type']}{cal}")
    lines.append("normalized recipe:")
    lines.append(_indent(data.get("yaml", "")))
    return "\n".join(lines)


def fmt_recipe_presets(data: list[dict[str, Any]]) -> str:
    lines = []
    for preset in data:
        cal = "calibration" if preset["requires_calibration"] else "data-free"
        lines.append(f"{preset['name']:<24} {cal:<12} {preset['description']}")
    return "\n".join(lines)


def fmt_run(data: dict[str, Any]) -> str:
    lines = [
        f"model          {data.get('model')}",
        f"output         {data.get('output_dir')}",
    ]
    if data.get("scheme"):
        lines.append(f"scheme         {data['scheme']}")
    if data.get("methods"):
        lines.append(f"methods        {', '.join(str(m) for m in data['methods'])}")
    dataset = data.get("dataset") or {}
    if dataset.get("source") == "none":
        lines.append("dataset        none (data-free)")
    elif dataset:
        label = dataset.get("path") or dataset.get("name")
        split = dataset.get("split")
        rows = dataset.get("rows")
        detail = f"{label}"
        if split:
            detail += f" split={split}"
        if rows is not None:
            detail += f" rows={rows}"
        lines.append(f"dataset        {detail}")
    summary = data.get("recipe_summary") or {}
    if summary.get("modifiers"):
        lines.append(
            "recipe         "
            + " -> ".join(modifier["type"] for modifier in summary["modifiers"])
        )
    if data.get("dry_run"):
        lines.append("mode           dry-run (nothing loaded or written)")
        if data.get("recipe_yaml"):
            lines.append("recipe yaml:")
            lines.append(_indent(data["recipe_yaml"]))
        if data.get("run_warnings"):
            lines.append("warnings:")
            lines.extend(f"  - {warning}" for warning in data["run_warnings"])
        return "\n".join(lines)

    lines.append(f"elapsed        {data.get('elapsed_s', '?')} s")
    output = data.get("output") or {}
    if output:
        lines.append(f"artifact       {output.get('status')}")
        files = output.get("files") or {}
        tensors = output.get("tensors") or {}
        if files:
            lines.append(
                f"files          {files.get('weight_file_count')} shard(s), "
                f"{human_bytes(output.get('bytes'))}"
            )
        if tensors:
            lines.append(
                f"tensors        {tensors.get('count')} tensors, "
                f"{human_count(tensors.get('parameters'))} parameters"
            )
        quant = output.get("quantization")
        if quant:
            lines.append(
                f"quantization   {quant.get('quant_method')} / {quant.get('format')} / "
                f"{quant.get('quantization_status')}"
            )
        for error in output.get("errors") or []:
            lines.append(f"error          {error}")
        for warning in output.get("warnings") or []:
            lines.append(f"warning        {warning}")
    if data.get("run_warnings"):
        for warning in data["run_warnings"]:
            lines.append(f"warning        {warning}")
    return "\n".join(lines)


def fmt_verify(data: dict[str, Any]) -> str:
    lines = [
        f"path           {data['path']}",
        f"status         {data['status']}",
        f"weights        {data['files']['weight_file_count']} shard(s), "
        f"{human_bytes(data['files']['weight_bytes'])}, "
        f"{data['tensors']['count']} tensors, {human_count(data['tensors']['parameters'])} parameters",
    ]
    quant = data.get("quantization")
    if quant:
        lines.append(
            f"quantization   {quant.get('quant_method')} / {quant.get('format')} / "
            f"{quant.get('quantization_status')}"
        )
    coverage = data.get("coverage") or {}
    if coverage:
        lines.append(
            "coverage       "
            f"{coverage.get('packed_weights', 0)} packed, "
            f"{coverage.get('scaled_weights', 0)} scaled, "
            f"{coverage.get('global_scales', 0)} global-scale, "
            f"{coverage.get('input_scales', 0)} input-scale tensors"
        )
    if data.get("errors"):
        lines.append("errors:")
        lines.extend(f"  - {error}" for error in data["errors"])
    if data.get("warnings"):
        lines.append("warnings:")
        lines.extend(f"  - {warning}" for warning in data["warnings"])
    if data.get("load_check"):
        lines.append(f"load check     {data['load_check']}")
    return "\n".join(lines)


def _indent(text: str, prefix: str = "  ") -> str:
    return "\n".join(prefix + line for line in str(text).rstrip().splitlines())
