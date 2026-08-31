"""Shell-independent user-service launcher for Omacast sessions."""

from __future__ import annotations

from pathlib import Path

from .bounds import bounded_text
from .command import Runner, run_command
from .identity import receiver_address
from .pairing import sealed_pairing_credential, validate_pairing_pin, validate_sealed_credential_path


class ServiceError(RuntimeError):
    pass


UNIT_NAME = "omacast-session.service"
INHIBIT_REASON = "Desktop casting is active"
SESSION_CPU_WEIGHT = 10_000


def session_service_command(
    *,
    executable: str,
    peer: str,
    mode: str,
    profile: str,
    duration: int,
    simulate: bool = False,
    pairing_credential_path: str | None = None,
    backend: str = "direct",
) -> tuple[str, ...]:
    launcher = Path(executable).resolve()
    if not launcher.is_file():
        raise ServiceError("Omacast launcher is unavailable")
    if backend not in {"direct", "networkmanager"}:
        raise ServiceError("The Wi-Fi Direct backend is unsupported")
    if not simulate:
        try:
            peer = receiver_address(peer)
        except ValueError as exc:
            raise ServiceError(str(exc)) from exc
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
        f"--property=CPUWeight={SESSION_CPU_WEIGHT}",
    ]
    if pairing_credential_path is not None:
        validate_sealed_credential_path(pairing_credential_path)
        command.append(f"--property=LoadCredential=omacast-pairing-pin:{pairing_credential_path}")
    command.extend([
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
        "--backend",
        backend,
    ])
    if pairing_credential_path is not None:
        command.append("--pairing-pin-credential")
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
    pairing_pin: bytes | None = None,
    backend: str = "direct",
    runner: Runner = run_command,
) -> dict[str, object]:
    try:
        if pairing_pin is None:
            command = session_service_command(
                executable=executable, peer=peer, mode=mode, profile=profile,
                duration=duration, simulate=simulate,
                backend=backend,
            )
            result = runner(command, timeout=10)
        else:
            if simulate:
                raise ServiceError("Pairing credentials are not accepted by simulated sessions")
            encoded = validate_pairing_pin(pairing_pin)
            with sealed_pairing_credential(encoded) as credential_path:
                command = session_service_command(
                    executable=executable, peer=peer, mode=mode, profile=profile,
                    duration=duration, simulate=False,
                    pairing_credential_path=credential_path,
                    backend=backend,
                )
                result = runner(command, timeout=10)
    except (OSError, ValueError) as exc:
        raise ServiceError("could not start the Omacast session service: " + bounded_text(str(exc), limit=512)) from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "systemd refused the session").strip()
        raise ServiceError(detail[-1024:])
    return {
        "schemaVersion": 1,
        "ok": True,
        "phase": "starting",
        "unit": UNIT_NAME,
    }


def stop_pending_session_service(*, runner: Runner = run_command) -> dict[str, object]:
    """Cancel a service launch before its supervised session owns runtime state."""
    command = ("systemctl", "--user", "stop", UNIT_NAME)
    try:
        result = runner(command, timeout=25)
    except OSError as exc:
        raise ServiceError("could not cancel the Omacast session service: " + bounded_text(str(exc), limit=512)) from exc
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
