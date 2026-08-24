"""Versioned, atomic runtime state for exactly one cast session."""

from __future__ import annotations

from datetime import UTC, datetime
import fcntl
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Mapping

from .bounds import BoundError, MAX_STATE_BYTES, bounded_text, read_bounded_regular_file, validate_json_budget


SCHEMA_VERSION = 1
IDLE_PHASE = "idle"
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


class SessionLock:
    """An advisory, non-blocking per-user lock for the session supervisor."""

    def __init__(self, environ: Mapping[str, str] | None = None) -> None:
        self._environ = environ
        self._handle: object | None = None

    @property
    def acquired(self) -> bool:
        return self._handle is not None

    def acquire(self) -> None:
        if self._handle is not None:
            raise StateError("session lock is already held by this controller")
        directory = runtime_directory(self._environ)
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        lock_path = directory / "session.lock"
        handle = lock_path.open("a+", encoding="utf-8")
        try:
            os.chmod(lock_path, 0o600)
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            handle.close()
            raise StateError("another omarchy-cast session is already active") from exc
        except Exception:
            handle.close()
            raise
        self._handle = handle

    def release(self) -> None:
        if self._handle is None:
            return
        handle = self._handle
        self._handle = None
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()

    def __enter__(self) -> "SessionLock":
        self.acquire()
        return self

    def __exit__(self, exception_type: object, exception: object, traceback: object) -> None:
        self.release()


def session_lock_is_held(environ: Mapping[str, str] | None = None) -> bool:
    """Report whether a live supervisor owns the lock without taking it."""
    directory = runtime_directory(environ)
    lock_path = directory / "session.lock"
    if not lock_path.is_file():
        return False
    try:
        with lock_path.open("r", encoding="utf-8") as handle:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return True
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                return False
    except OSError:
        return False


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
    if state.get("schemaVersion") != SCHEMA_VERSION:
        raise StateError("unsupported state schema")
    phase = state.get("phase")
    if phase not in PHASES:
        raise StateError("invalid session phase")
    session_id = state.get("sessionId")
    if phase == IDLE_PHASE and session_id is not None:
        raise StateError("idle state may not own a session")
    if phase != IDLE_PHASE and (not isinstance(session_id, str) or not session_id):
        raise StateError("active state requires a session id")
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
    path = state_path(environ)
    try:
        encoded = read_bounded_regular_file(
            path,
            limit=MAX_STATE_BYTES,
            require_owner=True,
            require_private=True,
        )
    except FileNotFoundError:
        return idle_state()
    except (OSError, BoundError) as exc:
        raise StateError("cannot read runtime state: " + bounded_text(str(exc), limit=240)) from exc
    try:
        loaded = json.loads(encoded.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise StateError("cannot read runtime state: invalid JSON") from exc
    if not isinstance(loaded, dict):
        raise StateError("runtime state must be an object")
    return validate_state(loaded)


def write_state(state: Mapping[str, Any], environ: Mapping[str, str] | None = None) -> Path:
    checked = validate_state(state)
    directory = runtime_directory(environ)
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = directory / "state.json"
    descriptor, temporary_name = tempfile.mkstemp(prefix=".state-", dir=directory)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(checked, handle, sort_keys=True, separators=(",", ":"))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
        directory_descriptor = os.open(directory, os.O_DIRECTORY)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    finally:
        temporary.unlink(missing_ok=True)
    return path
