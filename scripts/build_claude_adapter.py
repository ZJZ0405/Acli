#!/usr/bin/env python3
"""Generate the Claude Code adapter from the agent-agnostic marketplace.

The marketplace index and plugin manifests are the source of truth. Everything
under .claude-plugin/ and plugins/<id>/ is generated; do not edit it by hand.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import acli_market as market  # type: ignore[import-not-found]  # noqa: E402

ROOT = market.ROOT
EXTRAS_PATH = ROOT / "adapters" / "claude-code" / "extras.json"


def dump(data: object) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2) + "\n"


def load_extras() -> dict:
    with EXTRAS_PATH.open(encoding="utf-8") as handle:
        return json.load(handle)


def claude_plugin_manifest(plugin: dict) -> dict:
    manifest = {
        "$schema": "https://json.schemastore.org/claude-code-plugin-manifest.json",
        "name": plugin["id"],
        "version": plugin["version"],
        "description": plugin.get("summary", plugin.get("description", "")),
    }
    if plugin.get("publisher"):
        manifest["author"] = plugin["publisher"]
    for key in ("homepage", "repository", "license"):
        if plugin.get(key):
            manifest[key] = plugin[key]
    if plugin.get("keywords"):
        manifest["keywords"] = plugin["keywords"]
    return manifest


def claude_marketplace_entry(plugin: dict) -> dict:
    entry = {
        "name": plugin["id"],
        "source": f"./plugins/{plugin['id']}",
        "description": plugin.get("summary", plugin.get("description", "")),
    }
    if plugin.get("category"):
        entry["category"] = plugin["category"]
    if plugin.get("keywords"):
        entry["keywords"] = plugin["keywords"]
    return entry


def claude_skill(plugin: dict) -> str:
    text = market.guide_text(plugin, "skill")
    marker = f"<!-- Generated from marketplace/plugins/{plugin['id']}/usage/{Path(plugin['usage']['guide']).name}; edit the source guide instead. -->\n\n"
    if text.startswith("---\n"):
        end = text.find("\n---", 4)
        if end != -1:
            split = end + len("\n---")
            if split < len(text) and text[split] == "\n":
                split += 1
            return text[:split] + "\n" + marker + text[split:].lstrip("\n")
    return marker + text


def generated_files() -> dict[Path, str]:
    extras = load_extras()
    marketplace = extras["marketplace"]
    plugins = market.load_plugins()

    files: dict[Path, str] = {}
    entries = [claude_marketplace_entry(plugin) for plugin in plugins]
    entries.extend(extras.get("plugins", []))
    files[ROOT / ".claude-plugin" / "marketplace.json"] = dump(
        {
            "$schema": "https://json.schemastore.org/claude-code-marketplace.json",
            "name": marketplace["name"],
            "version": marketplace.get("version"),
            "description": marketplace.get("description"),
            "owner": marketplace["owner"],
            "plugins": entries,
        }
    )

    for manifest in extras.get("manifests", {}).values():
        files[ROOT / ".claude-plugin" / "plugin.json"] = dump(manifest)

    for plugin in plugins:
        plugin_dir = ROOT / "plugins" / plugin["id"]
        files[plugin_dir / ".claude-plugin" / "plugin.json"] = dump(claude_plugin_manifest(plugin))
        if (plugin.get("usage") or {}).get("mode") in ("guide", "both"):
            files[plugin_dir / "skills" / plugin["id"] / "SKILL.md"] = claude_skill(plugin)

    return files


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Fail if generated files differ.")
    args = parser.parse_args(argv)

    files = generated_files()
    drift = []
    for path, content in sorted(files.items()):
        if args.check:
            current = path.read_text(encoding="utf-8") if path.is_file() else None
            if current != content:
                drift.append(path)
                print(f"drift: {path.relative_to(ROOT)}", file=sys.stderr)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            print(f"wrote: {path.relative_to(ROOT)}")

    if args.check:
        if drift:
            print(f"{len(drift)} generated file(s) differ; run scripts/build_claude_adapter.py", file=sys.stderr)
            return 1
        print("claude adapter: up to date")
    return 0


if __name__ == "__main__":
    sys.exit(main())
