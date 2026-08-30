"""Small, shell-free subprocess abstraction used by host discovery."""

from __future__ import annotations

from dataclasses import dataclass
import os
import selectors
import signal
import subprocess
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

    assert process.stdout is not None and process.stderr is not None
    streams = {process.stdout: ("stdout", bytearray()), process.stderr: ("stderr", bytearray())}
    selector = selectors.DefaultSelector()
    for stream in streams:
        os.set_blocking(stream.fileno(), False)
        selector.register(stream, selectors.EVENT_READ)
    deadline = time.monotonic() + timeout
    timed_out = output_limited = False
    try:
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                timed_out = True
                break
            events = selector.select(min(remaining, 0.05))
            for key, _mask in events:
                stream = key.fileobj
                try:
                    chunk = os.read(stream.fileno(), 8192)
                except BlockingIOError:
                    continue
                if not chunk:
                    selector.unregister(stream)
                    continue
                _name, buffer = streams[stream]
                room = MAX_COMMAND_OUTPUT_BYTES + 1 - len(buffer)
                if room > 0:
                    buffer.extend(chunk[:room])
                if len(buffer) > MAX_COMMAND_OUTPUT_BYTES:
                    output_limited = True
                    break
            if output_limited:
                break
        if not timed_out and not output_limited:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                timed_out = True
            else:
                try:
                    process.wait(timeout=remaining)
                except subprocess.TimeoutExpired:
                    timed_out = True
    finally:
        selector.close()

    if timed_out or output_limited or process.poll() is None:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    try:
        returncode = process.wait(timeout=1.0)
    except subprocess.TimeoutExpired:
        returncode = 124
        timed_out = True
    for stream in streams:
        stream.close()
    assert returncode is not None

    if timed_out:
        return CommandResult(argv, 124, "", "command timed out")
    if output_limited:
        return CommandResult(argv, 125, "", f"command output exceeded {MAX_COMMAND_OUTPUT_BYTES} bytes")
    return CommandResult(
        argv,
        returncode,
        bytes(streams[process.stdout][1]).decode("utf-8", errors="replace"),
        bytes(streams[process.stderr][1]).decode("utf-8", errors="replace"),
    )
