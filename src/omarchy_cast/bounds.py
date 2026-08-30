"""Shared size and shape boundaries for local runtime data."""

from __future__ import annotations

import math
import os
import stat
from pathlib import Path
from typing import Any


MAX_COMMAND_OUTPUT_BYTES = 65_536
MAX_STATE_BYTES = 65_536
MAX_TELEMETRY_BYTES = 262_144
MAX_UI_RESPONSE_BYTES = 262_144
MAX_RUNTIME_TEXT_CHARS = 1_024


class BoundError(ValueError):
    """Runtime input exceeded an explicit local trust boundary."""


def bounded_text(value: object, *, limit: int = MAX_RUNTIME_TEXT_CHARS, fallback: str = "") -> str:
    """Return one display-safe string with control characters normalized."""
    text = value if isinstance(value, str) else fallback
    oversized = len(text) > limit
    # Normalize only the prefix we can return. This prevents a very large
    # diagnostic or wireless label from being copied in full before truncation.
    prefix = text[:limit]
    normalized = "".join(character if character in "\n\t" or 32 <= ord(character) != 127 else "�" for character in prefix)
    if not oversized:
        return normalized
    return normalized[: max(0, limit - 1)] + "…"


def read_bounded_regular_file(
    path: Path,
    *,
    limit: int,
    require_owner: bool = False,
    require_private: bool = False,
    require_single_link: bool = False,
    directory_fd: int | None = None,
) -> bytes:
    """Read a regular file through one descriptor, never beyond ``limit``."""
    # A FIFO opened read-only can wait forever before descriptor validation.
    # Nonblocking mode is inert for regular files and lets us reject every
    # other file type through fstat without first joining its I/O protocol.
    flags = os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NONBLOCK", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags, dir_fd=directory_fd)
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise BoundError("runtime data is not a regular file")
        if require_owner and metadata.st_uid != os.getuid():
            raise BoundError("runtime data is not owned by the current user")
        if require_private and metadata.st_mode & 0o077:
            raise BoundError("runtime data permissions are too broad")
        if require_single_link and metadata.st_nlink != 1:
            raise BoundError("runtime data has an unsafe link count")
        if metadata.st_size > limit:
            raise BoundError(f"runtime data exceeds the {limit}-byte limit")
        chunks: list[bytes] = []
        remaining = limit + 1
        while remaining:
            chunk = os.read(descriptor, min(65_536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        payload = b"".join(chunks)
        if len(payload) > limit:
            raise BoundError(f"runtime data exceeds the {limit}-byte limit")
        return payload
    finally:
        os.close(descriptor)


def validate_json_budget(
    value: Any,
    *,
    max_depth: int = 12,
    max_nodes: int = 2_048,
    max_collection_items: int = 128,
    max_string_chars: int = 8_192,
) -> None:
    """Reject JSON-compatible values with excessive depth or fan-out."""
    remaining = max_nodes

    def visit(item: Any, depth: int) -> None:
        nonlocal remaining
        remaining -= 1
        if remaining < 0:
            raise BoundError("runtime data contains too many values")
        if depth > max_depth:
            raise BoundError("runtime data is nested too deeply")
        if isinstance(item, str):
            if len(item) > max_string_chars:
                raise BoundError("runtime data contains an oversized string")
            return
        if isinstance(item, dict):
            if len(item) > max_collection_items:
                raise BoundError("runtime data object contains too many fields")
            for key, child in item.items():
                if not isinstance(key, str) or len(key) > 128:
                    raise BoundError("runtime data contains an invalid field name")
                visit(child, depth + 1)
            return
        if isinstance(item, list):
            if len(item) > max_collection_items:
                raise BoundError("runtime data array contains too many items")
            for child in item:
                visit(child, depth + 1)
            return
        if isinstance(item, float) and not math.isfinite(item):
            raise BoundError("runtime data contains a non-finite number")
        if item is not None and not isinstance(item, (bool, int, float)):
            raise BoundError("runtime data contains an unsupported value")

    visit(value, 0)
