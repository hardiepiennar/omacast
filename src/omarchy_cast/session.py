"""A supervised simulation path for testing the cast lifecycle without hardware."""

from __future__ import annotations

from datetime import UTC, datetime
import json
import os
from pathlib import Path
import re
import time
from typing import Any, Mapping
from uuid import uuid4

from .state import SessionLock, StateError, idle_state, read_state, runtime_directory, transition, write_state
from .telemetry import cleanup_live_telemetry
from .transport import TransportAdapter, TransportError, TransportResult, result_payload, validate_transport_plan


PROFILES = frozenset({"safe"})
MODES = frozenset({"mirror"})
SOURCES = frozenset({"display"})
_PEER_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
MAX_SESSION_LOGS = 50


class SessionError(RuntimeError):
    pass


def _now() -> str:
    return datetime.now(UTC).isoformat()


def validate_request(*, peer: str, mode: str, profile: str, source: str = "display") -> dict[str, str]:
    if not _PEER_ID.fullmatch(peer):
        raise SessionError("peer must be a stable receiver identifier, not a command or display name")
    if mode not in MODES:
        raise SessionError(f"unsupported cast mode: {mode}")
    if profile not in PROFILES:
        raise SessionError(f"unsupported quality profile: {profile}")
    if source not in SOURCES:
        raise SessionError(f"unsupported capture source: {source}")
    return {"peer": peer, "mode": mode, "profile": profile, "source": source}


def _state_home(environ: Mapping[str, str] | None = None) -> Path:
    environ = os.environ if environ is None else environ
    configured = environ.get("XDG_STATE_HOME")
    if configured:
        return Path(configured) / "omarchy-cast"
    return Path.home() / ".local" / "state" / "omarchy-cast"


def event_log_path(session_id: str, environ: Mapping[str, str] | None = None) -> Path:
    return _state_home(environ) / "sessions" / f"{session_id}.jsonl"


def _prune_session_logs(directory: Path, current: Path) -> None:
    """Keep bounded private history without ever following links."""
    try:
        paths = sorted(
            (
                path for path in directory.glob("*.jsonl")
                if path != current and path.is_file() and not path.is_symlink()
                and re.fullmatch(r"[a-f0-9]{32}\.jsonl", path.name)
            ),
            key=lambda path: path.stat().st_mtime_ns,
            reverse=True,
        )
        for path in paths[MAX_SESSION_LOGS - 1:]:
            path.unlink(missing_ok=True)
            (directory.parent / "telemetry" / path.name).unlink(missing_ok=True)
    except OSError:
        # Retention housekeeping must never interrupt a cast lifecycle.
        return


