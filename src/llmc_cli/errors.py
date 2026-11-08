"""Typed errors and stable process exit codes."""

from __future__ import annotations

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_INPUT = 3
EXIT_RUNTIME = 4
EXIT_ENVIRONMENT = 5
EXIT_INTERRUPTED = 130


class LLMCError(Exception):
    """An error that is safe to show to a command-line caller."""

    code = "RUNTIME"
    exit_code = EXIT_RUNTIME

    def __init__(self, message: str, *, hint: str | None = None):
        super().__init__(message)
        self.message = message
        self.hint = hint

    def to_dict(self) -> dict:
        payload = {"code": self.code, "message": self.message}
        if self.hint:
            payload["hint"] = self.hint
        return payload


class UsageError(LLMCError):
    code = "USAGE"
    exit_code = EXIT_USAGE


class InputError(LLMCError):
    code = "INPUT"
    exit_code = EXIT_INPUT


class EnvironmentError_(LLMCError):
    code = "ENVIRONMENT"
    exit_code = EXIT_ENVIRONMENT


class RuntimeFailure(LLMCError):
    code = "RUNTIME"
    exit_code = EXIT_RUNTIME
