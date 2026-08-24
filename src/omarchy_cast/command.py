"""Small, shell-free subprocess abstraction used by host discovery."""

from __future__ import annotations

from dataclasses import dataclass
import os
import signal
import subprocess
import threading
import time
from typing import Mapping, Protocol, Sequence

from .bounds import MAX_COMMAND_OUTPUT_BYTES


@dataclass(frozen=True)
class CommandResult:
    args: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str


class Runner(Protocol):
    def __call__(self, args: Sequence[str], *, timeout: float = 5.0) -> CommandResult: ...


def run_command(
    args: Sequence[str],
    *,
    timeout: float = 5.0,
    env: Mapping[str, str] | None = None,
) -> CommandResult:
    """Run a fixed argument vector with independently bounded output pipes."""
    argv = tuple(str(arg) for arg in args)
    try:
        process = subprocess.Popen(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            start_new_session=True,
        )
    except FileNotFoundError as exc:
        return CommandResult(argv, 127, "", str(exc))
    except OSError as exc:
        return CommandResult(argv, 126, "", str(exc))

    buffers = {"stdout": bytearray(), "stderr": bytearray()}
    exceeded = threading.Event()

    def drain(name: str, stream: object) -> None:
        try:
            while True:
                chunk = stream.read(8192)  # type: ignore[attr-defined]
                if not chunk:
                    break
                buffer = buffers[name]
                room = MAX_COMMAND_OUTPUT_BYTES + 1 - len(buffer)
                if room > 0:
                    buffer.extend(chunk[:room])
                if len(buffer) > MAX_COMMAND_OUTPUT_BYTES:
                    exceeded.set()
        finally:
            stream.close()  # type: ignore[attr-defined]

    assert process.stdout is not None and process.stderr is not None
    readers = [
        threading.Thread(target=drain, args=("stdout", process.stdout), daemon=True),
        threading.Thread(target=drain, args=("stderr", process.stderr), daemon=True),
    ]
    for reader in readers:
        reader.start()
    deadline = time.monotonic() + timeout
    timed_out = output_limited = False
    while process.poll() is None:
        if exceeded.is_set():
            output_limited = True
            break
        if time.monotonic() >= deadline:
            timed_out = True
            break
        try:
            process.wait(timeout=min(0.05, max(0.001, deadline - time.monotonic())))
        except subprocess.TimeoutExpired:
            pass
    if timed_out or output_limited:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        returncode = process.wait()
    else:
        returncode = process.returncode
    for reader in readers:
        reader.join()
    assert returncode is not None

    if timed_out:
        return CommandResult(argv, 124, "", "command timed out")
    if output_limited or exceeded.is_set():
        return CommandResult(argv, 125, "", f"command output exceeded {MAX_COMMAND_OUTPUT_BYTES} bytes")
    return CommandResult(
        argv,
        returncode,
        bytes(buffers["stdout"]).decode("utf-8", errors="replace"),
        bytes(buffers["stderr"]).decode("utf-8", errors="replace"),
    )
