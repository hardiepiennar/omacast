from __future__ import annotations

import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from omarchy_cast.session import MAX_SESSION_LOGS, DryRunSupervisor, SessionError, SimulatedSupervisor, TransportTestSupervisor, append_event, event_log_path, read_session_events, recover_stale_session, request_stop, session_history, validate_request
from omarchy_cast.state import idle_state, read_state, transition, write_state
from omarchy_cast.transport import FakeTransportAdapter, TransportError, TransportResult


class SessionTest(unittest.TestCase):
    def test_new_session_prunes_history_to_the_bounded_limit(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            environment = {"XDG_RUNTIME_DIR": temp, "XDG_STATE_HOME": temp}
            telemetry = Path(temp) / "omarchy-cast" / "telemetry"
            telemetry.mkdir(parents=True)
            for index in range(MAX_SESSION_LOGS + 4):
                session_id = f"{index:032x}"
                (telemetry / f"{session_id}.jsonl").write_text("{}\n")
                append_event(session_id, "session-started", environment)
            directory = Path(temp) / "omarchy-cast" / "sessions"
            self.assertEqual(len(list(directory.glob("*.jsonl"))), MAX_SESSION_LOGS)
            self.assertEqual(len(list(telemetry.glob("*.jsonl"))), MAX_SESSION_LOGS)

    def environment(self, root: str) -> dict[str, str]:
        runtime = Path(root) / "runtime"
        runtime.mkdir(mode=0o700)
        return {"XDG_RUNTIME_DIR": str(runtime), "XDG_STATE_HOME": str(Path(root) / "state")}

    def test_request_validation_rejects_unsafe_peer(self) -> None:
        with self.assertRaises(SessionError):
            validate_request(peer="TV; rm -rf /", mode="mirror", profile="safe")
        self.assertEqual(validate_request(peer="fire-tv:abc123", mode="mirror", profile="safe")["profile"], "safe")
        with self.assertRaisesRegex(SessionError, "unsupported capture source"):
            validate_request(peer="fire-tv:abc123", mode="mirror", profile="safe", source="window")

    def test_simulation_returns_to_idle_and_writes_event_log(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            environment = self.environment(temp)
            result = SimulatedSupervisor(environment).run(peer="simulator", mode="mirror", profile="safe", duration=0.05)
            self.assertTrue(result["ok"])
            self.assertEqual(read_state(environment)["phase"], "idle")
            events = [json.loads(line)["event"] for line in event_log_path(str(result["sessionId"]), environment).read_text().splitlines()]
            self.assertEqual(events[0], "session-started")
            self.assertEqual(events[-1], "session-finished")

    def test_stop_request_is_observed_by_running_simulation(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            environment = self.environment(temp)
            result: dict[str, object] = {}

            def run() -> None:
                result.update(SimulatedSupervisor(environment).run(peer="simulator", mode="mirror", profile="safe", duration=5))

            worker = threading.Thread(target=run)
            worker.start()
            deadline = time.monotonic() + 2
            while read_state(environment)["phase"] != "streaming" and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertEqual(read_state(environment)["phase"], "streaming")
            self.assertTrue(request_stop(environment)["ok"])
            worker.join(timeout=2)
            self.assertFalse(worker.is_alive())
            self.assertEqual(result["reason"], "user-request")
            self.assertEqual(read_state(environment)["phase"], "idle")

    def test_stop_is_valid_before_discovery_begins(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            environment = self.environment(temp)
            with patch("omarchy_cast.session._stop_requested", return_value=True):
                result = SimulatedSupervisor(environment).run(peer="simulator", mode="mirror", profile="safe", duration=5)
            self.assertEqual(result["reason"], "user-request")
            self.assertEqual(read_state(environment)["phase"], "idle")

    def test_dry_run_audits_a_read_only_plan_without_streaming(self) -> None:
        plan = {
            "schemaVersion": 1, "kind": "launch-plan", "readOnly": True,
            "execution": {"allowed": False},
            "selection": {"peer": "simulator", "mode": "mirror", "source": "display"},
            "command": ["fluxcast", "--wfd-peer", "simulator"],
        }
        with tempfile.TemporaryDirectory() as temp:
            environment = self.environment(temp)
            result = DryRunSupervisor(environment).run(peer="simulator", mode="mirror", profile="safe", plan=plan)
            self.assertTrue(result["dryRun"])
            self.assertEqual(read_state(environment)["phase"], "idle")
            events = [json.loads(line)["event"] for line in event_log_path(str(result["sessionId"]), environment).read_text().splitlines()]
            self.assertIn("plan-reviewed", events)
            self.assertNotIn("streaming", events)

    def test_dry_run_refuses_an_executable_plan(self) -> None:
        plan = {"schemaVersion": 1, "kind": "launch-plan", "readOnly": True, "execution": {"allowed": True}, "selection": {"peer": "simulator", "mode": "mirror", "source": "display"}, "command": ["fluxcast"]}
        with self.assertRaisesRegex(SessionError, "permits execution"):
            DryRunSupervisor().run(peer="simulator", mode="mirror", profile="safe", plan=plan)

    def test_history_lists_bounded_summaries_and_explicit_events(self) -> None:
        plan = {"schemaVersion": 1, "kind": "launch-plan", "readOnly": True, "execution": {"allowed": False}, "selection": {"peer": "simulator", "mode": "mirror", "source": "display"}, "command": ["fluxcast"]}
        with tempfile.TemporaryDirectory() as temp:
            environment = self.environment(temp)
            result = DryRunSupervisor(environment).run(peer="simulator", mode="mirror", profile="safe", plan=plan)
            history = session_history(environ=environment)
            self.assertEqual(history["sessions"][0]["sessionId"], result["sessionId"])
            events = read_session_events(str(result["sessionId"]), environ=environment)
            self.assertEqual(events["events"][0]["event"], "session-started")
            with self.assertRaisesRegex(SessionError, "controller-issued"):
                read_session_events("not-a-session", environ=environment)

    def test_recovery_clears_stale_active_state_under_lock(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            environment = self.environment(temp)
            stale = transition(idle_state(), "checking", sessionId="a" * 32, request={"peer": "simulator"})
            write_state(stale, environment)
            from omarchy_cast.telemetry import telemetry_paths
            live = telemetry_paths("a" * 32, environment)["current"]
            live.write_text("{}")
            result = recover_stale_session(environment)
            self.assertTrue(result["recovered"])
            self.assertEqual(read_state(environment)["phase"], "idle")
            self.assertFalse(live.parent.exists())

    def test_transport_test_supervisor_owns_fake_cleanup_for_all_outcomes(self) -> None:
        plan = {"schemaVersion": 1, "kind": "launch-plan", "readOnly": True, "execution": {"allowed": False}, "selection": {"peer": "simulator", "mode": "mirror", "source": "display"}, "command": ["fluxcast", "--protocol", "wfd", "--output-res", "1280x720", "--fps", "60", "--bitrate", "4M", "--wfd-p2p-backend", "supplicant", "--wfd-interface", "wlan42", "--wfd-peer", "simulator", "--wfd-capture-backend", "wf-recorder", "--monitor", "eDP-1", "--wfd-audio-device", "sink.monitor", "--wfd-video-encoder", "vaapi", "--wfd-no-firewall"]}
        with tempfile.TemporaryDirectory() as temp:
            environment = self.environment(temp)
            success = TransportTestSupervisor(FakeTransportAdapter(), environment).run(peer="simulator", mode="mirror", profile="safe", plan=plan)
            failure = TransportTestSupervisor(FakeTransportAdapter("failure"), environment).run(peer="simulator", mode="mirror", profile="safe", plan=plan)
            self.assertTrue(success["ok"])
            self.assertFalse(failure["ok"])
            failed_state = read_state(environment)
            self.assertEqual(failed_state["phase"], "error")
            self.assertEqual(failed_state["error"]["code"], "transport-failed")
            self.assertTrue(recover_stale_session(environment)["recovered"])
            self.assertEqual(read_state(environment)["phase"], "idle")

    def test_transport_failure_code_reaches_state_and_session_history(self) -> None:
        class DhcpFailureAdapter:
            def run(self, plan, *, timeout_seconds, cancelled, stage):
                del plan, timeout_seconds, cancelled
                stage("connecting")
                return TransportResult("failed", "DHCP did not provide an address", True, "dhcp-failed")

        executable = {
            "schemaVersion": 1, "kind": "launch-plan", "readOnly": True,
            "execution": {"allowed": False},
            "selection": {"peer": "simulator", "mode": "mirror", "source": "display"},
            "command": ["fluxcast", "--protocol", "wfd", "--output-res", "1280x720", "--fps", "60", "--bitrate", "4M", "--wfd-p2p-backend", "supplicant", "--wfd-interface", "wlan42", "--wfd-peer", "simulator", "--wfd-capture-backend", "wf-recorder", "--monitor", "eDP-1", "--wfd-audio-device", "sink.monitor", "--wfd-video-encoder", "vaapi", "--wfd-no-firewall"],
        }
        with tempfile.TemporaryDirectory() as temp:
            environment = self.environment(temp)
            result = TransportTestSupervisor(DhcpFailureAdapter(), environment).run(peer="simulator", mode="mirror", profile="safe", plan=executable)
            state = read_state(environment)
            self.assertEqual(state["error"]["code"], "dhcp-failed")
            events = read_session_events(str(result["sessionId"]), environ=environment)["events"]
            self.assertEqual(events[-1]["reason"], "dhcp-failed")

    def test_transport_exception_code_reaches_state_and_session_history(self) -> None:
        class CancelledAuthorizationAdapter:
            def run(self, plan, *, timeout_seconds, cancelled, stage):
                del plan, timeout_seconds, cancelled, stage
                raise TransportError("Administrator approval was cancelled. Nothing was changed.", code="authorization-cancelled")

        executable = {
            "schemaVersion": 1, "kind": "launch-plan", "readOnly": True,
            "execution": {"allowed": False},
            "selection": {"peer": "simulator", "mode": "mirror", "source": "display"},
            "command": ["fluxcast", "--protocol", "wfd", "--output-res", "1280x720", "--fps", "60", "--bitrate", "4M", "--wfd-p2p-backend", "supplicant", "--wfd-interface", "wlan42", "--wfd-peer", "simulator", "--wfd-capture-backend", "wf-recorder", "--monitor", "eDP-1", "--wfd-audio-device", "sink.monitor", "--wfd-video-encoder", "vaapi", "--wfd-no-firewall"],
        }
        with tempfile.TemporaryDirectory() as temp:
            environment = self.environment(temp)
            with self.assertRaisesRegex(SessionError, "Nothing was changed"):
                TransportTestSupervisor(CancelledAuthorizationAdapter(), environment).run(peer="simulator", mode="mirror", profile="safe", plan=executable)
            state = read_state(environment)
            self.assertEqual(state["error"]["code"], "authorization-cancelled")
            events = session_history(environ=environment)["sessions"]
            session_id = events[0]["sessionId"]
            history = read_session_events(str(session_id), environ=environment)["events"]
            self.assertEqual(history[-1]["code"], "authorization-cancelled")
