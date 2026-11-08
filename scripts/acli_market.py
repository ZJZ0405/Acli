#!/usr/bin/env python3
"""Agent-agnostic client for the Acli plugin marketplace.

The registry is plain JSON. This tool is one way to read it; any agent that can
run a shell command can use the same commands and JSON output.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
MARKETPLACE = ROOT / "marketplace"
INDEX_PATH = MARKETPLACE / "index.json"
SCHEMA_VERSION = "1"
TOOL_VERSION = "0.1.0"
EXIT_CODES = {
    "0": "success",
    "1": "internal_error",
    "2": "usage",
    "3": "not_found",
    "4": "invalid_catalog",
}


class MarketError(Exception):
    def __init__(self, code: str, message: str, exit_code: int = 1) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.exit_code = exit_code


class Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:  # type: ignore[override]
        raise MarketError("usage", message, 2)


def load_json(path: Path) -> Any:
    try:
        with path.open(encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError as exc:
        raise MarketError("invalid_catalog", f"missing file: {path}", 4) from exc
    except json.JSONDecodeError as exc:
        raise MarketError("invalid_catalog", f"invalid JSON in {path}: {exc}", 4) from exc


def load_index() -> dict[str, Any]:
    index = load_json(INDEX_PATH)
    if not isinstance(index, dict) or index.get("schema_version") != 1:
        raise MarketError("invalid_catalog", "marketplace/index.json is not schema_version 1", 4)
    return index


def plugin_dir(entry: dict[str, Any]) -> Path:
    return (MARKETPLACE / entry["manifest"]).parent


def load_plugins() -> list[dict[str, Any]]:
    plugins = []
    for entry in load_index().get("plugins", []):
        directory = plugin_dir(entry)
        manifest = load_json(directory / "plugin.json")
        plugins.append({**manifest, "_dir": directory, "_index_id": entry["id"]})
    return plugins


def summary(plugin: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": plugin["id"],
        "name": plugin.get("name", plugin["id"]),
        "version": plugin.get("version"),
        "kind": plugin.get("kind"),
        "summary": plugin.get("summary"),
        "keywords": plugin.get("keywords", []),
    }


def find_plugin(plugin_id: str) -> dict[str, Any]:
    for plugin in load_plugins():
        if plugin["id"] == plugin_id:
            return plugin
    raise MarketError("not_found", f"unknown plugin: {plugin_id}", 3)


def parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---", 4)
    if end == -1:
        return {}, text
    meta: dict[str, str] = {}
    for line in text[4:end].splitlines():
        if ":" not in line or line.startswith((" ", "\t", "#")):
            continue
        key, value = line.split(":", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        meta[key.strip()] = value
    body_start = end + len("\n---")
    if body_start < len(text) and text[body_start] == "\n":
        body_start += 1
    return meta, text[body_start:]


def guide_path(plugin: dict[str, Any]) -> Path:
    usage = plugin.get("usage") or {}
    if usage.get("mode") not in ("guide", "both"):
        raise MarketError("not_found", f"{plugin['id']} has no usage guide", 3)
    path = plugin["_dir"] / usage["guide"]
    if not path.is_file():
        raise MarketError("invalid_catalog", f"missing guide: {path}", 4)
    return path


def guide_text(plugin: dict[str, Any], fmt: str) -> str:
    text = guide_path(plugin).read_text(encoding="utf-8")
    meta, body = parse_frontmatter(text)
    if fmt == "markdown":
        return text
    if fmt == "skill":
        name = meta.get("name", plugin["id"])
        description = meta.get("description", plugin.get("summary", ""))
        return (
            "---\n"
            f"name: {json.dumps(name, ensure_ascii=False)}\n"
            f"description: {json.dumps(description, ensure_ascii=False)}\n"
            "---\n\n"
            f"{body.lstrip()}"
        )
    raise MarketError("usage", f"unknown guide format: {fmt}", 2)


def plan(plugin: dict[str, Any]) -> dict[str, Any]:
    interface = plugin.get("interface", {})
    return {
        "id": plugin["id"],
        "version": plugin["version"],
        "distributions": plugin.get("distributions", []),
        "verify": [
            " ".join(interface.get("version_command", [])),
            " ".join(interface.get("schema_command", [])).replace("{file}", "/tmp/acli-schema.json"),
        ],
        "guide": (plugin.get("usage") or {}).get("guide"),
        "authority": "The installed CLI's --help and schema are authoritative.",
    }


def validate() -> dict[str, Any]:
    errors: list[str] = []
    index = load_index()
    if not index.get("name"):
        errors.append("index.name is required")
    seen: set[str] = set()
    for entry in index.get("plugins", []):
        plugin_id = entry.get("id", "")
        if plugin_id in seen:
            errors.append(f"duplicate plugin id: {plugin_id}")
        seen.add(plugin_id)
        if not plugin_id:
            errors.append("index entry missing id")
            continue
        directory = plugin_dir(entry)
        try:
            plugin = load_json(directory / "plugin.json")
        except MarketError as exc:
            errors.append(str(exc))
            continue
        if plugin.get("schema_version") != 1:
            errors.append(f"{plugin_id}: schema_version must be 1")
        if plugin.get("id") != plugin_id:
            errors.append(f"{plugin_id}: manifest id is {plugin.get('id')!r}")
        for field in ("name", "version", "summary", "kind"):
            if not plugin.get(field):
                errors.append(f"{plugin_id}: missing {field}")
        interface = plugin.get("interface") or {}
        for field in ("executable", "version_command", "help_command", "schema_command", "schema_version"):
            if not interface.get(field):
                errors.append(f"{plugin_id}: missing interface.{field}")
        if interface.get("non_interactive") is not True:
            errors.append(f"{plugin_id}: interface.non_interactive must be true")
        usage = plugin.get("usage") or {}
        if usage.get("mode") in ("guide", "both"):
            guide = usage.get("guide")
            if not guide:
                errors.append(f"{plugin_id}: usage.guide is required for mode {usage.get('mode')}")
            else:
                path = directory / guide
                if not path.is_file():
                    errors.append(f"{plugin_id}: missing guide {path}")
                else:
                    meta, _ = parse_frontmatter(path.read_text(encoding="utf-8"))
                    for field in ("name", "description"):
                        if not meta.get(field):
                            errors.append(f"{plugin_id}: guide frontmatter missing {field}")
        if not plugin.get("distributions"):
            errors.append(f"{plugin_id}: missing distributions")
    return {"ok": not errors, "plugins": len(index.get("plugins", [])), "errors": errors}


def emit(data: Any, extra: dict[str, Any] | None = None) -> None:
    meta = {"schema_version": SCHEMA_VERSION}
    if extra:
        meta.update(extra)
    json.dump({"type": "result", "data": data, "meta": meta}, sys.stdout, ensure_ascii=False, indent=2)
    print()


def emit_error(error: MarketError) -> None:
    json.dump(
        {
            "type": "error",
            "error": {"code": error.code, "message": error.message, "retryable": False},
            "meta": {"schema_version": SCHEMA_VERSION},
        },
        sys.stderr,
        ensure_ascii=False,
        indent=2,
    )
    print(file=sys.stderr)


def print_human(text: str) -> None:
    print(text)


def cmd_list(args: argparse.Namespace) -> int:
    plugins = load_plugins()
    if args.kind:
        plugins = [plugin for plugin in plugins if plugin.get("kind") == args.kind]
    rows = [summary(plugin) for plugin in plugins]
    if args.json:
        emit(rows, {"count": len(rows), "has_more": False, "next_cursor": None})
        return 0
    for row in rows:
        print(f"{row['id']:16} {row['version'] or '-':10} {row['kind'] or '-':8} {row['summary'] or ''}")
    return 0


def cmd_search(args: argparse.Namespace) -> int:
    query = args.query.lower()
    rows = []
    for plugin in load_plugins():
        haystack = " ".join(
            str(value)
            for value in (
                plugin.get("id"),
                plugin.get("name"),
                plugin.get("summary"),
                plugin.get("description"),
                " ".join(plugin.get("keywords", [])),
            )
        ).lower()
        if query in haystack:
            rows.append(summary(plugin))
    if args.json:
        emit(rows, {"count": len(rows), "has_more": False, "next_cursor": None})
        return 0
    for row in rows:
        print(f"{row['id']:16} {row['summary'] or ''}")
    return 0


def cmd_info(args: argparse.Namespace) -> int:
    plugin = find_plugin(args.id)
    data = {key: value for key, value in plugin.items() if not key.startswith("_")}
    if args.json:
        emit(data)
        return 0
    print(f"{data['id']} {data['version']} ({data['kind']})")
    print(data.get("summary", ""))
    interface = data.get("interface", {})
    print(f"help:    {' '.join(interface.get('help_command', []))}")
    print(f"schema:  {' '.join(interface.get('schema_command', [])).replace('{file}', '<file>')}")
    print(f"version: {' '.join(interface.get('version_command', []))}")
    print(f"guide:   {(data.get('usage') or {}).get('guide', '-')}")
    return 0


def cmd_guide(args: argparse.Namespace) -> int:
    plugin = find_plugin(args.id)
    text = guide_text(plugin, args.format)
    if args.json:
        emit({"id": plugin["id"], "format": args.format, "content": text})
    else:
        sys.stdout.write(text)
    return 0


def cmd_plan(args: argparse.Namespace) -> int:
    plugin = find_plugin(args.id)
    data = plan(plugin)
    if args.json:
        emit(data)
    else:
        print(f"plugin:  {data['id']} {data['version']}")
        for distribution in data["distributions"]:
            print(f"install: {json.dumps(distribution, ensure_ascii=False)}")
        print("verify:")
        for command in data["verify"]:
            print(f"  {command}")
        print(f"authority: {data['authority']}")
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    result = validate()
    if args.json:
        emit(result)
    elif result["ok"]:
        print(f"ok: {result['plugins']} plugin(s)")
    else:
        for error in result["errors"]:
            print(f"error: {error}", file=sys.stderr)
    return 0 if result["ok"] else 4


def cmd_schema(_: argparse.Namespace) -> int:
    data = {
        "name": "acli-market",
        "version": TOOL_VERSION,
        "description": "Agent-agnostic client for the Acli plugin marketplace.",
        "schema_version": SCHEMA_VERSION,
        "commands": [
            {"path": ["list"], "summary": "List plugins.", "flags": ["--kind", "--json"]},
            {"path": ["search"], "summary": "Search id, description, and keywords.", "args": ["query"], "flags": ["--json"]},
            {"path": ["info"], "summary": "Show one plugin manifest.", "args": ["id"], "flags": ["--json"]},
            {"path": ["guide"], "summary": "Print the portable usage guide.", "args": ["id"], "flags": ["--format", "--json"]},
            {"path": ["plan"], "summary": "Print the install and verification plan.", "args": ["id"], "flags": ["--json"]},
            {"path": ["validate"], "summary": "Validate the catalog and manifests.", "flags": ["--json"]},
            {"path": ["schema"], "summary": "Emit this manifest.", "flags": []},
        ],
        "exit_codes": EXIT_CODES,
    }
    json.dump(data, sys.stdout, ensure_ascii=False, indent=2)
    print()
    return 0


def build_parser() -> Parser:
    common = Parser(add_help=False)
    common.add_argument(
        "--json",
        action="store_true",
        default=argparse.SUPPRESS,
        help="Emit one machine-readable JSON document.",
    )
    parser = Parser(
        prog="acli-market",
        description="Agent-agnostic client for the Acli plugin marketplace.",
        parents=[common],
    )
    sub = parser.add_subparsers(dest="command", required=True, parser_class=Parser)

    p_list = sub.add_parser("list", parents=[common], help="List plugins.")
    p_list.add_argument("--kind", choices=["cli"])
    p_list.set_defaults(func=cmd_list)

    p_search = sub.add_parser("search", parents=[common], help="Search plugins.")
    p_search.add_argument("query")
    p_search.set_defaults(func=cmd_search)

    p_info = sub.add_parser("info", parents=[common], help="Show one plugin.")
    p_info.add_argument("id")
    p_info.set_defaults(func=cmd_info)

    p_guide = sub.add_parser("guide", parents=[common], help="Print a usage guide.")
    p_guide.add_argument("id")
    p_guide.add_argument("--format", choices=["markdown", "skill"], default="markdown")
    p_guide.set_defaults(func=cmd_guide)

    p_plan = sub.add_parser("plan", parents=[common], help="Print the install plan.")
    p_plan.add_argument("id")
    p_plan.set_defaults(func=cmd_plan)

    p_validate = sub.add_parser("validate", parents=[common], help="Validate the catalog.")
    p_validate.set_defaults(func=cmd_validate)

    p_schema = sub.add_parser("schema", parents=[common], help="Emit the tool manifest.")
    p_schema.set_defaults(func=cmd_schema)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
        if not hasattr(args, "json"):
            args.json = False
        return args.func(args)
    except MarketError as error:
        if "--json" in (argv if argv is not None else sys.argv[1:]):
            emit_error(error)
        else:
            print(f"error [{error.code}]: {error.message}", file=sys.stderr)
        return error.exit_code
    except BrokenPipeError:
        return 0
    except Exception as error:  # noqa: BLE001 - top-level boundary
        wrapped = MarketError("internal_error", str(error), 1)
        if "--json" in (argv if argv is not None else sys.argv[1:]):
            emit_error(wrapped)
        else:
            print(f"error [{wrapped.code}]: {wrapped.message}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
