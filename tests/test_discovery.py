from __future__ import annotations

from pathlib import Path
import json
import tempfile
import unittest

from omarchy_cast.command import CommandResult
from omarchy_cast.discovery import ENGINE_CONTRACT, MAX_RENDER_NODES, _bounded_render_nodes, discover_host, parse_hyprland_monitors, parse_iw_link, parse_nmcli_devices


class DiscoveryTest(unittest.TestCase):
    @staticmethod
    def ready_runner(args, *, timeout=5.0):
        if args[0] == "fluxcast":
            return CommandResult(tuple(args), 0, json.dumps(ENGINE_CONTRACT), "")
        if Path(args[0]).name == "omarchy-cast-guard":
            return CommandResult(tuple(args), 0, json.dumps({"schemaVersion": 1, "kind": "omarchy-cast-guard-version", "apiRevision": 14}), "")
        if args[0] == "nmcli":
            return CommandResult(tuple(args), 0, "wlan42:wifi:connected\n", "")
        if args[0] == "iw":
            return CommandResult(tuple(args), 0, "Connected to aa:bb:cc:dd:ee:ff\n\tSSID: Example\n\tfreq: 2412\n", "")
        if args[0] == "hyprctl":
            return CommandResult(tuple(args), 0, '[{"name":"eDP-1","width":1920,"height":1080,"focused":true}]', "")
        if args[0] == "pactl":
            return CommandResult(tuple(args), 0, "alsa_output.example\n", "")
        return CommandResult(tuple(args), 0, "", "")

    @staticmethod
    def install_fake_helpers(root: Path) -> None:
        for name in ("omarchy-cast-guard", "omarchy-cast-guard-recover", "omarchy-cast-supplicant-broker"):
            path = root / name
            path.write_text("#!/bin/sh\n", encoding="utf-8")
            path.chmod(0o755)

    def test_parses_only_wifi_devices_without_hard_coding_names(self) -> None:
        devices = parse_nmcli_devices("wlan42:wifi:connected\np2p-dev-wlan42:wifi-p2p:disconnected\nlo:loopback:connected\n")
        self.assertEqual([(device.name, device.type) for device in devices], [("wlan42", "wifi"), ("p2p-dev-wlan42", "wifi-p2p")])

    def test_device_and_monitor_models_have_hard_count_and_string_limits(self) -> None:
        devices = parse_nmcli_devices("".join(f"wlan{index}:wifi:connected\n" for index in range(100)))
        monitors = parse_hyprland_monitors(json.dumps([
            {"name": f"DP-{index}", "description": "x" * 500, "width": 1920, "height": 1080}
            for index in range(100)
        ]))
        self.assertEqual(len(devices), 32)
        self.assertEqual(len(monitors), 16)
        self.assertEqual(len(monitors[0].description), 240)

    def test_parses_valid_hyprland_outputs(self) -> None:
        monitors = parse_hyprland_monitors('[{"name":"DP-2","description":"TV","width":1920,"height":1080,"refreshRate":60,"focused":true}]')
        self.assertEqual(monitors[0].name, "DP-2")
        self.assertEqual(monitors[0].width, 1920)
        self.assertTrue(monitors[0].focused)

    def test_monitor_numbers_require_exact_bounded_finite_types(self) -> None:
        monitors = parse_hyprland_monitors(json.dumps([
            {"name": "bool", "width": True, "height": 1080, "refreshRate": 60},
            {"name": "huge", "width": 16_385, "height": 1080, "refreshRate": 60},
            {"name": "refresh", "width": 1920, "height": 1080, "refreshRate": "invalid"},
        ]))
        self.assertEqual([monitor.name for monitor in monitors], ["refresh"])
        self.assertEqual(monitors[0].refresh_rate, 0.0)
        self.assertEqual(parse_hyprland_monitors(json.dumps([
            {"name": "nonfinite", "width": 1920, "height": 1080, "refreshRate": float("inf")},
        ])), [])
        self.assertEqual(parse_hyprland_monitors("[" * 2_000 + "0" + "]" * 2_000), [])

    def test_parses_connected_and_disconnected_wifi_link(self) -> None:
        connected = parse_iw_link("wlan42", "Connected to aa:bb:cc:dd:ee:ff (on wlan42)\n\tSSID: Example Network\n\tfreq: 2412.0\n")
        disconnected = parse_iw_link("wlan43", "Not connected.\n")
        self.assertEqual((connected.ssid, connected.frequency_mhz), ("Example Network", 2412))
        self.assertFalse(disconnected.connected)

    def test_wifi_frequency_rejects_wide_nonfinite_and_out_of_range_numbers(self) -> None:
        for candidate in ("9" * 10_000, "Infinity", "2299", "7126", "2412.0000"):
            with self.subTest(candidate=candidate[:20]):
                link = parse_iw_link("wlan42", f"Connected\n\tSSID: Example\n\tfreq: {candidate}\n")
                self.assertIsNone(link.frequency_mhz)

    def test_render_node_discovery_has_a_hard_result_ceiling(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for index in range(100):
                (root / f"renderD{index}").symlink_to("/dev/null")
            nodes = _bounded_render_nodes(root)
        self.assertEqual(len(nodes), MAX_RENDER_NODES)

    def test_reports_failed_probes_as_diagnostics(self) -> None:
        def runner(args, *, timeout=5.0):
            if args[0] == "nmcli":
                return CommandResult(tuple(args), 0, "wlan42:wifi:connected\n", "")
            if args[0] == "hyprctl":
                return CommandResult(tuple(args), 1, "", "Hyprland socket unavailable")
            return CommandResult(tuple(args), 0, "alsa_output.example\n", "")

        with tempfile.TemporaryDirectory() as temp:
            snapshot = discover_host(runner=runner, render_root=Path(temp))
        self.assertEqual(snapshot["wifiDevices"][0]["name"], "wlan42")
        self.assertEqual(snapshot["wifiLinks"][0]["interface"], "wlan42")
        self.assertEqual(snapshot["monitors"], [])
        self.assertEqual(snapshot["diagnostics"], [{"source": "Hyprland", "message": "Hyprland socket unavailable"}])

    def test_readiness_proves_the_complete_supported_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            helpers = root / "helpers"
            helpers.mkdir()
            self.install_fake_helpers(helpers)
            snapshot = discover_host(
                runner=self.ready_runner,
                render_root=root / "dri",
                guard_root=helpers,
                command_finder=lambda name: f"/usr/bin/{name}",
            )
        self.assertTrue(snapshot["readiness"]["ready"])
        self.assertFalse(snapshot["readiness"]["setupRequired"])
        self.assertEqual(snapshot["readiness"]["issues"], [])
        self.assertEqual(snapshot["readiness"]["summary"], "Casting support ready")
        guard = next(helper for helper in snapshot["helpers"] if helper["name"] == "omarchy-cast-guard")
        self.assertEqual(guard["apiRevision"], 14)

    def test_readiness_rejects_an_old_or_unversioned_companion(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            helpers = root / "helpers"
            helpers.mkdir()
            self.install_fake_helpers(helpers)

            def old_runner(args, *, timeout=5.0):
                if Path(args[0]).name == "omarchy-cast-guard":
                    return CommandResult(tuple(args), 2, "", "Usage: old helper")
                if args[0] == "fluxcast":
                    return CommandResult(tuple(args), 0, "--omacast-session --wfd-p2p-backend gpu-screen-recorder", "")
                return self.ready_runner(args, timeout=timeout)

            snapshot = discover_host(
                runner=old_runner,
                render_root=root / "dri",
                guard_root=helpers,
                command_finder=lambda name: f"/usr/bin/{name}",
            )

        self.assertFalse(snapshot["readiness"]["ready"])
        self.assertTrue(snapshot["readiness"]["setupRequired"])
        codes = {issue["code"] for issue in snapshot["readiness"]["issues"]}
        self.assertIn("engine-incompatible", codes)
        self.assertIn("helper-incompatible", codes)

    def test_readiness_rejects_old_alternate_capture_selectors(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            helpers = root / "helpers"
            helpers.mkdir()
            self.install_fake_helpers(helpers)

            def old_capture_runner(args, *, timeout=5.0):
                if args[0] == "fluxcast":
                    contract = dict(ENGINE_CONTRACT)
                    contract["capture"] = "portal"
                    return CommandResult(tuple(args), 0, json.dumps(contract), "")
                return self.ready_runner(args, timeout=timeout)

            snapshot = discover_host(
                runner=old_capture_runner,
                render_root=root / "dri",
                guard_root=helpers,
                command_finder=lambda name: f"/usr/bin/{name}",
            )

        self.assertFalse(snapshot["readiness"]["ready"])
        self.assertTrue(snapshot["readiness"]["setupRequired"])
        self.assertIn("engine-incompatible", {issue["code"] for issue in snapshot["readiness"]["issues"]})

    def test_readiness_rejects_help_token_false_positives_and_open_contracts(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            helpers = root / "helpers"
            helpers.mkdir()
            self.install_fake_helpers(helpers)

            for payload in (
                "--omacast-session --wfd-p2p-backend --wfd-supplicant-mode "
                "--wfd-video-encoder gpu-screen-recorder",
                json.dumps({**ENGINE_CONTRACT, "unexpected": True}),
                json.dumps({**ENGINE_CONTRACT, "apiRevision": True}),
                json.dumps({
                    **ENGINE_CONTRACT,
                    "profile": {**ENGINE_CONTRACT["profile"], "fps": 60.0},
                }),
                json.dumps({"nested": [[[[["too deep"]]]]]}),
                "[" * 2_000 + "0" + "]" * 2_000,
            ):
                with self.subTest(payload=payload[:40]):
                    def incompatible_runner(args, *, timeout=5.0):
                        if args[0] == "fluxcast":
                            return CommandResult(tuple(args), 0, payload, "")
                        return self.ready_runner(args, timeout=timeout)

                    snapshot = discover_host(
                        runner=incompatible_runner,
                        render_root=root / "dri",
                        guard_root=helpers,
                        command_finder=lambda name: f"/usr/bin/{name}",
                    )
                    self.assertFalse(snapshot["readiness"]["ready"])
                    self.assertIn(
                        "engine-incompatible",
                        {issue["code"] for issue in snapshot["readiness"]["issues"]},
                    )

    def test_readiness_rejects_recursively_deep_guard_version_json(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            helpers = root / "helpers"
            helpers.mkdir()
            self.install_fake_helpers(helpers)

            def deep_guard_runner(args, *, timeout=5.0):
                if Path(args[0]).name == "omarchy-cast-guard":
                    return CommandResult(tuple(args), 0, "[" * 2_000 + "0" + "]" * 2_000, "")
                return self.ready_runner(args, timeout=timeout)

            snapshot = discover_host(
                runner=deep_guard_runner,
                render_root=root / "dri",
                guard_root=helpers,
                command_finder=lambda name: f"/usr/bin/{name}",
            )
        guard = next(item for item in snapshot["helpers"] if item["name"] == "omarchy-cast-guard")
        self.assertEqual(guard["status"], "incompatible")

    def test_readiness_rejects_buggy_guard_api_ten(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            helpers = root / "helpers"
            helpers.mkdir()
            self.install_fake_helpers(helpers)

            def revision_ten_runner(args, *, timeout=5.0):
                if Path(args[0]).name == "omarchy-cast-guard":
                    return CommandResult(tuple(args), 0, json.dumps({
                        "schemaVersion": 1,
                        "kind": "omarchy-cast-guard-version",
                        "apiRevision": 10,
                    }), "")
                return self.ready_runner(args, timeout=timeout)

            snapshot = discover_host(
                runner=revision_ten_runner,
                render_root=root / "dri",
                guard_root=helpers,
                command_finder=lambda name: f"/usr/bin/{name}",
            )

        self.assertFalse(snapshot["readiness"]["ready"])
        self.assertTrue(snapshot["readiness"]["setupRequired"])
        self.assertIn("helper-incompatible", {issue["code"] for issue in snapshot["readiness"]["issues"]})

    def test_readiness_requires_a_closed_typed_guard_version_contract(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            helpers = root / "helpers"
            helpers.mkdir()
            self.install_fake_helpers(helpers)
            for payload in (
                {"schemaVersion": True, "kind": "omarchy-cast-guard-version", "apiRevision": 14},
                {"schemaVersion": 1, "kind": "omarchy-cast-guard-version", "apiRevision": 14, "extra": True},
            ):
                with self.subTest(payload=payload):
                    def incompatible_runner(args, *, timeout=5.0):
                        if Path(args[0]).name == "omarchy-cast-guard":
                            return CommandResult(tuple(args), 0, json.dumps(payload), "")
                        return self.ready_runner(args, timeout=timeout)

                    snapshot = discover_host(
                        runner=incompatible_runner,
                        render_root=root / "dri",
                        guard_root=helpers,
                        command_finder=lambda name: f"/usr/bin/{name}",
                    )
                    self.assertIn(
                        "helper-incompatible",
                        {issue["code"] for issue in snapshot["readiness"]["issues"]},
                    )

    def test_readiness_distinguishes_companion_setup_from_host_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            missing = discover_host(
                runner=lambda args, timeout=5.0: CommandResult(tuple(args), 127, "", "missing") if args[0] == "fluxcast" else self.ready_runner(args, timeout=timeout),
                render_root=root / "dri",
                guard_root=root / "helpers",
                command_finder=lambda name: None if name == "fluxcast" else f"/usr/bin/{name}",
            )
            helpers = root / "ready-helpers"
            helpers.mkdir()
            self.install_fake_helpers(helpers)
            disconnected = discover_host(
                runner=lambda args, timeout=5.0: CommandResult(tuple(args), 0, "", "") if args[0] == "nmcli" else self.ready_runner(args, timeout=timeout),
                render_root=root / "dri",
                guard_root=helpers,
                command_finder=lambda name: f"/usr/bin/{name}",
            )
        self.assertFalse(missing["readiness"]["ready"])
        self.assertTrue(missing["readiness"]["setupRequired"])
        self.assertIn("companion package", missing["readiness"]["summary"])
        self.assertFalse(disconnected["readiness"]["ready"])
        self.assertFalse(disconnected["readiness"]["setupRequired"])
        self.assertEqual(disconnected["readiness"]["summary"], "Connect to Wi-Fi before casting")
