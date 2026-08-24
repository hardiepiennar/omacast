"""Small, shell-free subprocess abstraction used by host discovery."""

from __future__ import annotations

from dataclasses import dataclass
import subprocess
from typing import Protocol, Sequence


@dataclass(frozen=True)
class CommandResult:
    args: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str


class Runner(Protocol):
    def __call__(self, args: Sequence[str], *, timeout: float = 5.0) -> CommandResult: ...


def run_command(args: Sequence[str], *, timeout: float = 5.0) -> CommandResult:
    """Run a fixed argument vector without a shell or inherited stdin."""
    argv = tuple(str(arg) for arg in args)
    try:
        completed = subprocess.run(
            argv,
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError as exc:
        return CommandResult(argv, 127, "", str(exc))
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout if isinstance(exc.stdout, str) else ""
        stderr = exc.stderr if isinstance(exc.stderr, str) else ""
        return CommandResult(argv, 124, stdout, stderr or "command timed out")
    return CommandResult(argv, completed.returncode, completed.stdout, completed.stderr)
