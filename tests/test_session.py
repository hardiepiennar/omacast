from __future__ import annotations

import json
import fcntl
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from omarchy_cast.session import MAX_EVENT_DIRECTORY_ENTRIES, MAX_EVENTS_PER_SESSION, MAX_SESSION_LOGS, DryRunSupervisor, SessionError, SimulatedSupervisor, TransportTestSupervisor, _stop_requested, append_event, event_log_path, read_session_events, recover_stale_session, request_stop, session_history, validate_request
from omarchy_cast.state import idle_state, read_state, transition, write_state
from omarchy_cast.transport import FakeTransportAdapter, TransportError, TransportResult


class SessionTest(unittest.TestCase):
    def test_new_session_prunes_history_to_the_bounded_limit(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            environment = {"XDG_RUNTIME_DIR": temp, "XDG_STATE_HOME": temp}
            telemetry = Path(temp) / "omarchy-cast" / "telemetry"
            telemetry.mkdir(mode=0o700, parents=True)
            telemetry.parent.chmod(0o700)
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

    def test_history_does_not_coerce_event_flags(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            environment = self.environment(temp)
            session_id = "7" * 32
            append_event(session_id, "session-started", environment, dryRun=1, simulated="yes")
            summary = session_history(environ=environment)["sessions"][0]
            self.assertIs(summary["dryRun"], False)
            self.assertIs(summary["simulated"], False)

    def test_event_append_rejects_links_and_fifo_without_changing_targets(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            environment = self.environment(temp)
            session_id = "a" * 32
            path = event_log_path(session_id, environment)
            path.parent.mkdir(mode=0o700, parents=True)
            target = Path(temp) / "unrelated-user-file"
            target.write_text("preserve", encoding="utf-8")
            target.chmod(0o644)

            path.symlink_to(target)
            with self.assertRaisesRegex(SessionError, "unavailable or unsafe"):
                append_event(session_id, "test", environment)
            self.assertEqual(target.read_text(encoding="utf-8"), "preserve")
            self.assertEqual(target.stat().st_mode & 0o777, 0o644)

            path.unlink()
            os.link(target, path)
            with self.assertRaisesRegex(SessionError, "link count"):
                append_event(session_id, "test", environment)
            self.assertEqual(target.read_text(encoding="utf-8"), "preserve")
            self.assertEqual(target.stat().st_mode & 0o777, 0o644)

            path.unlink()
            os.mkfifo(path, mode=0o600)
            with self.assertRaisesRegex(SessionError, "unavailable or unsafe"):
                append_event(session_id, "test", environment)

    def test_event_directory_cannot_be_redirected_through_product_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            state_home = Path(temp) / "state"
            state_home.mkdir(mode=0o700)
            unrelated = Path(temp) / "unrelated"
            sessions = unrelated / "sessions"
            sessions.mkdir(mode=0o700, parents=True)
            unrelated.chmod(0o700)
            session_id = "a" * 32
            target = sessions / f"{session_id}.jsonl"
            target.write_text("preserve\n", encoding="utf-8")
            target.chmod(0o600)
            (state_home / "omarchy-cast").symlink_to(unrelated, target_is_directory=True)

            with self.assertRaisesRegex(SessionError, "unavailable or unsafe"):
                append_event(session_id, "session-started", {"XDG_STATE_HOME": str(state_home)})

            self.assertEqual(target.read_text(encoding="utf-8"), "preserve\n")

    def test_event_reads_reject_links_fifo_hardlinks_and_oversized_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            environment = self.environment(temp)
            session_id = "b" * 32
            path = event_log_path(session_id, environment)
            path.parent.mkdir(mode=0o700, parents=True)
            target = Path(temp) / "event-target"
            target.write_text('{"schemaVersion":1,"event":"test"}\n', encoding="utf-8")
            target.chmod(0o600)

            path.symlink_to(target)
            with self.assertRaisesRegex(SessionError, "safe boundary"):
                read_session_events(session_id, environ=environment)
            path.unlink()

            os.link(target, path)
            with self.assertRaisesRegex(SessionError, "safe boundary"):
                read_session_events(session_id, environ=environment)
            path.unlink()

            os.mkfifo(path, mode=0o600)
            with self.assertRaisesRegex(SessionError, "safe boundary"):
                read_session_events(session_id, environ=environment)
            path.unlink()

            path.write_bytes(b"x" * (1_048_576 + 1))
            path.chmod(0o600)
            with self.assertRaisesRegex(SessionError, "safe boundary"):
                read_session_events(session_id, environ=environment)
            path.write_text('{"schemaVersion":true,"event":"test"}\n', encoding="utf-8")
            with self.assertRaisesRegex(SessionError, "unsupported event"):
                read_session_events(session_id, environ=environment)

    def test_history_includes_only_safe_controller_issued_event_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            environment = self.environment(temp)
            accepted = "a" * 32
            append_event(accepted, "session-started", environment)
            directory = event_log_path(accepted, environment).parent
            content = '{"schemaVersion":1,"event":"session-started"}\n'
            (directory / "not-a-session.jsonl").write_text(content, encoding="utf-8")
            target = Path(temp) / "history-target"
            target.write_text(content, encoding="utf-8")
            target.chmod(0o600)
            (directory / ("b" * 32 + ".jsonl")).symlink_to(target)
            os.link(target, directory / ("c" * 32 + ".jsonl"))
            os.mkfifo(directory / ("d" * 32 + ".jsonl"), mode=0o600)

            history = session_history(limit=10, environ=environment)
            self.assertEqual([item["sessionId"] for item in history["sessions"]], [accepted])

    def test_history_enumeration_and_event_count_are_bounded(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            environment = self.environment(temp)
            session_id = "a" * 32
            path = event_log_path(session_id, environment)
            path.parent.mkdir(mode=0o700, parents=True)
            event = '{"schemaVersion":1,"event":"sample"}\n'
            path.write_text(event * (MAX_EVENTS_PER_SESSION + 1), encoding="utf-8")
            path.chmod(0o600)
            with self.assertRaisesRegex(SessionError, "too many events"):
                read_session_events(session_id, environ=environment)

            path.unlink()
            for index in range(MAX_EVENT_DIRECTORY_ENTRIES + 1):
                (path.parent / f"untrusted-{index}").touch(mode=0o600)
            started = time.monotonic()
            with self.assertRaisesRegex(SessionError, "too many entries"):
                session_history(environ=environment)
            self.assertLess(time.monotonic() - started, 1.0)

            # A same-UID entry flood must not make persistent history grow
            # beyond its directory budget.
            with self.assertRaisesRegex(SessionError, "too many entries"):
                append_event(session_id, "session-started", environment)
            self.assertFalse(path.exists())

    def test_event_append_rejects_public_mode_without_repairing_it(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            environment = self.environment(temp)
            session_id = "9" * 32
            path = event_log_path(session_id, environment)
            path.parent.mkdir(mode=0o700, parents=True)
            path.write_text("preserve\n", encoding="utf-8")
            path.chmod(0o644)
            with self.assertRaisesRegex(SessionError, "permissions"):
                append_event(session_id, "test", environment)
            self.assertEqual(path.read_text(encoding="utf-8"), "preserve\n")
            self.assertEqual(path.stat().st_mode & 0o777, 0o644)

    def test_event_append_has_a_bounded_lock_deadline(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            environment = self.environment(temp)
            session_id = "8" * 32
            path = event_log_path(session_id, environment)
            path.parent.mkdir(mode=0o700, parents=True)
            path.write_text("", encoding="utf-8")
            path.chmod(0o600)
            with path.open("rb") as locked:
                fcntl.flock(locked.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                started = time.monotonic()
                with self.assertRaisesRegex(SessionError, "history is busy"):
                    append_event(session_id, "test", environment)
                self.assertLess(time.monotonic() - started, 0.75)

    def test_stop_request_replaces_links_without_touching_their_targets(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            environment = self.environment(temp)
            session_id = "e" * 32
            write_state(transition(idle_state(), "checking", sessionId=session_id), environment)
            runtime = Path(environment["XDG_RUNTIME_DIR"]) / "omarchy-cast"
            target = Path(temp) / "stop-target"
            target.write_text("preserve", encoding="utf-8")
            target.chmod(0o644)
            (runtime / "stop-request.tmp").symlink_to(target)
            (runtime / "stop-request.json").symlink_to(target)

            self.assertTrue(request_stop(environment)["ok"])
            self.assertEqual(target.read_text(encoding="utf-8"), "preserve")
            self.assertEqual(target.stat().st_mode & 0o777, 0o644)
            self.assertTrue((runtime / "stop-request.tmp").is_symlink())
            self.assertFalse((runtime / "stop-request.json").is_symlink())
            self.assertEqual((runtime / "stop-request.json").stat().st_mode & 0o777, 0o600)
            self.assertEqual(list(runtime.glob(".stop-request-*.tmp")), [])
            self.assertTrue(_stop_requested(session_id, environment))
            events = read_session_events(session_id, environ=environment)["events"]
            self.assertEqual(events[-1]["event"], "stop-requested")
            self.assertEqual(events[-1]["previousPhase"], "checking")

    def test_stop_request_does_not_depend_on_session_history(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            environment = self.environment(temp)
            session_id = "d" * 32
            write_state(transition(idle_state(), "checking", sessionId=session_id), environment)
            with patch("omarchy_cast.session.append_event", side_effect=SessionError("history is busy")):
                self.assertTrue(request_stop(environment)["ok"])
            self.assertTrue(_stop_requested(session_id, environment))

    def test_stop_reader_rejects_fifo_links_hardlinks_and_unexpected_json(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            environment = self.environment(temp)
            session_id = "f" * 32
            runtime = Path(environment["XDG_RUNTIME_DIR"]) / "omarchy-cast"
            runtime.mkdir(mode=0o700)
            path = runtime / "stop-request.json"
            target = Path(temp) / "stop-target"
            target.write_text(json.dumps({"schemaVersion": 1, "sessionId": session_id}), encoding="utf-8")
            target.chmod(0o600)

            path.symlink_to(target)
            self.assertFalse(_stop_requested(session_id, environment))
            path.unlink()
            os.link(target, path)
            self.assertFalse(_stop_requested(session_id, environment))
            path.unlink()
            os.mkfifo(path, mode=0o600)
            self.assertFalse(_stop_requested(session_id, environment))
            path.unlink()
            path.write_bytes(b"{" + b" " * 4_096 + b"}")
            path.chmod(0o600)
            self.assertFalse(_stop_requested(session_id, environment))
            path.write_text("[]", encoding="utf-8")
            path.chmod(0o600)
            self.assertFalse(_stop_requested(session_id, environment))
            path.write_text(json.dumps({"schemaVersion": True, "sessionId": session_id}), encoding="utf-8")
            self.assertFalse(_stop_requested(session_id, environment))
            path.write_text(json.dumps({"schemaVersion": 1, "sessionId": session_id, "extra": True}), encoding="utf-8")
            self.assertFalse(_stop_requested(session_id, environment))

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
        plan = {"schemaVersion": 1, "kind": "launch-plan", "readOnly": True, "execution": {"allowed": False}, "selection": {"peer": "simulator", "mode": "mirror", "source": "display"}, "command": ["fluxcast", "--protocol", "wfd", "--output-res", "1280x720", "--fps", "60", "--bitrate", "4M", "--wfd-p2p-backend", "supplicant", "--wfd-interface", "wlan42", "--wfd-peer", "simulator", "--wfd-capture-backend", "gpu-screen-recorder", "--monitor", "eDP-1", "--wfd-audio-device", "sink.monitor", "--wfd-video-encoder", "vaapi", "--wfd-no-firewall"]}
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
            "command": ["fluxcast", "--protocol", "wfd", "--output-res", "1280x720", "--fps", "60", "--bitrate", "4M", "--wfd-p2p-backend", "supplicant", "--wfd-interface", "wlan42", "--wfd-peer", "simulator", "--wfd-capture-backend", "gpu-screen-recorder", "--monitor", "eDP-1", "--wfd-audio-device", "sink.monitor", "--wfd-video-encoder", "vaapi", "--wfd-no-firewall"],
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
            "command": ["fluxcast", "--protocol", "wfd", "--output-res", "1280x720", "--fps", "60", "--bitrate", "4M", "--wfd-p2p-backend", "supplicant", "--wfd-interface", "wlan42", "--wfd-peer", "simulator", "--wfd-capture-backend", "gpu-screen-recorder", "--monitor", "eDP-1", "--wfd-audio-device", "sink.monitor", "--wfd-video-encoder", "vaapi", "--wfd-no-firewall"],
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
