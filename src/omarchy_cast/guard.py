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
_PEER = re.compile(r"^(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$")


class GuardError(ValueError):
    """A request cannot safely cross the privileged boundary."""


@dataclass(frozen=True)
class GuardRequest:
    schema_version: int
    session_id: str
    uid: int
    interface: str
    peer: str
    frequency_mhz: int
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
        if not _PEER.fullmatch(self.peer):
            raise GuardError("guard receiver address is invalid")
        if not isinstance(self.frequency_mhz, int) or (
            self.frequency_mhz != 0 and not 2300 <= self.frequency_mhz <= 7125
        ):
            raise GuardError("guard P2P frequency is invalid")
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
        "--peer", request.peer,
        "--frequency", str(request.frequency_mhz),
        "--duration", str(request.duration_seconds),
    )


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
    expected_trigger = f"/run/omarchy-cast/{payload.get('sessionId')}/user/trigger"
    if trigger is not None and (not isinstance(trigger, str) or trigger != expected_trigger):
        raise GuardError("guard status has an invalid trigger path")
    broker = payload.get("brokerPath")
    expected_broker = f"/run/omarchy-cast/{payload.get('sessionId')}/supplicant.sock"
    if broker is not None and (not isinstance(broker, str) or broker != expected_broker):
        raise GuardError("guard status has an invalid broker path")
    return dict(payload)
