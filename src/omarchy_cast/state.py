"""Versioned, atomic runtime state for exactly one cast session."""

from __future__ import annotations

from datetime import UTC, datetime
import fcntl
import json
import os
from pathlib import Path
import re
import stat
from typing import Any, Mapping
from uuid import uuid4

from .bounds import BoundError, MAX_STATE_BYTES, bounded_text, read_bounded_regular_file, validate_json_budget


SCHEMA_VERSION = 1
IDLE_PHASE = "idle"
_SESSION_ID = re.compile(r"^[a-f0-9]{32}$")
PHASES = frozenset({"idle", "checking", "discovering", "preparing", "connecting", "streaming", "stopping", "error", "recovering"})
TRANSITIONS = {
    "idle": {"checking", "discovering", "recovering"},
    "checking": {"idle", "discovering", "stopping", "error"},
    "discovering": {"idle", "preparing", "error", "stopping"},
    "preparing": {"connecting", "error", "stopping"},
    "connecting": {"streaming", "error", "stopping"},
    "streaming": {"stopping", "error"},
    "stopping": {"idle", "error"},
    "error": {"idle", "recovering"},
    "recovering": {"idle", "error"},
}


class StateError(ValueError):
    pass


def _open_session_runtime_descriptor(environ: Mapping[str, str] | None, *, create: bool) -> int:
    """Open the private runtime directory without following either component."""
    environ = os.environ if environ is None else environ
    runtime = environ.get("XDG_RUNTIME_DIR")
    if not runtime:
        raise StateError("XDG_RUNTIME_DIR is required for cast session state")
    flags = os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_DIRECTORY", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    if create:
        try:
            Path(runtime).mkdir(mode=0o700, parents=True, exist_ok=True)
        except OSError as exc:
            raise StateError("session runtime directory is unavailable or unsafe") from exc
    try:
        runtime_descriptor = os.open(runtime, flags)
    except FileNotFoundError:
        raise
    except OSError as exc:
        raise StateError("session runtime directory is unavailable or unsafe") from exc
    try:
        metadata = os.fstat(runtime_descriptor)
        if not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != os.getuid() or metadata.st_mode & 0o077:
            raise StateError("session runtime parent is unsafe")
        if create:
            try:
                os.mkdir("omarchy-cast", mode=0o700, dir_fd=runtime_descriptor)
            except FileExistsError:
                pass
        try:
            directory_descriptor = os.open("omarchy-cast", flags, dir_fd=runtime_descriptor)
        except FileNotFoundError:
            raise
        except OSError as exc:
            raise StateError("session runtime directory is unavailable or unsafe") from exc
    finally:
        os.close(runtime_descriptor)
    try:
        metadata = os.fstat(directory_descriptor)
        if not stat.S_ISDIR(metadata.st_mode):
            raise StateError("session runtime path is not a directory")
        if metadata.st_uid != os.getuid():
            raise StateError("session runtime directory is not owned by the current user")
        if stat.S_IMODE(metadata.st_mode) != 0o700:
            raise StateError("session runtime directory permissions are unsafe")
        return directory_descriptor
    except Exception:
        os.close(directory_descriptor)
        raise


def _open_session_lock_descriptor(directory_descriptor: int, *, create: bool) -> int:
    """Open and validate the lock inode without following its pathname."""
    flags = os.O_RDWR | os.O_CLOEXEC | getattr(os, "O_NONBLOCK", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        created = False
        if create:
            try:
                descriptor = os.open(
                    "session.lock", flags | os.O_CREAT | os.O_EXCL,
                    0o600, dir_fd=directory_descriptor,
                )
                created = True
            except FileExistsError:
                descriptor = os.open("session.lock", flags, dir_fd=directory_descriptor)
        else:
            descriptor = os.open("session.lock", flags, dir_fd=directory_descriptor)
    except OSError as exc:
        raise StateError("session lock is unavailable or unsafe") from exc
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise StateError("session lock is not a regular file")
        if metadata.st_uid != os.getuid():
            raise StateError("session lock is not owned by the current user")
        if metadata.st_nlink != 1:
            raise StateError("session lock has an unsafe link count")
        if created:
            os.fchmod(descriptor, 0o600)
            metadata = os.fstat(descriptor)
        if stat.S_IMODE(metadata.st_mode) != 0o600:
            raise StateError("session lock permissions are unsafe")
        return descriptor
    except Exception:
        os.close(descriptor)
        raise


class SessionLock:
    """An advisory, non-blocking per-user lock for the session supervisor."""

    def __init__(self, environ: Mapping[str, str] | None = None) -> None:
        self._environ = environ
        self._descriptor: int | None = None

    @property
    def acquired(self) -> bool:
        return self._descriptor is not None

    def acquire(self) -> None:
        if self._descriptor is not None:
            raise StateError("session lock is already held by this controller")
        directory_descriptor = _open_session_runtime_descriptor(self._environ, create=True)
        try:
            descriptor = _open_session_lock_descriptor(directory_descriptor, create=True)
        finally:
            os.close(directory_descriptor)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            os.close(descriptor)
            raise StateError("another omarchy-cast session is already active") from exc
        except Exception:
            os.close(descriptor)
            raise
        self._descriptor = descriptor

    def release(self) -> None:
        if self._descriptor is None:
            return
        descriptor = self._descriptor
        self._descriptor = None
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)

    def __enter__(self) -> "SessionLock":
        self.acquire()
        return self

    def __exit__(self, exception_type: object, exception: object, traceback: object) -> None:
        self.release()


