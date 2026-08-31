from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import io
import json
import tempfile
import unittest
from unittest.mock import patch

from omarchy_cast.cli import MAX_STREAMED_SCAN_SNAPSHOTS, _emit, build_parser, main
from omarchy_cast.guard import GuardError
from omarchy_cast.receivers import FixtureReceiverDiscovery


class CliTest(unittest.TestCase):
    def invoke(self, arguments: list[str]) -> tuple[int, dict[str, object]]:
        output = io.StringIO()
        with redirect_stdout(output):
            code = main(arguments)
        return code, json.loads(output.getvalue())

    def test_status_without_state_is_idle(self) -> None:
        with tempfile.TemporaryDirectory() as temp, patch.dict("os.environ", {"XDG_RUNTIME_DIR": temp}, clear=False):
            with patch("omarchy_cast.cli.session_lock_is_held", return_value=True):
                code, payload = self.invoke(["status"])
        self.assertEqual(code, 0)
        self.assertEqual(payload["phase"], "idle")

    def test_ui_response_has_a_global_byte_ceiling(self) -> None:
        output = io.StringIO()
        with patch("omarchy_cast.cli.MAX_UI_RESPONSE_BYTES", 128), redirect_stdout(output):
            _emit({"schemaVersion": 1, "items": ["x" * 1_000]})
        payload = json.loads(output.getvalue())
        self.assertEqual(payload["error"]["code"], "response-too-large")

    def test_hardware_cast_defaults_to_until_stopped(self) -> None:
        self.assertEqual(build_parser().parse_args(["start", "--peer", "tv-01"]).duration, 0)
        self.assertEqual(build_parser().parse_args(["connect", "--peer", "tv-01"]).duration, 0)
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            build_parser().parse_args(["start", "--peer", "tv-01", "--source", "window"])

    def test_numeric_arguments_are_lexically_and_numerically_bounded(self) -> None:
        parser = build_parser()
        for arguments in (
            ["scan", "--timeout", "0"],
            ["scan", "--timeout", "9" * 100_000],
            ["start", "--peer", "tv-01", "--duration", "86401"],
            ["connect", "--peer", "tv-01", "--duration", "1e3"],
            ["logs", "--limit", "51"],
        ):
            with self.subTest(arguments=arguments), redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                parser.parse_args(arguments)

    def test_executable_session_surface_exposes_only_accepted_mode_and_profile(self) -> None:
        parser = build_parser()
        self.assertEqual(parser.parse_args(["connect", "--peer", "tv-01"]).mode, "mirror")
        self.assertEqual(parser.parse_args(["connect", "--peer", "tv-01"]).profile, "safe")
        for arguments in (
            ["connect", "--peer", "tv-01", "--mode", "extend"],
            ["connect", "--peer", "tv-01", "--profile", "sports"],
        ):
            with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                parser.parse_args(arguments)

    def test_hidden_simulation_keeps_a_bounded_default(self) -> None:
        with tempfile.TemporaryDirectory() as temp, patch.dict("os.environ", {"XDG_RUNTIME_DIR": temp, "XDG_STATE_HOME": temp}, clear=False):
            code = main(["connect", "--peer", "simulator", "--simulate"])
            self.assertEqual(code, 0)

    def test_status_includes_live_telemetry_for_its_active_session(self) -> None:
        with tempfile.TemporaryDirectory() as temp, patch.dict("os.environ", {"XDG_RUNTIME_DIR": temp, "XDG_STATE_HOME": temp}, clear=False):
            from omarchy_cast.state import idle_state, transition, write_state
            from omarchy_cast.telemetry import telemetry_paths
            session_id = "a" * 32
            write_state(transition(idle_state(), "checking", sessionId=session_id))
            paths = telemetry_paths(session_id)
            paths["current"].write_text(json.dumps({"schemaVersion": 1, "sessionId": session_id, "health": {"status": "warming"}}))
            paths["current"].chmod(0o600)
            code, payload = self.invoke(["status"])
        self.assertEqual(code, 0)
        self.assertEqual(payload["telemetry"]["health"]["status"], "warming")

    def test_status_turns_an_unowned_active_state_into_recovery(self) -> None:
        with tempfile.TemporaryDirectory() as temp, patch.dict("os.environ", {"XDG_RUNTIME_DIR": temp}, clear=False):
            from omarchy_cast.state import idle_state, transition, write_state
            write_state(transition(idle_state(), "checking", sessionId="a" * 32))
            code, payload = self.invoke(["status"])
        self.assertEqual(code, 0)
        self.assertEqual(payload["phase"], "error")
        self.assertEqual(payload["error"]["code"], "session-owner-lost")

    def test_plan_is_a_read_only_preview(self) -> None:
        host = {
            "schemaVersion": 1,
            "checks": [{"name": name, "status": "ok"} for name in ("fluxcast", "nmcli", "gpu-screen-recorder", "ffmpeg")],
            "wifiLinks": [{"interface": "wlan42", "connected": True, "frequency_mhz": 2412}],
            "monitors": [{"name": "eDP-1", "focused": True}],
            "defaultSink": "alsa_output.example",
            "renderNodes": [],
        }
        with patch("omarchy_cast.cli.discover_host", return_value=host):
            code, payload = self.invoke(["plan", "--peer", "AA:BB:CC:DD:EE:FF", "--profile", "safe"])
        self.assertEqual(code, 0)
        self.assertTrue(payload["readOnly"])
        self.assertEqual(payload["selection"]["monitor"], "eDP-1")

    def test_dry_run_never_executes_the_preview_command(self) -> None:
        host = {
            "schemaVersion": 1,
            "checks": [{"name": name, "status": "ok"} for name in ("fluxcast", "nmcli", "gpu-screen-recorder", "ffmpeg")],
            "wifiLinks": [{"interface": "wlan42", "connected": True, "frequency_mhz": 2412}],
            "monitors": [{"name": "eDP-1", "focused": True}],
            "defaultSink": "alsa_output.example", "renderNodes": [],
        }
        with tempfile.TemporaryDirectory() as temp, patch.dict("os.environ", {
            "XDG_RUNTIME_DIR": temp, "XDG_STATE_HOME": temp,
        }, clear=False), patch("omarchy_cast.cli.discover_host", return_value=host):
            code, payload = self.invoke(["dry-run", "--peer", "AA:BB:CC:DD:EE:FF"])
        self.assertEqual(code, 0)
        self.assertTrue(payload["dryRun"])

    def test_connect_passes_the_canonical_p2p_frequency_to_the_guard(self) -> None:
        base_host = {
            "schemaVersion": 1,
            "checks": [{"name": name, "status": "ok"} for name in ("fluxcast", "nmcli", "gpu-screen-recorder", "ffmpeg")],
            "monitors": [{"name": "eDP-1", "focused": True}],
            "defaultSink": "alsa_output.example",
            "renderNodes": ["/dev/dri/renderD128"],
        }
        for station_frequency, expected_p2p_frequency in ((2412, 2412), (5745, 0)):
            with self.subTest(station_frequency=station_frequency):
                host = {**base_host, "wifiLinks": [{
                    "interface": "wlan42", "connected": True,
                    "frequency_mhz": station_frequency,
                }]}
                completed = {"schemaVersion": 1, "ok": True, "transport": {"status": "completed"}}
                with patch("omarchy_cast.cli.discover_host", return_value=host), patch(
                    "omarchy_cast.cli.GuardedTransportAdapter"
                ) as adapter, patch("omarchy_cast.cli.TransportTestSupervisor") as supervisor:
                    supervisor.return_value.run.return_value = completed
                    code, payload = self.invoke(["connect", "--peer", "AA:BB:CC:DD:EE:FF"])

                self.assertEqual((code, payload), (0, completed))
                request = adapter.call_args.args[0]
                self.assertEqual(request.frequency_mhz, expected_p2p_frequency)

    def test_receiver_fixture_and_live_scan_share_the_ui_contract(self) -> None:
        code, payload = self.invoke(["receivers", "--fixture"])
        self.assertEqual(code, 0)
        self.assertEqual(payload["receivers"][0]["id"], "02:00:00:00:00:FE")
        live = FixtureReceiverDiscovery([{"id": "AA:BB:CC:DD:EE:FF", "name": "Living room TV", "kind": "wfd-display", "capabilities": ["miracast", "audio", "video"]}])
        with patch("omarchy_cast.cli.read_state", return_value={"phase": "idle"}), patch("omarchy_cast.cli.discover_host", return_value={"wifiLinks": [{"interface": "wlan42", "connected": True}]}), patch("omarchy_cast.cli.FluxCastReceiverDiscovery", return_value=live):
            code, payload = self.invoke(["scan", "--timeout", "4"])
        self.assertEqual(code, 0)
        self.assertEqual(payload["receivers"][0]["name"], "Living room TV")

    def test_streaming_scan_flushes_each_changed_receiver_snapshot(self) -> None:
        receiver = FixtureReceiverDiscovery([
            {"id": "AA:BB:CC:DD:EE:FF", "name": "Living room TV", "kind": "wfd-display", "capabilities": ["miracast"]}
        ]).list_receivers(timeout_seconds=1)[0]

        class ProgressiveDiscovery:
            def watch_receivers(self, *, timeout_seconds, callback):
                self.timeout_seconds = timeout_seconds
                callback([])
                callback([receiver])
                return [receiver]

        discovery = ProgressiveDiscovery()
        output = io.StringIO()
        with patch("omarchy_cast.cli.read_state", return_value={"phase": "idle"}), patch(
            "omarchy_cast.cli.discover_host", return_value={"wifiLinks": []}
        ), patch("omarchy_cast.cli.FluxCastReceiverDiscovery", return_value=discovery), redirect_stdout(output):
            code = main(["scan", "--timeout", "4", "--stream"])

        lines = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual(code, 0)
        self.assertEqual(discovery.timeout_seconds, 4)
        self.assertEqual([len(line["receivers"]) for line in lines], [0, 1])

    def test_streaming_scan_stops_after_its_snapshot_budget(self) -> None:
        class FloodedDiscovery:
            def watch_receivers(self, *, timeout_seconds, callback):
                del timeout_seconds
                for _ in range(MAX_STREAMED_SCAN_SNAPSHOTS + 1):
                    callback([])
                return []

        output = io.StringIO()
        with patch("omarchy_cast.cli.read_state", return_value={"phase": "idle"}), patch(
            "omarchy_cast.cli.discover_host", return_value={"wifiLinks": []}
        ), patch("omarchy_cast.cli.FluxCastReceiverDiscovery", return_value=FloodedDiscovery()), redirect_stdout(output):
            code = main(["scan", "--timeout", "4", "--stream"])

        lines = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual(code, 2)
        self.assertEqual(len(lines), MAX_STREAMED_SCAN_SNAPSHOTS + 1)
        self.assertEqual(lines[-1]["error"]["code"], "receiver-scan-failed")

    def test_logs_is_empty_before_any_session(self) -> None:
        with tempfile.TemporaryDirectory() as temp, patch.dict("os.environ", {"XDG_RUNTIME_DIR": temp, "XDG_STATE_HOME": temp}, clear=False):
            code, payload = self.invoke(["logs"])
        self.assertEqual(code, 0)
        self.assertEqual(payload["sessions"], [])

    def test_transport_test_uses_only_the_fake_adapter(self) -> None:
        host = {"schemaVersion": 1, "checks": [{"name": name, "status": "ok"} for name in ("fluxcast", "nmcli", "gpu-screen-recorder", "ffmpeg")], "wifiLinks": [{"interface": "wlan42", "connected": True, "frequency_mhz": 2412}], "monitors": [{"name": "eDP-1", "focused": True}], "defaultSink": "alsa_output.example", "renderNodes": []}
        with tempfile.TemporaryDirectory() as temp, patch.dict("os.environ", {
            "XDG_CONFIG_HOME": temp, "XDG_RUNTIME_DIR": temp, "XDG_STATE_HOME": temp,
        }, clear=False), patch("omarchy_cast.cli.discover_host", return_value=host):
            code, payload = self.invoke(["transport-test", "--peer", "AA:BB:CC:DD:EE:FF", "--scenario", "success"])
        self.assertEqual(code, 0)
        self.assertTrue(payload["transportTest"])
        self.assertEqual(payload["transport"]["status"], "completed")

    def test_protocol_fixture_command_is_offline_and_distinguishes_outcomes(self) -> None:
        code, success = self.invoke(["protocol-test"])
        timeout_code, timeout = self.invoke(["protocol-test", "--scenario", "timeout"])
        self.assertEqual((code, timeout_code), (0, 0))
        self.assertTrue(success["offline"])
        self.assertEqual(success["result"]["status"], "completed")
        self.assertEqual(timeout["result"]["status"], "timeout")

    def test_media_probe_reports_only_the_injected_local_result(self) -> None:
        with patch("omarchy_cast.cli.discover_host", return_value={"schemaVersion": 1}), patch("omarchy_cast.cli.probe_media", return_value={"schemaVersion": 1, "ok": True, "encoder": "vaapi", "frames": 30}):
            code, payload = self.invoke(["media-probe", "--profile", "safe"])
        self.assertEqual(code, 0)
        self.assertEqual((payload["encoder"], payload["frames"]), ("vaapi", 30))

    def test_start_delegates_the_cast_to_the_user_service(self) -> None:
        started = {"schemaVersion": 1, "ok": True, "phase": "starting", "unit": "omacast-session.service"}
        with patch("omarchy_cast.cli.read_state", return_value={"phase": "idle"}), patch("omarchy_cast.cli.start_session_service", return_value=started) as launch:
            code, payload = self.invoke(["start", "--peer", "AA:BB:CC:DD:EE:FF"])
        self.assertEqual(code, 0)
        self.assertEqual(payload, started)
        self.assertEqual(launch.call_args.kwargs["mode"], "mirror")
        self.assertEqual(launch.call_args.kwargs["profile"], "safe")
        self.assertEqual(launch.call_args.kwargs["backend"], "direct")
        self.assertNotIn("source", launch.call_args.kwargs)

        with patch("omarchy_cast.cli.read_state", return_value={"phase": "idle"}), patch(
            "omarchy_cast.cli.start_session_service", return_value=started
        ) as compatibility_launch:
            code, _payload = self.invoke([
                "start", "--peer", "AA:BB:CC:DD:EE:FF",
                "--backend", "networkmanager",
            ])
        self.assertEqual(code, 0)
        self.assertEqual(compatibility_launch.call_args.kwargs["backend"], "networkmanager")

    def test_start_reads_pairing_pin_from_stdin_not_arguments(self) -> None:
        from types import SimpleNamespace

        started = {"schemaVersion": 1, "ok": True, "phase": "starting", "unit": "omacast-session.service"}
        stdin = SimpleNamespace(buffer=io.BytesIO(b"12345670\n"))
        with patch("omarchy_cast.cli.read_state", return_value={"phase": "idle"}), patch(
            "omarchy_cast.cli.start_session_service", return_value=started
        ) as launch, patch("omarchy_cast.cli.sys.stdin", stdin):
            code, payload = self.invoke([
                "start", "--peer", "AA:BB:CC:DD:EE:FF", "--pairing-pin-stdin"
            ])
        self.assertEqual(code, 0)
        self.assertEqual(payload, started)
        self.assertEqual(launch.call_args.kwargs["pairing_pin"], b"12345670")

    def test_start_rejects_invalid_pairing_pin_before_service_launch(self) -> None:
        from types import SimpleNamespace

        stdin = SimpleNamespace(buffer=io.BytesIO(b"12345671\n"))
        with patch("omarchy_cast.cli.read_state", return_value={"phase": "idle"}), patch(
            "omarchy_cast.cli.start_session_service"
        ) as launch, patch("omarchy_cast.cli.sys.stdin", stdin):
            code, payload = self.invoke([
                "start", "--peer", "AA:BB:CC:DD:EE:FF", "--pairing-pin-stdin"
            ])
        self.assertEqual(code, 1)
        self.assertEqual(payload["error"]["code"], "session-start-failed")
        launch.assert_not_called()

    def test_start_refuses_a_non_idle_session(self) -> None:
        with patch("omarchy_cast.cli.read_state", return_value={"phase": "error"}), patch("omarchy_cast.cli.start_session_service") as launch:
            code, payload = self.invoke(["start", "--peer", "tv-01"])
        self.assertEqual(code, 1)
        self.assertEqual(payload["error"]["code"], "session-start-failed")
        launch.assert_not_called()

    def test_start_rejects_a_symbolic_receiver_before_spawning_a_service(self) -> None:
        with patch("omarchy_cast.cli.read_state", return_value={"phase": "idle"}), patch(
            "omarchy_cast.cli.start_session_service"
        ) as launch:
            code, payload = self.invoke(["start", "--peer", "tv-01"])
        self.assertEqual(code, 1)
        self.assertIn("MAC address", payload["error"]["message"])
        launch.assert_not_called()

    def test_stop_cancels_a_pending_service_while_runtime_state_is_idle(self) -> None:
        cancelled = {"schemaVersion": 1, "ok": True, "phase": "idle", "reason": "launch-cancelled"}
        recovered = {"schemaVersion": 1, "ok": True, "recovered": True}
        with patch("omarchy_cast.cli.read_state", return_value={"phase": "idle"}), patch("omarchy_cast.cli.stop_pending_session_service", return_value=cancelled) as stop_service, patch("omarchy_cast.cli.recover_stale_session", return_value=recovered) as recover, patch("omarchy_cast.cli.request_stop") as stop_session:
            code, payload = self.invoke(["stop"])
        self.assertEqual(code, 0)
        self.assertTrue(payload["recovered"])
        stop_service.assert_called_once_with()
        recover.assert_called_once_with()
        stop_session.assert_not_called()

    def test_stop_remains_cooperative_after_session_state_is_active(self) -> None:
        stopped = {"schemaVersion": 1, "ok": True, "phase": "connecting"}
        with patch("omarchy_cast.cli.read_state", return_value={"phase": "connecting"}), patch("omarchy_cast.cli.request_stop", return_value=stopped) as stop_session, patch("omarchy_cast.cli.stop_pending_session_service") as stop_service, patch("omarchy_cast.cli.recover_stale_session") as recover:
            code, payload = self.invoke(["stop"])
        self.assertEqual(code, 0)
        self.assertEqual(payload, stopped)
        stop_session.assert_called_once_with()
        stop_service.assert_not_called()
        recover.assert_not_called()

    def test_recover_reclaims_detected_p2p_orphans_through_the_fixed_helper(self) -> None:
        local = {"schemaVersion": 1, "ok": True, "recovered": True}
        host = {"wifiDevicesComplete": True, "wifiLinks": [
            {"interface": "wlan42", "connected": True},
            {"interface": "wlan43", "connected": False},
            {"interface": "wlan44", "connected": True},
        ]}
        with patch("omarchy_cast.cli.recover_stale_session", return_value=local), patch(
            "omarchy_cast.cli.discover_host", return_value=host
        ), patch("omarchy_cast.cli.orphan_parent_interfaces", return_value=("wlan43", "wlan44")) as probe, patch(
            "omarchy_cast.cli.reclaim_orphan_interfaces",
            return_value={"schemaVersion": 1, "kind": "omarchy-cast-guard-reclaim-status", "ok": True, "reclaimed": 2},
        ) as reclaim:
            code, payload = self.invoke(["recover"])
        self.assertEqual(code, 0)
        self.assertEqual(payload["reclaimedP2pInterfaces"], 4)
        probe.assert_called_once_with(["wlan42", "wlan43", "wlan44"])
        self.assertEqual([call.args for call in reclaim.call_args_list], [("wlan43",), ("wlan44",)])

    def test_recover_does_not_prompt_when_no_orphan_is_present(self) -> None:
        local = {"schemaVersion": 1, "ok": True, "recovered": False}
        host = {"wifiDevicesComplete": True, "wifiLinks": [{"interface": "wlan42", "connected": True}]}
        with patch("omarchy_cast.cli.recover_stale_session", return_value=local), patch(
            "omarchy_cast.cli.discover_host", return_value=host
        ), patch("omarchy_cast.cli.orphan_parent_interfaces", return_value=()), patch(
            "omarchy_cast.cli.reclaim_orphan_interfaces"
        ) as reclaim:
            code, payload = self.invoke(["recover"])
        self.assertEqual(code, 0)
        self.assertEqual(payload["reclaimedP2pInterfaces"], 0)
        reclaim.assert_not_called()

    def test_recover_refuses_an_incomplete_wifi_inventory(self) -> None:
        with patch(
            "omarchy_cast.cli.discover_host",
            return_value={"wifiDevicesComplete": False, "wifiLinks": []},
        ), patch("omarchy_cast.cli.orphan_parent_interfaces") as probe, patch(
            "omarchy_cast.cli.recover_stale_session"
        ) as local_recover:
            code, payload = self.invoke(["recover"])
        self.assertEqual(code, 1)
        self.assertEqual(payload["error"]["code"], "recovery-unavailable")
        probe.assert_not_called()
        local_recover.assert_not_called()

    def test_failed_orphan_reclaim_preserves_recoverable_user_state(self) -> None:
        host = {"wifiDevicesComplete": True, "wifiLinks": [{"interface": "wlan42", "connected": True}]}
        with patch("omarchy_cast.cli.discover_host", return_value=host), patch(
            "omarchy_cast.cli.orphan_parent_interfaces", return_value=("wlan42",)
        ), patch(
            "omarchy_cast.cli.reclaim_orphan_interfaces", side_effect=GuardError("client is still connected")
        ), patch("omarchy_cast.cli.recover_stale_session") as local_recover:
            code, payload = self.invoke(["recover"])
        self.assertEqual(code, 1)
        self.assertEqual(payload["error"]["code"], "recovery-unavailable")
        local_recover.assert_not_called()
