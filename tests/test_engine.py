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
        plan = build_launch_plan(snapshot(), peer="tv-01", mode="mirror", profile="safe")
        self.assertTrue(plan["readOnly"])
        self.assertEqual(plan["selection"]["monitor"], "eDP-1")
        self.assertEqual(plan["selection"]["videoEncoder"], "vaapi")
        self.assertIn("1280x720", plan["command"])
        frequency_flag = plan["command"].index("--wfd-supplicant-frequency")
        self.assertEqual(plan["command"][frequency_flag + 1], "2412")
        self.assertEqual(plan["warnings"], [])

    def test_plan_warns_for_5ghz(self) -> None:
        host = snapshot()
        host["wifiLinks"] = [{"interface": "wlan42", "connected": True, "frequency_mhz": 5745}]
        plan = build_launch_plan(host, peer="tv-01", mode="mirror", profile="safe")
        self.assertEqual(plan["profile"]["fps"], 60)
        self.assertEqual(len(plan["warnings"]), 1)

    def test_display_source_uses_only_the_proven_capture_backend(self) -> None:
        plan = build_launch_plan(snapshot(), peer="tv-01", mode="mirror", profile="safe")
        self.assertEqual(plan["selection"]["source"], "display")
        backend_flag = plan["command"].index("--wfd-capture-backend")
        self.assertEqual(plan["command"][backend_flag + 1], "wf-recorder")
        self.assertNotIn("--wfd-portal-source", plan["command"])
        self.assertEqual(plan["profile"]["fps"], 60)

    def test_window_source_is_not_a_product_mode(self) -> None:
        with self.assertRaisesRegex(LaunchPlanError, "unsupported capture source"):
            build_launch_plan(snapshot(), peer="tv-01", mode="mirror", profile="safe", source="window")

    def test_plan_rejects_removed_modes_and_profiles(self) -> None:
        for mode, profile in (("extend", "safe"), ("mirror", "sports"), ("mirror", "balanced"), ("mirror", "smooth")):
            with self.assertRaises(LaunchPlanError):
                build_launch_plan(snapshot(), peer="tv-01", mode=mode, profile=profile)
        with self.assertRaises(LaunchPlanError):
            build_launch_plan(snapshot(), peer="tv-01", mode="mirror", profile="safe", source="region")

    def test_safe_profile_targets_receiver_proven_720p(self) -> None:
        plan = build_launch_plan(snapshot(), peer="tv-01", mode="mirror", profile="safe")
        self.assertEqual(plan["profile"]["fps"], 60)
        self.assertEqual(plan["profile"]["bitrateMbps"], 7)
        self.assertIn("1280x720", plan["command"])
        self.assertIn("60", plan["command"])
        self.assertIn("7M", plan["command"])

    def test_plan_rejects_missing_prerequisites(self) -> None:
        host = snapshot()
        host["checks"] = []
        with self.assertRaisesRegex(LaunchPlanError, "required tools unavailable"):
            build_launch_plan(host, peer="tv-01", mode="mirror", profile="safe")
