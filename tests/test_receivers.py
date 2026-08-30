from __future__ import annotations

import unittest
from types import SimpleNamespace

from omarchy_cast.receivers import DisabledReceiverDiscovery, FixtureReceiverDiscovery, FluxCastReceiverDiscovery, ReceiverDiscoveryUnavailable, ReceiverError, discovery_payload, normalize_receivers


class ReceiverDiscoveryTest(unittest.TestCase):
    def records(self) -> list[dict[str, object]]:
        return [
            {"id": "02:00:00:00:00:01", "name": "Fire TV · Bedroom", "kind": "fire-tv", "capabilities": ["video", "miracast", "audio"]},
            {"id": "02:00:00:00:00:02", "name": "Fire TV · Lounge", "kind": "fire-tv", "capabilities": ["miracast", "audio"]},
        ]

    def test_fixture_normalizes_capabilities_and_sorts_by_label(self) -> None:
        payload = discovery_payload(FixtureReceiverDiscovery(reversed(self.records())))
        self.assertTrue(payload["readOnly"])
        self.assertEqual([item["id"] for item in payload["receivers"]], ["02:00:00:00:00:01", "02:00:00:00:00:02"])
        self.assertEqual(payload["receivers"][1]["capabilities"], ("audio", "miracast"))

    def test_duplicate_or_invalid_records_are_rejected(self) -> None:
        duplicate = self.records() + [self.records()[0]]
        with self.assertRaisesRegex(ReceiverError, "duplicate"):
            normalize_receivers(duplicate)
        with self.assertRaisesRegex(ReceiverError, "MAC address"):
            normalize_receivers([{"id": "Fire TV", "name": "TV", "kind": "fire-tv", "capabilities": ["miracast"]}])
        with self.assertRaisesRegex(ReceiverError, "control"):
            normalize_receivers([{"id": "02:00:00:00:00:03", "name": "Fire TV\nInjected", "kind": "fire-tv", "capabilities": ["miracast"]}])

    def test_receiver_limit_stops_consuming_an_unbounded_iterable(self) -> None:
        consumed = 0

        def records():
            nonlocal consumed
            for index in range(1_000):
                consumed += 1
                yield {"id": f"02:00:00:00:00:{index:02X}", "name": f"Fire TV {index}", "kind": "fire-tv", "capabilities": ["miracast"]}

        with self.assertRaisesRegex(ReceiverError, "too many"):
            normalize_receivers(records())
        self.assertEqual(consumed, 65)

    def test_markup_like_name_remains_data_for_plain_text_ui(self) -> None:
        receiver = normalize_receivers([{"id": "02:00:00:00:00:03", "name": "<b>Fire TV</b>", "kind": "fire-tv", "capabilities": ["miracast"]}])[0]
        self.assertEqual(receiver.name, "<b>Fire TV</b>")

    def test_validated_fire_tv_sinks_sort_before_generic_wfd_labels(self) -> None:
        receivers = normalize_receivers([
            {"id": "02:00:00:00:00:04", "name": "[TV] Generic display", "kind": "wfd-display", "capabilities": ["miracast"]},
            {"id": "02:00:00:00:00:05", "name": "Living room Fire TV", "kind": "fire-tv", "capabilities": ["miracast"]},
        ])
        self.assertEqual([receiver.id for receiver in receivers], ["02:00:00:00:00:05", "02:00:00:00:00:04"])

    def test_live_adapter_is_explicitly_disabled(self) -> None:
        with self.assertRaises(ReceiverDiscoveryUnavailable):
            discovery_payload(DisabledReceiverDiscovery())

    def test_discovery_timeout_rejects_boolean_and_nonfinite_values(self) -> None:
        discovery = FixtureReceiverDiscovery(self.records())
        for value in (True, False, float("nan"), float("inf")):
            with self.subTest(value=value), self.assertRaisesRegex(ReceiverError, "timeout"):
                discovery.list_receivers(timeout_seconds=value)

    def test_scanner_iterator_failure_is_a_controlled_discovery_error(self) -> None:
        def scanner(*, interface: str | None, timeout: int):
            del interface, timeout
            def peers():
                yield SimpleNamespace(
                    address="02:00:00:00:00:01", name="Fire TV",
                    details="wfd_dev_info=0x00111c4400c8",
                )
                raise RuntimeError("iterator failed")
            return peers()

        with self.assertRaisesRegex(ReceiverDiscoveryUnavailable, "iterator failed"):
            discovery_payload(FluxCastReceiverDiscovery(scanner=scanner))

    def test_fluxcast_adapter_sanitizes_and_sorts_live_peers(self) -> None:
        calls: list[tuple[str | None, int]] = []

        def scanner(*, interface: str | None, timeout: int) -> list[SimpleNamespace]:
            calls.append((interface, timeout))
            return [
                SimpleNamespace(address="02:00:00:00:00:01", name="Living Room Fire TV", details="manufacturer=Amazon; wfd_dev_info=0x00111c4400c8"),
                SimpleNamespace(address="02:00:00:00:00:02", name="Nearby printer", details="manufacturer=Printer; wfd_ies=(<@ay []>,)"),
                SimpleNamespace(address="02:00:00:00:00:03", name="Someone's phone", details="manufacturer=Phone"),
                SimpleNamespace(address="00:11:22:33:44:55", name="[TV] Generic display", details="wfd_dev_info=0x00111c4400c8; sink_rtsp_port=7236"),
                SimpleNamespace(address="generic-receiver", name="Malformed sink", details="wfd_dev_info=0x00111c4400c8"),
            ]

        payload = discovery_payload(FluxCastReceiverDiscovery(interface="wlan42", scanner=scanner), timeout_seconds=6)
        self.assertEqual(calls, [("wlan42", 6)])
        self.assertEqual(len(payload["receivers"]), 2)
        self.assertEqual(payload["receivers"][0]["id"], "02:00:00:00:00:01")
        self.assertEqual(payload["receivers"][0]["kind"], "fire-tv")
        # A non-Amazon sink is a real cast target and keeps the generic kind.
        self.assertEqual(payload["receivers"][1]["id"], "00:11:22:33:44:55")
        self.assertEqual(payload["receivers"][1]["kind"], "wfd-display")
        self.assertEqual(payload["receivers"][1]["capabilities"], ("miracast",))
        # An empty WFD IE and a peer with no WFD advertisement stay excluded.
        self.assertNotIn("02:00:00:00:00:02", [receiver["id"] for receiver in payload["receivers"]])
        self.assertNotIn("02:00:00:00:00:03", [receiver["id"] for receiver in payload["receivers"]])
        self.assertNotIn("generic-receiver", [receiver["id"] for receiver in payload["receivers"]])

    def test_fluxcast_adapter_rejects_peers_without_a_valid_sink_role(self) -> None:
        def scanner(*, interface: str | None, timeout: int) -> list[SimpleNamespace]:
            del interface, timeout
            return [
                SimpleNamespace(address="02:00:00:00:00:10", name="Source-only phone", details="wfd_dev_info=0x00101c4400c8"),
                SimpleNamespace(address="02:00:00:00:00:11", name="RTSP-only peer", details="sink_rtsp_port=7236"),
                SimpleNamespace(address="02:00:00:00:00:12", name="Malformed display", details="wfd_dev_info=not-hex"),
                SimpleNamespace(address="02:00:00:00:00:17", name="Truncated display", details="wfd_dev_info=0x00111c44"),
                SimpleNamespace(address="02:00:00:00:00:18", name="Contaminated display", details="wfd_dev_info=0x00111c4400c8wrong"),
                SimpleNamespace(address="02:00:00:00:00:13", name="Secondary sink", details="wfd_dev_info=0x00121c4400c8"),
                SimpleNamespace(address="02:00:00:00:00:14", name="Dual-role display", details="wfd_dev_info=0x00131c4400c8"),
                SimpleNamespace(address="02:00:00:00:00:15", name="Raw source", details="wfd_ies=<@ay [byte 0x00, 0x00, 0x06, 0x00, 0x10, 0x1c, 0x44, 0x00, 0xc8]>"),
                SimpleNamespace(address="02:00:00:00:00:16", name="Raw primary sink", details="wfd_ies=<@ay [byte 0x00, 0x00, 0x06, 0x00, 0x11, 0x1c, 0x44, 0x00, 0xc8]>"),
            ]

        payload = discovery_payload(FluxCastReceiverDiscovery(scanner=scanner))

        self.assertEqual(
            [receiver["id"] for receiver in payload["receivers"]],
            ["02:00:00:00:00:14", "02:00:00:00:00:16", "02:00:00:00:00:13"],
        )

    def test_fluxcast_diagnostics_are_discarded_instead_of_collected(self) -> None:
        def scanner(*, interface: str | None, timeout: int):
            print("diagnostic" * 100_000)
            return []

        payload = discovery_payload(FluxCastReceiverDiscovery(scanner=scanner))
        self.assertEqual(payload["receivers"], [])

    def test_live_wireless_labels_are_bounded_before_projection(self) -> None:
        def scanner(*, interface: str | None, timeout: int):
            del interface, timeout
            return [SimpleNamespace(
                address="02:00:00:00:00:01",
                name="Fire TV " + "x" * 100_000,
                details="wfd_dev_info=0x00111c4400c8",
            )]

        payload = discovery_payload(FluxCastReceiverDiscovery(scanner=scanner))
        self.assertEqual(len(payload["receivers"]), 1)
        self.assertLessEqual(len(payload["receivers"][0]["name"]), 120)
