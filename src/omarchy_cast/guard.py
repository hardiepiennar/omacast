"""Strict contract for the privileged cast-network helper.

The QML panel and unprivileged controller never choose a helper executable,
write a policy file, or pass a shell fragment. They may only construct this
small, versioned request for the package-installed helper.
"""

from __future__ import annotations

from dataclasses import dataclass
import re


HELPER_PATH = "/usr/lib/omarchy-cast/omarchy-cast-guard"
_SESSION_ID = re.compile(r"^[a-f0-9]{32}$")
_INTERFACE = re.compile(r"^[A-Za-z0-9_.-]{1,15}$")


class GuardError(ValueError):
    """A request cannot safely cross the privileged boundary."""


@dataclass(frozen=True)
class GuardRequest:
    schema_version: int
    session_id: str
    uid: int
    interface: str
    duration_seconds: int

    def validate(self) -> "GuardRequest":
        if self.schema_version != 1:
            raise GuardError("unsupported guard request schema")
        if not _SESSION_ID.fullmatch(self.session_id):
            raise GuardError("guard session id must be controller-issued")
        if not isinstance(self.uid, int) or self.uid < 1000 or self.uid > 2_147_483_647:
            raise GuardError("guard uid is outside the permitted user range")
        if not _INTERFACE.fullmatch(self.interface):
            raise GuardError("guard interface name is invalid")
        if not isinstance(self.duration_seconds, int) or not 60 <= self.duration_seconds <= 1800:
            raise GuardError("guard duration must be between 60 and 1800 seconds")
        return self


def prepare_command(request: GuardRequest, *, helper_path: str = HELPER_PATH) -> tuple[str, ...]:
    """Build the one fixed-argument invocation accepted by the helper."""
    request.validate()
    if helper_path != HELPER_PATH:
        raise GuardError("production guard path is fixed by the installed package")
    return (
        HELPER_PATH,
        "prepare",
        "--schema-version", "1",
        "--session", request.session_id,
        "--uid", str(request.uid),
        "--interface", request.interface,
        "--duration", str(request.duration_seconds),
    )


def stop_command(request: GuardRequest, *, helper_path: str = HELPER_PATH) -> tuple[str, ...]:
    """Build the matching fixed stop request for an already-approved session."""
    command = list(prepare_command(request, helper_path=helper_path))
    command[1] = "stop"
    return tuple(command)


def validate_helper_result(payload: object) -> dict[str, object]:
    """Accept only the narrow status document returned by the helper."""
    if not isinstance(payload, dict) or payload.get("schemaVersion") != 1:
        raise GuardError("guard returned an unsupported status document")
    if payload.get("kind") != "omarchy-cast-guard-status":
        raise GuardError("guard returned an unexpected status document")
    if not isinstance(payload.get("ok"), bool):
        raise GuardError("guard status is missing ok")
    if payload.get("phase") not in {"ready", "active", "cleaned", "error"}:
        raise GuardError("guard status has an invalid phase")
    session_id = payload.get("sessionId")
    if session_id is not None and (not isinstance(session_id, str) or not _SESSION_ID.fullmatch(session_id)):
        raise GuardError("guard status has an invalid session id")
    trigger = payload.get("triggerPath")
    if trigger is not None and (not isinstance(trigger, str) or not trigger.startswith("/run/user/")):
        raise GuardError("guard status has an invalid trigger path")
    return dict(payload)
