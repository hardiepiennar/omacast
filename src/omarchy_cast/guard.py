"""Strict contract for the privileged cast-network helper.

The QML panel and unprivileged controller never choose a helper executable,
write a policy file, or pass a shell fragment. They may only construct this
small, versioned request for the package-installed helper.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
import re

from .bounds import BoundError, bounded_text, validate_json_budget
from .command import Runner, run_command
from .identity import receiver_address


HELPER_PATH = "/usr/lib/omarchy-cast/omarchy-cast-guard"
_SESSION_ID = re.compile(r"^[a-f0-9]{32}$")
_INTERFACE = re.compile(r"^[A-Za-z0-9_.-]{1,15}$")
MAX_RECOVERY_INTERFACES = 32
MAX_RECOVERY_CHILD_INTERFACES = 64


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
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise GuardError("unsupported guard request schema")
        if not _SESSION_ID.fullmatch(self.session_id):
            raise GuardError("guard session id must be controller-issued")
        if type(self.uid) is not int or self.uid < 1000 or self.uid > 2_147_483_647:
            raise GuardError("guard uid is outside the permitted user range")
        if not _INTERFACE.fullmatch(self.interface):
            raise GuardError("guard interface name is invalid")
        try:
            receiver_address(self.peer)
        except ValueError as exc:
            raise GuardError("guard receiver address is invalid") from exc
        if type(self.frequency_mhz) is not int or (
            self.frequency_mhz != 0 and not 2300 <= self.frequency_mhz <= 7125
        ):
            raise GuardError("guard P2P frequency is invalid")
        if type(self.duration_seconds) is not int or not 60 <= self.duration_seconds <= 1800:
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


def reclaim_command(*, uid: int, interface: str, helper_path: str = HELPER_PATH) -> tuple[str, ...]:
    if helper_path != HELPER_PATH:
        raise GuardError("production guard path is fixed by the installed package")
    if type(uid) is not int or uid < 1000 or uid > 2_147_483_647:
        raise GuardError("guard uid is outside the permitted user range")
    if not _INTERFACE.fullmatch(interface):
        raise GuardError("guard interface name is invalid")
    return (
        HELPER_PATH, "reclaim", "--schema-version", "1",
        "--uid", str(uid), "--interface", interface,
    )


def orphan_parent_interfaces(
    interfaces: object, *, runner: Runner = run_command,
) -> tuple[str, ...]:
    """Return managed adapters with P2P children from one bounded iw snapshot."""
    if not isinstance(interfaces, (list, tuple)) or len(interfaces) > MAX_RECOVERY_INTERFACES:
        raise GuardError("Wi-Fi recovery interface list is invalid")
    candidates: list[str] = []
    for interface in interfaces:
        if not isinstance(interface, str) or not _INTERFACE.fullmatch(interface):
            raise GuardError("guard interface name is invalid")
        if interface not in candidates:
            candidates.append(interface)
    if not candidates:
        return ()
    result = runner(("iw", "dev"), timeout=5)
    if result.returncode != 0:
        raise GuardError("could not inspect Wi-Fi Direct interfaces")
    child_names: set[str] = set()
    for line in result.stdout.splitlines():
        fields = line.strip().split()
        if len(fields) != 2 or fields[0] != "Interface":
            continue
        if len(child_names) >= MAX_RECOVERY_CHILD_INTERFACES:
            raise GuardError("Wi-Fi Direct interface discovery was incomplete")
        child_names.add(fields[1])
    return tuple(
        interface for interface in candidates
        if any(name.startswith(f"p2p-{interface}-") for name in child_names)
    )


def reclaim_orphan_interfaces(
    interface: str, *, uid: int | None = None, runner: Runner = run_command,
) -> dict[str, object]:
    caller_uid = os.getuid() if uid is None else uid
    result = runner(("pkexec", *reclaim_command(uid=caller_uid, interface=interface)), timeout=60)
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "administrator approval was cancelled"
        raise GuardError(bounded_text(detail.splitlines()[0], limit=240))
    try:
        payload = json.loads(result.stdout)
    except (json.JSONDecodeError, RecursionError) as exc:
        raise GuardError("guard returned invalid P2P recovery status") from exc
    return validate_reclaim_result(payload)


def validate_reclaim_result(payload: object) -> dict[str, object]:
    try:
        validate_json_budget(payload, max_depth=2, max_nodes=8, max_collection_items=8, max_string_chars=64)
    except BoundError as exc:
        raise GuardError("guard returned an oversized P2P recovery status") from exc
    if not isinstance(payload, dict) or set(payload) != {"schemaVersion", "kind", "ok", "reclaimed"}:
        raise GuardError("guard returned an unexpected P2P recovery status")
    if type(payload.get("schemaVersion")) is not int or payload.get("schemaVersion") != 1 or payload.get("kind") != "omarchy-cast-guard-reclaim-status" or payload.get("ok") is not True:
        raise GuardError("guard returned an incompatible P2P recovery status")
    reclaimed = payload.get("reclaimed")
    if not isinstance(reclaimed, int) or isinstance(reclaimed, bool) or not 0 <= reclaimed <= 32:
        raise GuardError("guard returned an invalid P2P recovery count")
    return dict(payload)


def validate_helper_result(payload: object) -> dict[str, object]:
    """Accept only the narrow status document returned by the helper."""
    try:
        validate_json_budget(payload, max_depth=2, max_nodes=16, max_collection_items=12, max_string_chars=256)
    except BoundError as exc:
        raise GuardError("guard returned an oversized status document") from exc
    if not isinstance(payload, dict) or type(payload.get("schemaVersion")) is not int or payload.get("schemaVersion") != 1:
        raise GuardError("guard returned an unsupported status document")
    if payload.get("kind") != "omarchy-cast-guard-status":
        raise GuardError("guard returned an unexpected status document")
    if type(payload.get("ok")) is not bool:
        raise GuardError("guard status is missing ok")
    phase = payload.get("phase")
    if phase not in {"ready", "active", "cleaned", "error"}:
        raise GuardError("guard status has an invalid phase")
    session_id = payload.get("sessionId")
    if not isinstance(session_id, str) or not _SESSION_ID.fullmatch(session_id):
        raise GuardError("guard status has an invalid session id")
    base_fields = {"schemaVersion", "kind", "ok", "phase", "sessionId", "error"}
    expected_fields = base_fields | ({"triggerPath", "brokerPath"} if phase == "ready" else set())
    if set(payload) != expected_fields:
        raise GuardError("guard status has unexpected fields")
    error = payload.get("error")
    if phase == "error":
        if payload.get("ok") is not False or not isinstance(error, str) or not error or len(error) > 240:
            raise GuardError("guard error status is inconsistent")
    elif payload.get("ok") is not True or error is not None:
        raise GuardError("guard success status is inconsistent")
    trigger = payload.get("triggerPath")
    expected_trigger = f"/run/omarchy-cast/{session_id}/user/trigger"
    if phase == "ready" and (not isinstance(trigger, str) or trigger != expected_trigger):
        raise GuardError("guard status has an invalid trigger path")
    broker = payload.get("brokerPath")
    expected_broker = f"/run/omarchy-cast/{session_id}/supplicant.sock"
    if phase == "ready" and (not isinstance(broker, str) or broker != expected_broker):
        raise GuardError("guard status has an invalid broker path")
    return dict(payload)
