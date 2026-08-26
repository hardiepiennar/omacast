from __future__ import annotations

from pathlib import Path
import json
import tempfile
import unittest

from omarchy_cast.command import CommandResult
from omarchy_cast.discovery import discover_host, parse_hyprland_monitors, parse_iw_link, parse_nmcli_devices


class DiscoveryTest(unittest.TestCase):
    @staticmethod
    def ready_runner(args, *, timeout=5.0):
        if args[0] == "fluxcast":
            return CommandResult(tuple(args), 0, "--wfd-p2p-backend --wfd-supplicant-mode --wfd-video-encoder --wfd-supplicant-network-trigger --wfd-progress-log", "")
        if Path(args[0]).name == "omarchy-cast-guard":
            return CommandResult(tuple(args), 0, json.dumps({"schemaVersion": 1, "kind": "omarchy-cast-guard-version", "apiRevision": 9}), "")
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
        for name in ("omarchy-cast-guard", "omarchy-cast-guard-recover"):
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

    def test_parses_connected_and_disconnected_wifi_link(self) -> None:
        connected = parse_iw_link("wlan42", "Connected to aa:bb:cc:dd:ee:ff (on wlan42)\n\tSSID: Example Network\n\tfreq: 2412.0\n")
        disconnected = parse_iw_link("wlan43", "Not connected.\n")
        self.assertEqual((connected.ssid, connected.frequency_mhz), ("Example Network", 2412))
        self.assertFalse(disconnected.connected)

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
        self.assertEqual(guard["apiRevision"], 9)

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
                    return CommandResult(tuple(args), 0, "--wfd-p2p-backend --wfd-supplicant-mode --wfd-video-encoder", "")
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

    def test_readiness_rejects_guard_api_eight(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            helpers = root / "helpers"
            helpers.mkdir()
            self.install_fake_helpers(helpers)

            def revision_eight_runner(args, *, timeout=5.0):
                if Path(args[0]).name == "omarchy-cast-guard":
                    return CommandResult(tuple(args), 0, json.dumps({
                        "schemaVersion": 1,
                        "kind": "omarchy-cast-guard-version",
                        "apiRevision": 8,
                    }), "")
                return self.ready_runner(args, timeout=timeout)

            snapshot = discover_host(
                runner=revision_eight_runner,
                render_root=root / "dri",
                guard_root=helpers,
                command_finder=lambda name: f"/usr/bin/{name}",
            )

        self.assertFalse(snapshot["readiness"]["ready"])
        self.assertTrue(snapshot["readiness"]["setupRequired"])
        self.assertIn("helper-incompatible", {issue["code"] for issue in snapshot["readiness"]["issues"]})

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
