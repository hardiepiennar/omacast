"""Canonical identifiers shared across the unprivileged casting pipeline."""

from __future__ import annotations

import re


_RECEIVER_ADDRESS = re.compile(r"^(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$")


def receiver_address(value: object) -> str:
    """Return one canonical Wi-Fi Direct peer address or reject the value."""
    if not isinstance(value, str) or not _RECEIVER_ADDRESS.fullmatch(value):
        raise ValueError("receiver identifier must be a Wi-Fi Direct MAC address")
    octets = bytes(int(part, 16) for part in value.split(":"))
    if octets in {b"\0" * 6, b"\xff" * 6} or octets[0] & 1:
        raise ValueError("receiver identifier must be a unicast Wi-Fi Direct MAC address")
    return value.upper()