def append_event(session_id: str, event: str, environ: Mapping[str, str] | None = None, **fields: object) -> Path:
    path = event_log_path(session_id, environ)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    payload = {"schemaVersion": 1, "timestamp": _now(), "event": event, **fields}
    with path.open("a", encoding="utf-8") as handle:
        os.chmod(path, 0o600)
        handle.write(json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    if event == "session-started":
        _prune_session_logs(path.parent, path)
    return path


def _read_events(path: Path) -> list[dict[str, object]]:
    if not path.is_file() or path.stat().st_size > 1_048_576:
        raise SessionError("session event log is unavailable or exceeds the safe size limit")
    events: list[dict[str, object]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            raise SessionError("session event log contains invalid JSON") from exc
        if not isinstance(event, dict) or event.get("schemaVersion") != 1 or not isinstance(event.get("event"), str):
            raise SessionError("session event log has an unsupported event")
        events.append(event)
    return events


def session_history(*, limit: int = 10, environ: Mapping[str, str] | None = None) -> dict[str, object]:
    """Return bounded summaries only; detailed events require an explicit id."""
    if not 1 <= limit <= 50:
        raise SessionError("history limit must be between 1 and 50")
    directory = _state_home(environ) / "sessions"
    if not directory.exists():
        return {"schemaVersion": 1, "sessions": []}
    paths = sorted((path for path in directory.glob("*.jsonl") if path.is_file()), key=lambda path: path.stat().st_mtime_ns, reverse=True)
    summaries: list[dict[str, object]] = []
    for path in paths[:limit]:
        events = _read_events(path)
        if not events:
            continue
        started = next((event for event in events if event["event"] == "session-started"), None)
        finished = next((event for event in reversed(events) if event["event"] == "session-finished"), None)
        summaries.append({
            "sessionId": path.stem,
            "startedAt": started.get("timestamp") if isinstance(started, dict) else None,
            "finishedAt": finished.get("timestamp") if isinstance(finished, dict) else None,
            "reason": finished.get("reason") if isinstance(finished, dict) else None,
            "dryRun": bool(started.get("dryRun")) if isinstance(started, dict) else False,
            "simulated": bool(started.get("simulated")) if isinstance(started, dict) else False,
            "eventCount": len(events),
        })
    return {"schemaVersion": 1, "sessions": summaries}


def read_session_events(session_id: str, *, environ: Mapping[str, str] | None = None) -> dict[str, object]:
    if not re.fullmatch(r"[a-f0-9]{32}", session_id):
        raise SessionError("session id must be a controller-issued identifier")
    return {"schemaVersion": 1, "sessionId": session_id, "events": _read_events(event_log_path(session_id, environ))}


def _stop_request_path(environ: Mapping[str, str] | None = None) -> Path:
    return runtime_directory(environ) / "stop-request.json"


def request_stop(environ: Mapping[str, str] | None = None) -> dict[str, object]:
    state = read_state(environ)
    session_id = state.get("sessionId")
    if state["phase"] == "idle" or not isinstance(session_id, str):
        raise SessionError("no active omarchy-cast session to stop")
    directory = runtime_directory(environ)
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = _stop_request_path(environ)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps({"schemaVersion": 1, "sessionId": session_id}) + "\n", encoding="utf-8")
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)
    return {"schemaVersion": 1, "ok": True, "sessionId": session_id, "phase": state["phase"]}


def _stop_requested(session_id: str, environ: Mapping[str, str] | None = None) -> bool:
    path = _stop_request_path(environ)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return False
    return payload.get("schemaVersion") == 1 and payload.get("sessionId") == session_id


def recover_stale_session(environ: Mapping[str, str] | None = None) -> dict[str, object]:
    """Clear inactive runtime state only after proving no supervisor owns the lock."""
    with SessionLock(environ):
        state = read_state(environ)
        phase = str(state["phase"])
        session_id = state.get("sessionId")
        if phase == "idle":
            return {"schemaVersion": 1, "ok": True, "recovered": False, "reason": "already-idle"}
        if isinstance(session_id, str):
            append_event(session_id, "recovery-started", environ, previousPhase=phase)
        if phase == "error":
            state = transition(state, "recovering")
            write_state(state, environ)
        elif phase in {"discovering", "preparing", "connecting", "streaming"}:
            state = transition(state, "stopping", reason="recovered-stale-state")
            write_state(state, environ)
        # A checking state never touched hardware and can return directly idle.
        state = transition(state, "idle")
        write_state(state, environ)
        if isinstance(session_id, str):
            append_event(session_id, "recovery-finished", environ, previousPhase=phase)
            try:
                cleanup_live_telemetry(session_id, environ)
            except ValueError:
                pass
        _stop_request_path(environ).unlink(missing_ok=True)
        return {"schemaVersion": 1, "ok": True, "recovered": True, "previousPhase": phase, "sessionId": session_id}


