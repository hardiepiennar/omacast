"""Sanitized receiver discovery for the Omacast UI."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from contextlib import redirect_stdout
import math
import os
import re
from typing import Callable, Iterable, Mapping, Protocol

from .bounds import bounded_text
from .identity import receiver_address


_ALLOWED_CAPABILITIES = frozenset({"miracast", "audio", "video"})
MAX_RECEIVERS = 64
MAX_DISCOVERY_PEERS = 256
MAX_PEER_DETAILS_CHARS = 4_096
_WFD_DEVICE_INFO = re.compile(
    r"(?:^|[;\s])wfd_dev_info=0x([0-9a-f]{4})[0-9a-f]{8}(?![0-9a-z])",
    re.IGNORECASE,
)
_WFD_IES = re.compile(r"(?:^|[;\s])wfd_ies=([^;\n]*)", re.IGNORECASE)
_WFD_BYTE = re.compile(r"\b0x([0-9a-f]{1,2})\b", re.IGNORECASE)


class ReceiverError(ValueError):
    pass


class ReceiverDiscoveryUnavailable(ReceiverError):
    pass


def _validate_timeout(timeout_seconds: float) -> None:
    if type(timeout_seconds) not in {int, float} or not math.isfinite(timeout_seconds) or not 1 <= timeout_seconds <= 30:
        raise ReceiverError("receiver discovery timeout must be between 1 and 30 seconds")


@dataclass(frozen=True)
class Receiver:
    """A sanitized, UI-safe receiver record; adapter metadata stays private."""

    id: str
    name: str
    kind: str
    capabilities: tuple[str, ...]


class ReceiverDiscovery(Protocol):
    def list_receivers(self, *, timeout_seconds: float) -> list[Receiver]: ...


def _wfd_device_type(details: str) -> int | None:
    """Return the advertised WFD device type, or ``None`` if it is invalid."""
    parsed = _WFD_DEVICE_INFO.search(details)
    if parsed:
        return int(parsed.group(1), 16) & 0x03

    raw_ies = _WFD_IES.search(details)
    if not raw_ies:
        return None
    values = [int(value, 16) for value in _WFD_BYTE.findall(raw_ies.group(1))]
    offset = 0
    while offset + 3 <= len(values):
        subelement_id = values[offset]
        subelement_length = (values[offset + 1] << 8) | values[offset + 2]
        end = offset + 3 + subelement_length
        if end > len(values):
            return None
        if subelement_id == 0:
            if subelement_length != 6:
                return None
            device_info = (values[offset + 3] << 8) | values[offset + 4]
            return device_info & 0x03
        offset = end
    return None


def _receiver_from_record(record: Mapping[str, object]) -> Receiver:
    try:
        receiver_id = receiver_address(record.get("id"))
    except ValueError as exc:
        raise ReceiverError("receiver id must be a Wi-Fi Direct MAC address") from exc
    name = record.get("name")
    kind = record.get("kind")
    capabilities = record.get("capabilities")
    if not isinstance(name, str) or not name.strip() or len(name.strip()) > 120:
        raise ReceiverError("receiver name must be a non-empty display label")
    if any(ord(character) < 32 or ord(character) == 127 for character in name):
        raise ReceiverError("receiver name contains control characters")
    if not isinstance(kind, str) or kind not in {"fire-tv", "wfd-display"}:
        raise ReceiverError("receiver kind is unsupported")
    if not isinstance(capabilities, list) or not capabilities or any(not isinstance(item, str) for item in capabilities):
        raise ReceiverError("receiver capabilities must be a non-empty string list")
    normalized_capabilities = tuple(sorted(set(capabilities)))
    if not set(normalized_capabilities) <= _ALLOWED_CAPABILITIES or "miracast" not in normalized_capabilities:
        raise ReceiverError("receiver must advertise supported Miracast capabilities")
    return Receiver(id=receiver_id, name=name.strip(), kind=kind, capabilities=normalized_capabilities)


def normalize_receivers(records: Iterable[Mapping[str, object]]) -> list[Receiver]:
    receivers: list[Receiver] = []
    for record in records:
        if len(receivers) >= MAX_RECEIVERS:
            raise ReceiverError("receiver discovery returned too many records")
        receivers.append(_receiver_from_record(record))
    ids = [receiver.id for receiver in receivers]
    if len(ids) != len(set(ids)):
        raise ReceiverError("receiver discovery returned duplicate receiver ids")
    return sorted(receivers, key=lambda receiver: (receiver.kind != "fire-tv", receiver.name.casefold(), receiver.id))


class FixtureReceiverDiscovery:
    """A deterministic adapter used only by tests and explicit demo commands."""

    def __init__(self, records: Iterable[Mapping[str, object]]) -> None:
        self._records = tuple(dict(record) for record in records)

    def list_receivers(self, *, timeout_seconds: float) -> list[Receiver]:
        _validate_timeout(timeout_seconds)
        return normalize_receivers(self._records)


class FluxCastReceiverDiscovery:
    """Use the installed companion engine's active WFD scan without connecting."""

    def __init__(self, *, interface: str | None = None, scanner: Callable[..., object] | None = None) -> None:
        self.interface = interface
        self._scanner = scanner

    def list_receivers(self, *, timeout_seconds: float) -> list[Receiver]:
        _validate_timeout(timeout_seconds)
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
            with open(os.devnull, "w", encoding="utf-8") as discarded, redirect_stdout(discarded):
                peers = scanner(interface=self.interface, timeout=int(timeout_seconds))
        except Exception as exc:
            raise ReceiverDiscoveryUnavailable(bounded_text(str(exc), limit=240, fallback="Wi-Fi Direct scan failed") or "Wi-Fi Direct scan failed") from exc
        records: list[dict[str, object]] = []
        peer_count = 0
        try:
            for peer in peers:  # type: ignore[union-attr]
                peer_count += 1
                if peer_count > MAX_DISCOVERY_PEERS:
                    raise ReceiverDiscoveryUnavailable("receiver discovery returned too many peer records")
                if len(records) >= MAX_RECEIVERS:
                    raise ReceiverDiscoveryUnavailable("receiver discovery returned too many records")
                details = bounded_text(getattr(peer, "details", ""), limit=MAX_PEER_DETAILS_CHARS)
                # NetworkManager exposes every nearby Wi-Fi Direct peer, including
                # printers and source-only phones. Only peers whose WFD Device
                # Information identifies a sink are valid cast targets.
                if _wfd_device_type(details) not in {1, 2, 3}:
                    continue
                try:
                    address = receiver_address(getattr(peer, "address", ""))
                except ValueError:
                    continue
                raw_name = getattr(peer, "name", "")
                advertised_name = bounded_text(raw_name, limit=120).strip()
                name = advertised_name or f"Miracast display · {address[-5:]}"
                is_fire_tv = "fire tv" in advertised_name.casefold()
                records.append({
                    "id": address,
                    "name": name,
                    "kind": "fire-tv" if is_fire_tv else "wfd-display",
                    "capabilities": ["miracast", "audio", "video"] if is_fire_tv else ["miracast"],
                })
        except ReceiverDiscoveryUnavailable:
            raise
        except Exception as exc:
            raise ReceiverDiscoveryUnavailable(
                bounded_text(str(exc), limit=240, fallback="Wi-Fi Direct scan failed") or "Wi-Fi Direct scan failed"
            ) from exc
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
    {"id": "02:00:00:00:00:FE", "name": "Fire TV · Lounge", "kind": "fire-tv", "capabilities": ["miracast", "audio", "video"]},
)
