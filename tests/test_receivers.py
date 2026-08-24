from __future__ import annotations

import unittest
from types import SimpleNamespace

from omarchy_cast.receivers import DisabledReceiverDiscovery, FixtureReceiverDiscovery, FluxCastReceiverDiscovery, ReceiverDiscoveryUnavailable, ReceiverError, discovery_payload, normalize_receivers


class ReceiverDiscoveryTest(unittest.TestCase):
    def records(self) -> list[dict[str, object]]:
        return [
            {"id": "fire-tv-bedroom", "name": "Fire TV · Bedroom", "kind": "fire-tv", "capabilities": ["video", "miracast", "audio"]},
            {"id": "fire-tv-lounge", "name": "Fire TV · Lounge", "kind": "fire-tv", "capabilities": ["miracast", "audio"]},
        ]

    def test_fixture_normalizes_capabilities_and_sorts_by_label(self) -> None:
        payload = discovery_payload(FixtureReceiverDiscovery(reversed(self.records())))
        self.assertTrue(payload["readOnly"])
        self.assertEqual([item["id"] for item in payload["receivers"]], ["fire-tv-bedroom", "fire-tv-lounge"])
        self.assertEqual(payload["receivers"][1]["capabilities"], ("audio", "miracast"))

    def test_duplicate_or_invalid_records_are_rejected(self) -> None:
        duplicate = self.records() + [self.records()[0]]
        with self.assertRaisesRegex(ReceiverError, "duplicate"):
            normalize_receivers(duplicate)
        with self.assertRaisesRegex(ReceiverError, "stable"):
            normalize_receivers([{"id": "Fire TV", "name": "TV", "kind": "fire-tv", "capabilities": ["miracast"]}])

    def test_validated_fire_tv_sinks_sort_before_generic_wfd_labels(self) -> None:
        receivers = normalize_receivers([
            {"id": "generic-tv", "name": "[TV] Generic display", "kind": "wfd-display", "capabilities": ["miracast"]},
            {"id": "fire-tv", "name": "Living room Fire TV", "kind": "fire-tv", "capabilities": ["miracast"]},
        ])
        self.assertEqual([receiver.id for receiver in receivers], ["fire-tv", "generic-tv"])

    def test_live_adapter_is_explicitly_disabled(self) -> None:
        with self.assertRaises(ReceiverDiscoveryUnavailable):
            discovery_payload(DisabledReceiverDiscovery())

    def test_fluxcast_adapter_sanitizes_and_sorts_live_peers(self) -> None:
        calls: list[tuple[str | None, int]] = []

        def scanner(*, interface: str | None, timeout: int) -> list[SimpleNamespace]:
            calls.append((interface, timeout))
            return [
                SimpleNamespace(address="02:00:00:00:00:01", name="Living Room Fire TV", details="manufacturer=Amazon; wfd_ies=(<[byte 0x00, 0x11]>,)"),
                SimpleNamespace(address="02:00:00:00:00:02", name="Nearby printer", details="manufacturer=Printer; wfd_ies=(<@ay []>,)"),
                SimpleNamespace(address="00:11:22:33:44:55", name="[TV] Generic display", details="wfd_ies=(<[byte 0x00, 0x11]>,)"),
            ]

        payload = discovery_payload(FluxCastReceiverDiscovery(interface="wlan42", scanner=scanner), timeout_seconds=6)
        self.assertEqual(calls, [("wlan42", 6)])
        self.assertEqual(payload["receivers"][0]["id"], "02:00:00:00:00:01")
        self.assertEqual(payload["receivers"][0]["kind"], "fire-tv")
        self.assertEqual(len(payload["receivers"]), 1)
        self.assertNotIn("02:00:00:00:00:02", [receiver["id"] for receiver in payload["receivers"]])
        self.assertNotIn("00:11:22:33:44:55", [receiver["id"] for receiver in payload["receivers"]])