class SimulatedSupervisor:
    """Exercises lifecycle ownership and cleanup without running any engine."""

    def __init__(self, environ: Mapping[str, str] | None = None) -> None:
        self.environ = environ

    def _write_transition(self, state: dict[str, object], phase: str, **updates: object) -> dict[str, object]:
        next_state = transition(state, phase, **updates)
        write_state(next_state, self.environ)
        session_id = next_state.get("sessionId")
        if isinstance(session_id, str):
            append_event(session_id, "phase", self.environ, phase=phase)
        return next_state

    def run(self, *, peer: str, mode: str, profile: str, duration: float, source: str = "display") -> dict[str, object]:
        request = validate_request(peer=peer, mode=mode, profile=profile, source=source)
        if not 0 < duration <= 300:
            raise SessionError("simulation duration must be greater than zero and at most 300 seconds")

        with SessionLock(self.environ):
            existing = read_state(self.environ)
            if existing["phase"] != "idle":
                raise SessionError("stale session state found; recover it before starting another session")
            session_id = uuid4().hex
            _stop_request_path(self.environ).unlink(missing_ok=True)
            state = transition(idle_state(), "checking", sessionId=session_id, request=request, simulated=True)
            write_state(state, self.environ)
            append_event(session_id, "session-started", self.environ, request=request, simulated=True)
            started = time.monotonic()
            stopped_by_user = False
            try:
                for phase in ("discovering", "preparing", "connecting", "streaming"):
                    if _stop_requested(session_id, self.environ):
                        stopped_by_user = True
                        break
                    state = self._write_transition(state, phase)
                    if phase != "streaming":
                        time.sleep(0.02)
                while not stopped_by_user and time.monotonic() - started < duration:
                    stopped_by_user = _stop_requested(session_id, self.environ)
                    time.sleep(0.02)
                state = self._write_transition(state, "stopping", reason="user-request" if stopped_by_user else "simulation-complete")
                state = self._write_transition(state, "idle")
                append_event(session_id, "session-finished", self.environ, reason="user-request" if stopped_by_user else "simulation-complete")
                return {"schemaVersion": 1, "ok": True, "sessionId": session_id, "reason": "user-request" if stopped_by_user else "simulation-complete"}
            except Exception as exc:
                error_state = transition(state, "error", error={"code": "simulation-failed", "message": str(exc)})
                write_state(error_state, self.environ)
                append_event(session_id, "session-error", self.environ, message=str(exc))
                raise
            finally:
                _stop_request_path(self.environ).unlink(missing_ok=True)


class DryRunSupervisor:
    """Audit the supervisor path without spawning an engine or touching hardware."""

    def __init__(self, environ: Mapping[str, str] | None = None) -> None:
        self.environ = environ

    def _write_transition(self, state: dict[str, object], phase: str, **updates: object) -> dict[str, object]:
        next_state = transition(state, phase, **updates)
        write_state(next_state, self.environ)
        session_id = next_state.get("sessionId")
        if isinstance(session_id, str):
            append_event(session_id, "phase", self.environ, phase=phase, dryRun=True)
        return next_state

    @staticmethod
    def _validate_plan(plan: Mapping[str, Any], request: Mapping[str, str]) -> None:
        selection = plan.get("selection")
        command = plan.get("command")
        execution = plan.get("execution")
        if plan.get("schemaVersion") != 1 or plan.get("kind") != "launch-plan" or plan.get("readOnly") is not True:
            raise SessionError("dry-run requires a versioned read-only launch plan")
        if not isinstance(selection, Mapping) or selection.get("peer") != request["peer"]:
            raise SessionError("launch plan receiver does not match the requested receiver")
        if selection.get("mode") != request["mode"]:
            raise SessionError("launch plan mode does not match the requested mode")
        if selection.get("source") != request["source"]:
            raise SessionError("launch plan source does not match the requested source")
        if not isinstance(command, list) or not command or command[0] != "fluxcast":
            raise SessionError("launch plan has no valid FluxCast command preview")
        if not isinstance(execution, Mapping) or execution.get("allowed") is not False:
            raise SessionError("dry-run refuses a launch plan that permits execution")

    def run(self, *, peer: str, mode: str, profile: str, plan: Mapping[str, Any], source: str = "display") -> dict[str, object]:
        request = validate_request(peer=peer, mode=mode, profile=profile, source=source)
        self._validate_plan(plan, request)
        with SessionLock(self.environ):
            existing = read_state(self.environ)
            if existing["phase"] != "idle":
                raise SessionError("stale session state found; recover it before starting another session")
            session_id = uuid4().hex
            _stop_request_path(self.environ).unlink(missing_ok=True)
            state = transition(idle_state(), "checking", sessionId=session_id, request=request, dryRun=True)
            write_state(state, self.environ)
            append_event(session_id, "session-started", self.environ, request=request, dryRun=True)
            try:
                for phase, event in (("discovering", "plan-reviewed"), ("preparing", "cleanup-verified")):
                    if _stop_requested(session_id, self.environ):
                        state = self._write_transition(state, "stopping", reason="user-request")
                        state = self._write_transition(state, "idle")
                        append_event(session_id, "session-finished", self.environ, reason="user-request", dryRun=True)
                        return {"schemaVersion": 1, "ok": True, "sessionId": session_id, "reason": "user-request", "dryRun": True}
                    state = self._write_transition(state, phase)
                    append_event(session_id, event, self.environ, dryRun=True)
                state = self._write_transition(state, "stopping", reason="dry-run-complete")
                state = self._write_transition(state, "idle")
                append_event(session_id, "session-finished", self.environ, reason="dry-run-complete", dryRun=True)
                return {"schemaVersion": 1, "ok": True, "sessionId": session_id, "reason": "dry-run-complete", "dryRun": True}
            except Exception as exc:
                error_state = transition(state, "error", error={"code": "dry-run-failed", "message": str(exc)})
                write_state(error_state, self.environ)
                append_event(session_id, "session-error", self.environ, message=str(exc), dryRun=True)
                raise
            finally:
                _stop_request_path(self.environ).unlink(missing_ok=True)


