"""Sanitized receiver discovery for the Omacast UI."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from contextlib import redirect_stdout
import io
import re
from typing import Callable, Iterable, Mapping, Protocol


_RECEIVER_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_ALLOWED_CAPABILITIES = frozenset({"miracast", "audio", "video"})


class ReceiverError(ValueError):
    pass


class ReceiverDiscoveryUnavailable(ReceiverError):
    pass


@dataclass(frozen=True)
class Receiver:
    """A sanitized, UI-safe receiver record; adapter metadata stays private."""

    id: str
    name: str
    kind: str
    capabilities: tuple[str, ...]


class ReceiverDiscovery(Protocol):
    def list_receivers(self, *, timeout_seconds: float) -> list[Receiver]: ...


def _receiver_from_record(record: Mapping[str, object]) -> Receiver:
    receiver_id = record.get("id")
    name = record.get("name")
    kind = record.get("kind")
    capabilities = record.get("capabilities")
    if not isinstance(receiver_id, str) or not _RECEIVER_ID.fullmatch(receiver_id):
        raise ReceiverError("receiver id must be a stable identifier")
    if not isinstance(name, str) or not name.strip() or len(name.strip()) > 120:
        raise ReceiverError("receiver name must be a non-empty display label")
    if not isinstance(kind, str) or kind not in {"fire-tv", "wfd-display"}:
        raise ReceiverError("receiver kind is unsupported")
    if not isinstance(capabilities, list) or not capabilities or any(not isinstance(item, str) for item in capabilities):
        raise ReceiverError("receiver capabilities must be a non-empty string list")
    normalized_capabilities = tuple(sorted(set(capabilities)))
    if not set(normalized_capabilities) <= _ALLOWED_CAPABILITIES or "miracast" not in normalized_capabilities:
        raise ReceiverError("receiver must advertise supported Miracast capabilities")
    return Receiver(id=receiver_id, name=name.strip(), kind=kind, capabilities=normalized_capabilities)


def normalize_receivers(records: Iterable[Mapping[str, object]]) -> list[Receiver]:
    receivers = [_receiver_from_record(record) for record in records]
    if len(receivers) > 64:
        raise ReceiverError("receiver discovery returned too many records")
    ids = [receiver.id for receiver in receivers]
    if len(ids) != len(set(ids)):
        raise ReceiverError("receiver discovery returned duplicate receiver ids")
    return sorted(receivers, key=lambda receiver: (receiver.kind != "fire-tv", receiver.name.casefold(), receiver.id))


class FixtureReceiverDiscovery:
    """A deterministic adapter used only by tests and explicit demo commands."""

    def __init__(self, records: Iterable[Mapping[str, object]]) -> None:
        self._records = tuple(dict(record) for record in records)

    def list_receivers(self, *, timeout_seconds: float) -> list[Receiver]:
        if not 1 <= timeout_seconds <= 30:
            raise ReceiverError("receiver discovery timeout must be between 1 and 30 seconds")
        return normalize_receivers(self._records)


class FluxCastReceiverDiscovery:
    """Use the installed companion engine's active WFD scan without connecting."""

    def __init__(self, *, interface: str | None = None, scanner: Callable[..., object] | None = None) -> None:
        self.interface = interface
        self._scanner = scanner

    def list_receivers(self, *, timeout_seconds: float) -> list[Receiver]:
        if not 1 <= timeout_seconds <= 30:
            raise ReceiverError("receiver discovery timeout must be between 1 and 30 seconds")
        scanner = self._scanner
        if scanner is None:
            try:
                from wfd import active_scan
            except ImportError as exc:
                raise ReceiverDiscoveryUnavailable("the FluxCast companion engine is not installed") from exc
            scanner = active_scan
        try:
            # FluxCast's human diagnostics belong in its terminal UI. Keep this
            # controller command strict JSON for the QML consumer.
            with redirect_stdout(io.StringIO()):
                peers = scanner(interface=self.interface, timeout=int(timeout_seconds))
        except Exception as exc:
            raise ReceiverDiscoveryUnavailable(str(exc) or "Wi-Fi Direct scan failed") from exc
        records: list[dict[str, object]] = []
        for peer in peers:  # type: ignore[union-attr]
            details = str(getattr(peer, "details", ""))
            # NetworkManager exposes every nearby Wi-Fi Direct peer, including
            # printers. An explicitly empty WFD IE array proves that the peer
            # is not a Miracast sink and must never appear as a cast target.
            if "wfd_ies=" in details.casefold() and "@ay []" in details.casefold():
                continue
            address = str(getattr(peer, "address", "")).upper()
            advertised_name = str(getattr(peer, "name", "")).strip()
            # The first release supports the receiver class that completed the
            # full media and cleanup gate. Generic WFD advertisements are not
            # actionable release targets yet, even if they claim Miracast.
            if "fire tv" not in advertised_name.casefold():
                continue
            name = advertised_name or f"Miracast display · {address[-5:]}"
            records.append({
                "id": address,
                "name": name,
                "kind": "fire-tv",
                "capabilities": ["miracast", "audio", "video"],
            })
        return normalize_receivers(records)


class DisabledReceiverDiscovery:
    """A negative-test adapter that explicitly refuses live discovery."""

    def list_receivers(self, *, timeout_seconds: float) -> list[Receiver]:
        del timeout_seconds
        raise ReceiverDiscoveryUnavailable("live receiver discovery is disabled until the guarded P2P adapter is hardware-validated")


def discovery_payload(discovery: ReceiverDiscovery, *, timeout_seconds: float = 8) -> dict[str, object]:
    receivers = discovery.list_receivers(timeout_seconds=timeout_seconds)
    return {
        "schemaVersion": 1,
        "readOnly": True,
        "kind": "receiver-discovery",
        "receivers": [asdict(receiver) for receiver in receivers],
    }


DEMO_FIRE_TV: tuple[dict[str, object], ...] = (
    {"id": "demo-fire-tv-lounge", "name": "Fire TV · Lounge", "kind": "fire-tv", "capabilities": ["miracast", "audio", "video"]},
)
