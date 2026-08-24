from __future__ import annotations

import io
import unittest
from pathlib import Path
import tempfile

from unittest.mock import Mock, patch

from omarchy_cast.transport import CAPTURE_START_TIMEOUT_SECONDS, CONNECT_TIMEOUT_SECONDS, GUARD_LEASE_SECONDS, RECEIVER_DISCONNECT_GRACE_SECONDS, SUPPLICANT_GROUP_TIMEOUT_SECONDS, DisabledTransportAdapter, FakeTransportAdapter, GuardedTransportAdapter, SessionLease, TransportDisabled, TransportError, validate_transport_plan


def plan() -> dict[str, object]:
    return {"schemaVersion": 1, "kind": "launch-plan", "readOnly": True, "execution": {"allowed": False}, "selection": {"source": "display"}, "command": ["fluxcast", "--protocol", "wfd", "--output-res", "1280x720", "--fps", "60", "--bitrate", "4M", "--wfd-p2p-backend", "supplicant", "--wfd-interface", "wlan42", "--wfd-peer", "tv-01", "--wfd-capture-backend", "wf-recorder", "--monitor", "eDP-1", "--wfd-audio-device", "sink.monitor", "--wfd-video-encoder", "vaapi", "--wfd-no-firewall"]}


class TransportTest(unittest.TestCase):
    def test_fake_transport_never_spawns_and_runs_ordered_stages(self) -> None:
        stages: list[str] = []
        adapter = FakeTransportAdapter()
        result = adapter.run(plan(), timeout_seconds=30, cancelled=lambda: False, stage=stages.append)
        self.assertEqual((result.status, stages, adapter.calls), ("completed", ["connecting", "streaming"], ["start", "cleanup"]))

    def test_fake_outcomes_cleanup_without_streaming(self) -> None:
        for scenario in ("timeout", "failure", "cancelled"):
            adapter = FakeTransportAdapter(scenario)
            stages: list[str] = []
            result = adapter.run(plan(), timeout_seconds=30, cancelled=lambda: False, stage=stages.append)
            self.assertTrue(result.cleanup_complete)
            self.assertNotIn("streaming", stages)

    def test_production_adapter_and_shell_like_arguments_are_refused(self) -> None:
        with self.assertRaises(TransportDisabled):
            DisabledTransportAdapter().run(plan(), timeout_seconds=30, cancelled=lambda: False, stage=lambda _: None)
        unsafe = plan()
        unsafe["command"] = list(unsafe["command"]) + ["; touch nope"]
        with self.assertRaisesRegex(TransportError, "shell-like"):
            validate_transport_plan(unsafe)

    def test_capture_backend_must_match_the_display_selection(self) -> None:
        mismatched_source = plan()
        mismatched_source["selection"] = {"source": "window"}
        with self.assertRaisesRegex(TransportError, "does not match"):
            validate_transport_plan(mismatched_source)

        mismatched_backend = plan()
        command = list(mismatched_backend["command"])
        command[command.index("--wfd-capture-backend") + 1] = "other"
        mismatched_backend["command"] = command
        with self.assertRaisesRegex(TransportError, "does not match"):
            validate_transport_plan(mismatched_backend)

    def test_rtsp_state_requires_an_established_7236_socket(self) -> None:
        sockets = "sl local_address rem_address st tx_queue rx_queue tr tm->when retrnsmt uid timeout inode\n  0: 00000000:1C44 00000000:0000 0A 0:0 0:0 0 0 0 111\n  1: C0A81B41:1C44 C0A81B72:A540 01 0:0 0:0 0 0 0 222\n"
        with patch("omarchy_cast.transport.Path.read_text", return_value=sockets), patch.object(GuardedTransportAdapter, "_socket_inodes", return_value={"222"}):
            self.assertTrue(GuardedTransportAdapter._rtsp_established(1234))
        with patch("omarchy_cast.transport.Path.read_text", return_value=sockets), patch.object(GuardedTransportAdapter, "_socket_inodes", return_value={"333"}):
            self.assertFalse(GuardedTransportAdapter._rtsp_established(1234))

    def test_receiver_negotiation_has_a_bounded_stage_deadline(self) -> None:
        self.assertEqual(CONNECT_TIMEOUT_SECONDS, 75)
        self.assertEqual(CAPTURE_START_TIMEOUT_SECONDS, 30)
        self.assertEqual(SUPPLICANT_GROUP_TIMEOUT_SECONDS, 45)
        self.assertEqual(RECEIVER_DISCONNECT_GRACE_SECONDS, 3)

    def test_receiver_liveness_requires_the_session_p2p_group(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            network_root = Path(directory)
            self.assertFalse(GuardedTransportAdapter._p2p_group_present("wlan42", network_root))
            (network_root / "p2p-wlan42-7").mkdir()
            self.assertTrue(GuardedTransportAdapter._p2p_group_present("wlan42", network_root))
            self.assertFalse(GuardedTransportAdapter._p2p_group_present("wlan43", network_root))

    def test_streaming_requires_a_completed_video_progress_record(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            progress = Path(directory) / "progress"
            self.assertFalse(GuardedTransportAdapter._media_started(progress))
            progress.write_text("frame=0\nprogress=continue\n", encoding="utf-8")
            self.assertFalse(GuardedTransportAdapter._media_started(progress))
            progress.write_text("frame=0\nprogress=continue\nframe=1\nout_time_ms=33333\nprogress=continue\n", encoding="utf-8")
            self.assertTrue(GuardedTransportAdapter._media_started(progress))

    def test_group_timeout_is_not_coupled_to_session_duration(self) -> None:
        from omarchy_cast.guard import GuardRequest
        executable = plan()
        executable["execution"] = {"allowed": True}
        paths = {name: Path("/run/user/1000/omarchy-cast") / name for name in ("progress", "latency", "packets")}
        adapter = GuardedTransportAdapter(GuardRequest(1, "a" * 32, 1000, "wlan42", 60), env={})
        command = adapter._engine_command(executable, paths, "/run/user/1000/omarchy-cast/session/trigger")
        self.assertEqual(command[command.index("--wfd-supplicant-hold") + 1], "45")

    def test_session_lease_is_private_and_renewable(self) -> None:
        self.assertEqual(GUARD_LEASE_SECONDS, 60)
        with tempfile.TemporaryDirectory() as directory:
            heartbeat = Path(directory) / "heartbeat"
            lease = SessionLease(heartbeat, interval_seconds=0.01)
            lease.start()
            lease.stop()
            self.assertRegex(heartbeat.read_text(encoding="ascii"), r"^[0-9]+\n$")
            self.assertEqual(heartbeat.stat().st_mode & 0o777, 0o600)

    def test_engine_detail_is_bounded_to_recent_lines(self) -> None:
        detail = GuardedTransportAdapter._bounded_detail("first\nsecond\nthird\nfourth\nfifth\n", "fallback")
        self.assertEqual(detail, "second | third | fourth | fifth")
        self.assertEqual(GuardedTransportAdapter._bounded_detail(None, "fallback"), "fallback")

    def test_guard_stop_uses_the_unprivileged_session_marker(self) -> None:
        from omarchy_cast.guard import GuardRequest
        adapter = GuardedTransportAdapter(GuardRequest(1, "a" * 32, 1000, "wlan42", 60))
        with patch("omarchy_cast.transport.os.open", return_value=42) as open_marker, patch("omarchy_cast.transport.os.close") as close_marker:
            adapter._stop_guard()
        self.assertEqual(open_marker.call_args.args[0], "/run/user/1000/omarchy-cast/" + "a" * 32 + "/stop")
        close_marker.assert_called_once_with(42)

    def test_authorization_wait_observes_stop_without_touching_the_guard(self) -> None:
        from omarchy_cast.guard import GuardRequest
        request = GuardRequest(1, "a" * 32, 1000, "wlan42", 60)
        process = Mock(stdout=Mock())
        self.assertIsNone(GuardedTransportAdapter._read_ready(process, request, lambda: True))
        process.stdout.readline.assert_not_called()

    def test_dismissed_authorization_is_an_actionable_no_change_failure(self) -> None:
        from omarchy_cast.guard import GuardRequest
        request = GuardRequest(1, "a" * 32, 1000, "wlan42", 60)
        process = Mock(
            stdout=io.StringIO(""),
            stderr=io.StringIO("Error executing command as another user: Request dismissed"),
            returncode=126,
        )
        process.poll.return_value = 126
        with patch("omarchy_cast.transport.select.select", return_value=((process.stdout,), (), ())):
            with self.assertRaises(TransportError) as caught:
                GuardedTransportAdapter._read_ready(process, request, lambda: False)
        self.assertEqual(caught.exception.code, "authorization-cancelled")
        self.assertIn("Nothing was changed", str(caught.exception))

    def test_root_owned_helper_cleanup_does_not_mask_setup_failure(self) -> None:
        from omarchy_cast.guard import GuardRequest
        request = GuardRequest(1, "a" * 32, 1000, "wlan42", 60)
        helper = Mock()
        helper.poll.return_value = None
        helper.terminate.side_effect = PermissionError(1, "Operation not permitted")
        helper.wait.return_value = 2
        executable = plan()
        executable["execution"] = {"allowed": True}
        adapter = GuardedTransportAdapter(request, env={})
        with (
            patch("omarchy_cast.transport.subprocess.Popen", return_value=helper),
            patch.object(adapter, "_read_ready", side_effect=TransportError("network helper refused", code="guard-setup-failed")),
            patch.object(adapter, "_stop_guard") as stop_guard,
            patch("omarchy_cast.transport.cleanup_live_telemetry"),
        ):
            with self.assertRaisesRegex(TransportError, "network helper refused") as caught:
                adapter.run(executable, timeout_seconds=None, cancelled=lambda: False, stage=lambda _: None)
        self.assertEqual(caught.exception.code, "guard-setup-failed")
        stop_guard.assert_called_once_with()
        helper.terminate.assert_called_once_with()

    def test_engine_failures_have_stable_actionable_codes(self) -> None:
        cases = {
            "DHCP client did not obtain an IP address": "dhcp-failed",
            "wpa_supplicant P2P group formation failed": "p2p-negotiation-failed",
            "Waiting for DHCP before timed out waiting for a direct supplicant P2P group": "p2p-negotiation-failed",
            "RTSP Miracast negotiation refused": "receiver-negotiation-failed",
            "GPU Screen Recorder capture encoder failed": "capture-failed",
            "FluxCast exited with status 1": "engine-exited",
        }
        for detail, expected in cases.items():
            with self.subTest(detail=detail):
                self.assertEqual(GuardedTransportAdapter._failure_code(detail), expected)

        long_log = "DHCP failed\n" + "\n".join(f"cleanup line {index}" for index in range(10))
        self.assertNotIn("DHCP", GuardedTransportAdapter._bounded_detail(long_log, "fallback"))
        self.assertEqual(GuardedTransportAdapter._failure_code(long_log), "dhcp-failed")