class TransportTestSupervisor:
    """Exercise the full transport lifecycle using only an injected safe adapter."""

    def __init__(self, adapter: TransportAdapter, environ: Mapping[str, str] | None = None) -> None:
        self.adapter = adapter
        self.environ = environ

    def run(self, *, peer: str, mode: str, profile: str, plan: Mapping[str, Any], timeout_seconds: float | None = 30, session_id: str | None = None, executable: bool = False, production: bool = False, source: str = "display") -> dict[str, object]:
        request = validate_request(peer=peer, mode=mode, profile=profile, source=source)
        selection = plan.get("selection")
        if not isinstance(selection, Mapping) or selection.get("peer") != request["peer"] or selection.get("mode") != request["mode"] or selection.get("source") != request["source"]:
            raise SessionError("launch plan does not match the requested session")
        validate_transport_plan(plan, executable=executable)
        if production and not executable:
            raise SessionError("production sessions require an executable guarded plan")
        with SessionLock(self.environ):
            if read_state(self.environ)["phase"] != "idle":
                raise SessionError("stale session state found; recover it before starting another session")
            session_id = session_id or uuid4().hex
            if not re.fullmatch(r"[a-f0-9]{32}", session_id):
                raise SessionError("session id must be controller-issued")
            _stop_request_path(self.environ).unlink(missing_ok=True)
            state = transition(idle_state(), "checking", sessionId=session_id, request=request, transportTest=not production, production=production)
            write_state(state, self.environ)
            append_event(session_id, "session-started", self.environ, request=request, transportTest=not production, production=production)

            def move(phase: str, **updates: object) -> None:
                nonlocal state
                state = transition(state, phase, **updates)
                write_state(state, self.environ)
                append_event(session_id, "phase", self.environ, phase=phase, transportTest=not production, production=production)

            try:
                move("discovering")
                move("preparing")

                def stage(phase: str) -> None:
                    move(phase)

                result = self.adapter.run(plan, timeout_seconds=timeout_seconds, cancelled=lambda: _stop_requested(session_id, self.environ), stage=stage)
                append_event(session_id, "transport-result", self.environ, transport=result_payload(result), transportTest=not production, production=production)
                if result.status in {"completed", "cancelled", "timeout"}:
                    if state["phase"] != "stopping":
                        move("stopping", reason="transport-" + result.status)
                    move("idle")
                    append_event(session_id, "session-finished", self.environ, reason="transport-" + result.status, transportTest=not production, production=production)
                    return {"schemaVersion": 1, "ok": result.status in {"completed", "cancelled"}, "sessionId": session_id, "transport": result_payload(result), "transportTest": not production, "production": production}
                failure_code = result.code or "transport-failed"
                move("error", error={"code": failure_code, "message": result.detail})
                append_event(session_id, "session-finished", self.environ, reason=failure_code, transportTest=not production, production=production)
                return {"schemaVersion": 1, "ok": False, "sessionId": session_id, "transport": result_payload(result), "transportTest": not production, "production": production}
            except (TransportError, StateError) as exc:
                error_code = getattr(exc, "code", "transport-test-failed")
                if state["phase"] != "idle":
                    if state["phase"] != "error":
                        state = transition(state, "error", error={"code": error_code, "message": str(exc)})
                        write_state(state, self.environ)
                    append_event(session_id, "session-error", self.environ, code=error_code, message=str(exc), transportTest=not production, production=production)
                raise SessionError(str(exc)) from exc
            finally:
                _stop_request_path(self.environ).unlink(missing_ok=True)
