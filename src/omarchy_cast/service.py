"""Shell-independent user-service launcher for Omacast sessions."""

from __future__ import annotations

from pathlib import Path
import subprocess
from typing import Callable, Sequence


class ServiceError(RuntimeError):
    pass


Runner = Callable[..., subprocess.CompletedProcess[str]]
UNIT_NAME = "omacast-session.service"
INHIBIT_REASON = "Desktop casting is active"


def session_service_command(
    *,
    executable: str,
    peer: str,
    mode: str,
    profile: str,
    duration: int,
    simulate: bool = False,
) -> tuple[str, ...]:
    launcher = Path(executable).resolve()
    if not launcher.is_file():
        raise ServiceError("Omacast launcher is unavailable")
    command = [
        "systemd-run",
        "--user",
        "--quiet",
        "--collect",
        f"--unit={UNIT_NAME}",
        "--description=Omacast casting session",
        "--property=Type=exec",
        "--property=KillMode=mixed",
        "--property=TimeoutStopSec=20s",
        "systemd-inhibit",
        "--what=idle:sleep",
        "--who=Omacast",
        f"--why={INHIBIT_REASON}",
        "--mode=block",
        str(launcher),
        "connect",
        "--peer",
        peer,
        "--mode",
        mode,
        "--profile",
        profile,
        "--duration",
        str(duration),
    ]
    if simulate:
        command.append("--simulate")
    return tuple(command)


def start_session_service(
    *,
    executable: str,
    peer: str,
    mode: str,
    profile: str,
    duration: int,
    simulate: bool = False,
    runner: Runner = subprocess.run,
) -> dict[str, object]:
    command = session_service_command(
        executable=executable,
        peer=peer,
        mode=mode,
        profile=profile,
        duration=duration,
        simulate=simulate,
    )
    try:
        result = runner(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ServiceError(f"could not start the Omacast session service: {exc}") from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "systemd refused the session").strip()
        raise ServiceError(detail[-1024:])
    return {
        "schemaVersion": 1,
        "ok": True,
        "phase": "starting",
        "unit": UNIT_NAME,
    }


def stop_pending_session_service(*, runner: Runner = subprocess.run) -> dict[str, object]:
    """Cancel a service launch before its supervised session owns runtime state."""
    command = ("systemctl", "--user", "stop", UNIT_NAME)
    try:
        result = runner(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
            timeout=25,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ServiceError(f"could not cancel the Omacast session service: {exc}") from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "systemd refused the cancellation").strip()
        # Collected transient units disappear immediately after exit. In that
        # state there is nothing left to cancel, which is the desired result.
        if "not loaded" not in detail.lower() and "not found" not in detail.lower():
            raise ServiceError(detail[-1024:])
    return {
        "schemaVersion": 1,
        "ok": True,
        "phase": "idle",
        "unit": UNIT_NAME,
        "reason": "launch-cancelled",
    }