def session_lock_is_held(environ: Mapping[str, str] | None = None) -> bool:
    """Report whether a live supervisor owns the lock without taking it."""
    try:
        directory_descriptor = _open_session_runtime_descriptor(environ, create=False)
        try:
            descriptor = _open_session_lock_descriptor(directory_descriptor, create=False)
        finally:
            os.close(directory_descriptor)
    except (OSError, StateError):
        return False
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        else:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            return False
    finally:
        os.close(descriptor)


def runtime_directory(environ: Mapping[str, str] | None = None) -> Path:
    environ = os.environ if environ is None else environ
    runtime = environ.get("XDG_RUNTIME_DIR")
    if not runtime:
        raise StateError("XDG_RUNTIME_DIR is required for cast session state")
    return Path(runtime) / "omarchy-cast"


def idle_state() -> dict[str, object]:
    return {"schemaVersion": SCHEMA_VERSION, "phase": IDLE_PHASE, "sessionId": None, "updatedAt": None}


def validate_state(state: Mapping[str, Any]) -> dict[str, object]:
    try:
        validate_json_budget(state)
    except BoundError as exc:
        raise StateError(str(exc)) from exc
    if type(state.get("schemaVersion")) is not int or state.get("schemaVersion") != SCHEMA_VERSION:
        raise StateError("unsupported state schema")
    phase = state.get("phase")
    if phase not in PHASES:
        raise StateError("invalid session phase")
    session_id = state.get("sessionId")
    if phase == IDLE_PHASE and session_id is not None:
        raise StateError("idle state may not own a session")
    if phase != IDLE_PHASE and (
        not isinstance(session_id, str) or not _SESSION_ID.fullmatch(session_id)
    ):
        raise StateError("active state requires a controller-issued session id")
    updated_at = state.get("updatedAt")
    if updated_at is not None and not isinstance(updated_at, str):
        raise StateError("updatedAt must be an ISO timestamp or null")
    started_at = state.get("startedAt")
    if started_at is not None and not isinstance(started_at, str):
        raise StateError("startedAt must be an ISO timestamp or null")
    return dict(state)


def transition(state: Mapping[str, Any], phase: str, **updates: object) -> dict[str, object]:
    current = validate_state(state)
    if phase not in PHASES:
        raise StateError("invalid destination phase")
    if phase != current["phase"] and phase not in TRANSITIONS[str(current["phase"])]:
        raise StateError(f"illegal transition: {current['phase']} -> {phase}")
    now = datetime.now(UTC).isoformat()
    if phase == IDLE_PHASE:
        next_state = {**idle_state(), "updatedAt": now}
    else:
        next_state = {**current, **updates, "phase": phase, "updatedAt": now}
        if current["phase"] == IDLE_PHASE and "startedAt" not in next_state:
            next_state["startedAt"] = now
    return validate_state(next_state)


def state_path(environ: Mapping[str, str] | None = None) -> Path:
    return runtime_directory(environ) / "state.json"


def read_state(environ: Mapping[str, str] | None = None) -> dict[str, object]:
    try:
        directory_descriptor = _open_session_runtime_descriptor(environ, create=False)
        try:
            encoded = read_bounded_regular_file(
                Path("state.json"),
                limit=MAX_STATE_BYTES,
                require_owner=True,
                require_private=True,
                require_single_link=True,
                directory_fd=directory_descriptor,
            )
        finally:
            os.close(directory_descriptor)
    except FileNotFoundError:
        return idle_state()
    except (OSError, BoundError, StateError) as exc:
        raise StateError("cannot read runtime state: " + bounded_text(str(exc), limit=240)) from exc
    try:
        loaded = json.loads(encoded.decode("utf-8"))
        validate_json_budget(loaded)
    except (UnicodeDecodeError, json.JSONDecodeError, BoundError, RecursionError) as exc:
        raise StateError("cannot read runtime state: invalid JSON") from exc
    if not isinstance(loaded, dict):
        raise StateError("runtime state must be an object")
    return validate_state(loaded)


def write_state(state: Mapping[str, Any], environ: Mapping[str, str] | None = None) -> Path:
    checked = validate_state(state)
    encoded = (json.dumps(checked, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    if len(encoded) > MAX_STATE_BYTES:
        raise StateError(f"runtime state exceeds the {MAX_STATE_BYTES}-byte limit")
    directory_descriptor = _open_session_runtime_descriptor(environ, create=True)
    temporary_name = f".state-{uuid4().hex}.tmp"
    descriptor = -1
    try:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC
        flags |= getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
        descriptor = os.open(temporary_name, flags, 0o600, dir_fd=directory_descriptor)
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid() or metadata.st_nlink != 1:
            raise StateError("runtime state temporary file is unsafe")
        os.fchmod(descriptor, 0o600)
        view = memoryview(encoded)
        while view:
            written = os.write(descriptor, view)
            view = view[written:]
        os.fsync(descriptor)
        os.replace(
            temporary_name,
            "state.json",
            src_dir_fd=directory_descriptor,
            dst_dir_fd=directory_descriptor,
        )
        os.fsync(directory_descriptor)
    except OSError as exc:
        raise StateError("cannot write runtime state safely") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            os.unlink(temporary_name, dir_fd=directory_descriptor)
        except OSError:
            pass
        os.close(directory_descriptor)
    return state_path(environ)
