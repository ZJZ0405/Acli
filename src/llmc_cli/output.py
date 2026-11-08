"""Structured output, logging, and stdout hygiene for agent callers."""

from __future__ import annotations

import contextlib
import dataclasses
import json
import sys
import time
import warnings
from enum import Enum
from pathlib import Path
from typing import Any

from llmc_cli import __version__


def jsonable(value: Any) -> Any:
    """Best-effort conversion to JSON-serializable Python values."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Enum):
        return jsonable(value.value)
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {f.name: jsonable(getattr(value, f.name)) for f in dataclasses.fields(value)}
    if isinstance(value, dict):
        return {str(k): jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [jsonable(v) for v in value]
    for attr in ("model_dump", "dict"):
        method = getattr(value, attr, None)
        if callable(method):
            try:
                return jsonable(method(mode="json"))
            except TypeError:
                try:
                    return jsonable(method())
                except Exception:
                    pass
            except Exception:
                pass
    if hasattr(value, "tolist"):
        try:
            return jsonable(value.tolist())
        except Exception:
            pass
    return str(value)


class Reporter:
    """Owns the stdout contract and the human-readable presentation."""

    def __init__(self, json_mode: bool = False, debug: bool = False):
        self.json_mode = json_mode
        self.debug = debug
        self._warnings: list[str] = []

    def warn(self, message: str) -> None:
        self._warnings.append(str(message))

    def _envelope(self, command: str, started: float) -> dict:
        return {
            "llmc_version": __version__,
            "command": command,
            "duration_s": round(time.monotonic() - started, 3),
        }

    def success(
        self,
        command: str,
        data: Any,
        started: float,
        *,
        human: str | None = None,
        warnings_list: list[str] | None = None,
    ) -> int:
        all_warnings = list(dict.fromkeys(self._warnings + list(warnings_list or [])))
        if self.json_mode:
            payload = self._envelope(command, started)
            payload.update({"ok": True, "data": jsonable(data), "warnings": all_warnings})
            print(json.dumps(payload, ensure_ascii=False))
        else:
            text = human if human is not None else json.dumps(jsonable(data), indent=2, ensure_ascii=False)
            if text:
                print(text)
            for message in all_warnings:
                print(f"warning: {message}", file=sys.stderr)
        return 0

    def failure(self, command: str, error: Exception, started: float) -> int:
        from llmc_cli.errors import LLMCError

        if isinstance(error, LLMCError):
            payload = error.to_dict()
            exit_code = error.exit_code
        else:
            payload = {"code": "RUNTIME", "message": str(error), "type": type(error).__name__}
            exit_code = 4
        if self.debug:
            import traceback

            traceback.print_exc(file=sys.stderr)
        if self.json_mode:
            envelope = self._envelope(command, started)
            envelope.update({"ok": False, "error": jsonable(payload)})
            print(json.dumps(envelope, ensure_ascii=False))
        else:
            print(f"error [{payload['code']}]: {payload['message']}", file=sys.stderr)
            if payload.get("hint"):
                print(f"hint: {payload['hint']}", file=sys.stderr)
        return exit_code


_LOG_SETTINGS: dict[str, Any] | None = None


def configure_logging(
    *,
    level: str = "INFO",
    log_file: str | None = None,
    progress: str = "auto",
) -> None:
    """Route llm-compressor's loguru logs to stderr, never stdout."""
    global _LOG_SETTINGS
    _LOG_SETTINGS = {"level": level, "log_file": log_file, "progress": progress}
    _apply_logging()


def refresh_logging() -> None:
    """Re-apply our sinks after llm-compressor imports and resets loguru."""
    if _LOG_SETTINGS is not None:
        _apply_logging()


def _apply_logging() -> None:
    try:
        from loguru import logger
    except Exception:
        return

    settings = _LOG_SETTINGS or {"level": "INFO", "log_file": None, "progress": "auto"}
    level = settings["level"]
    log_file = settings["log_file"]
    progress = settings["progress"]

    logger.remove()
    if progress != "off":
        if progress == "json":
            logger.add(sys.stderr, level=level, serialize=True, backtrace=False, diagnose=False)
        else:
            logger.add(
                sys.stderr,
                level=level,
                colorize=sys.stderr.isatty(),
                format="<level>{level: <8}</level> {message}",
                backtrace=False,
                diagnose=False,
            )
    elif level.upper() in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"):
        logger.add(sys.stderr, level="ERROR", format="<level>{level: <8}</level> {message}")

    if log_file:
        Path(log_file).expanduser().parent.mkdir(parents=True, exist_ok=True)
        logger.add(log_file, level="DEBUG", serialize=False, enqueue=True)


@contextlib.contextmanager
def imported_quietly():
    """Import llm-compressor without letting its default loguru sink touch stdout."""
    with contextlib.redirect_stdout(sys.stderr):
        yield
    refresh_logging()


@contextlib.contextmanager
def keep_stdout_clean(enabled: bool):
    """Redirect library prints to stderr while the CLI owns stdout."""
    if not enabled:
        yield
        return
    with contextlib.redirect_stdout(sys.stderr):
        yield


@contextlib.contextmanager
def capture_warnings():
    """Collect Python warnings so they can travel with the JSON result."""
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("default")
        yield caught
