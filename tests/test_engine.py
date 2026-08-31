from __future__ import annotations

import unittest

from omarchy_cast.engine import LaunchPlanError, build_launch_plan


def snapshot() -> dict[str, object]:
    return {
        "schemaVersion": 1,
        "checks": [{"name": name, "status": "ok"} for name in ("fluxcast", "nmcli", "gpu-screen-recorder", "ffmpeg")],
        "wifiLinks": [{"interface": "wlan42", "connected": True, "frequency_mhz": 2412}],
        "monitors": [{"name": "DP-2", "focused": False}, {"name": "eDP-1", "focused": True}],
        "defaultSink": "alsa_output.example",
        "renderNodes": ["/dev/dri/renderD128"],
    }


class EngineTest(unittest.TestCase):
    def test_plan_selects_focused_monitor_and_vaapi(self) -> None:
        plan = build_launch_plan(snapshot(), peer="aa:bb:cc:dd:ee:ff", mode="mirror", profile="safe")
        self.assertTrue(plan["readOnly"])
        self.assertEqual(plan["selection"]["monitor"], "eDP-1")
        self.assertEqual(plan["selection"]["videoEncoder"], "vaapi")
        self.assertIn("1280x720", plan["command"])
        frequency_flag = plan["command"].index("--wfd-supplicant-frequency")
        self.assertEqual(plan["command"][frequency_flag + 1], "2412")
        self.assertEqual(plan["selection"]["p2pFrequencyMhz"], 2412)
        self.assertEqual(plan["warnings"], [])
        self.assertEqual(plan["selection"]["peer"], "AA:BB:CC:DD:EE:FF")
        self.assertEqual(plan["selection"]["networkBackend"], "direct")

    def test_networkmanager_backend_is_explicit_and_keeps_the_brokered_engine_contract(self) -> None:
        plan = build_launch_plan(
            snapshot(), peer="AA:BB:CC:DD:EE:FF", mode="mirror",
            profile="safe", backend="networkmanager",
        )
        self.assertEqual(plan["selection"]["networkBackend"], "networkmanager")
        self.assertIn("group owner through NetworkManager", plan["warnings"][-1])
        backend_flag = plan["command"].index("--wfd-p2p-backend")
        self.assertEqual(plan["command"][backend_flag + 1], "supplicant")
        with self.assertRaisesRegex(LaunchPlanError, "backend"):
            build_launch_plan(
                snapshot(), peer="AA:BB:CC:DD:EE:FF", mode="mirror",
                profile="safe", backend="auto",
            )

    def test_plan_leaves_5ghz_and_dfs_p2p_channel_selection_automatic(self) -> None:
        for station_frequency in (5180, 5500, 5745, 5955):
            with self.subTest(station_frequency=station_frequency):
                host = snapshot()
                host["wifiLinks"] = [{"interface": "wlan42", "connected": True, "frequency_mhz": station_frequency}]
                plan = build_launch_plan(host, peer="AA:BB:CC:DD:EE:FF", mode="mirror", profile="safe")
                frequency_flag = plan["command"].index("--wfd-supplicant-frequency")
                self.assertEqual(plan["command"][frequency_flag + 1], "0")
                self.assertEqual(plan["selection"]["p2pFrequencyMhz"], 0)
                self.assertEqual(plan["profile"]["fps"], 60)
                self.assertEqual(len(plan["warnings"]), 1)

    def test_display_source_uses_only_the_proven_capture_backend(self) -> None:
        plan = build_launch_plan(snapshot(), peer="AA:BB:CC:DD:EE:FF", mode="mirror", profile="safe")
        self.assertEqual(plan["selection"]["source"], "display")
        backend_flag = plan["command"].index("--wfd-capture-backend")
        self.assertEqual(plan["command"][backend_flag + 1], "gpu-screen-recorder")
        self.assertNotIn("--wfd-portal-source", plan["command"])
        self.assertEqual(plan["profile"]["fps"], 60)

    def test_window_source_is_not_a_product_mode(self) -> None:
        with self.assertRaisesRegex(LaunchPlanError, "unsupported capture source"):
            build_launch_plan(snapshot(), peer="AA:BB:CC:DD:EE:FF", mode="mirror", profile="safe", source="window")

    def test_plan_rejects_removed_modes_and_profiles(self) -> None:
        for mode, profile in (("extend", "safe"), ("mirror", "sports"), ("mirror", "balanced"), ("mirror", "smooth")):
            with self.assertRaises(LaunchPlanError):
                build_launch_plan(snapshot(), peer="AA:BB:CC:DD:EE:FF", mode=mode, profile=profile)
        with self.assertRaises(LaunchPlanError):
            build_launch_plan(snapshot(), peer="AA:BB:CC:DD:EE:FF", mode="mirror", profile="safe", source="region")

    def test_plan_rejects_symbolic_or_malformed_receiver_ids(self) -> None:
        for peer in (
            "tv-01", "AA:BB:CC:DD:EE", "GG:BB:CC:DD:EE:FF",
            " AA:BB:CC:DD:EE:FF", "00:00:00:00:00:00",
            "FF:FF:FF:FF:FF:FF", "01:00:5E:00:00:01",
        ):
            with self.subTest(peer=peer), self.assertRaisesRegex(LaunchPlanError, "MAC address"):
                build_launch_plan(snapshot(), peer=peer, mode="mirror", profile="safe")

    def test_safe_profile_targets_receiver_proven_720p(self) -> None:
        plan = build_launch_plan(snapshot(), peer="AA:BB:CC:DD:EE:FF", mode="mirror", profile="safe")
        self.assertEqual(plan["profile"]["fps"], 60)
        self.assertEqual(plan["profile"]["bitrateMbps"], 7)
        self.assertIn("1280x720", plan["command"])
        self.assertIn("60", plan["command"])
        self.assertIn("7M", plan["command"])

    def test_plan_rejects_missing_prerequisites(self) -> None:
        host = snapshot()
        host["checks"] = []
        with self.assertRaisesRegex(LaunchPlanError, "required tools unavailable"):
            build_launch_plan(host, peer="AA:BB:CC:DD:EE:FF", mode="mirror", profile="safe")

    def test_plan_rejects_boolean_schema_revision(self) -> None:
        host = snapshot()
        host["schemaVersion"] = True
        with self.assertRaisesRegex(LaunchPlanError, "schema"):
            build_launch_plan(host, peer="AA:BB:CC:DD:EE:FF", mode="mirror", profile="safe")

    def test_plan_does_not_coerce_discovery_flags_or_numbers(self) -> None:
        host = snapshot()
        host["wifiLinks"] = [{"interface": "wlan42", "connected": 1, "frequency_mhz": 2412}]
        with self.assertRaisesRegex(LaunchPlanError, "connected managed Wi-Fi"):
            build_launch_plan(host, peer="AA:BB:CC:DD:EE:FF", mode="mirror", profile="safe")

        host = snapshot()
        host["monitors"] = [{"name": "DP-2", "focused": "yes"}, {"name": "eDP-1", "focused": True}]
        host["renderNodes"] = "/dev/dri/renderD128"
        host["wifiLinks"] = [{"interface": "wlan42", "connected": True, "frequency_mhz": True}]
        plan = build_launch_plan(host, peer="AA:BB:CC:DD:EE:FF", mode="mirror", profile="safe")
        self.assertEqual(plan["selection"]["monitor"], "eDP-1")
        self.assertEqual(plan["selection"]["videoEncoder"], "libx264")
        frequency_flag = plan["command"].index("--wfd-supplicant-frequency")
        self.assertEqual(plan["command"][frequency_flag + 1], "0")
