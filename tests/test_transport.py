from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import threading
import time
import unittest
from pathlib import Path
import tempfile

from unittest.mock import Mock, patch

from omarchy_cast.telemetry import MAX_PROCESS_DESCRIPTORS, MAX_SYSFS_INTERFACE_ENTRIES
from omarchy_cast.transport import CAPTURE_START_TIMEOUT_SECONDS, CONNECT_TIMEOUT_SECONDS, GUARD_LEASE_SECONDS, MAX_GUARD_DIAGNOSTIC_BYTES, RECEIVER_DISCONNECT_GRACE_SECONDS, SUPPLICANT_GROUP_TIMEOUT_SECONDS, DisabledTransportAdapter, FakeTransportAdapter, GuardedTransportAdapter, SessionLease, TransportDisabled, TransportError, _BoundedPipeDrain, validate_transport_plan


class StatusDrainFixture:
    def __init__(self, text: str = "", *, overflowed: bool = False) -> None:
        self._lines = text.splitlines()
        self.overflowed = overflowed

    def next_line(self, _timeout: float) -> str | None:
        return self._lines.pop(0) if self._lines else None

    def lines(self) -> tuple[str, ...]:
        return tuple(self._lines)

    def text(self) -> str:
        return "\n".join(self._lines)


def plan() -> dict[str, object]:
    return {
        "schemaVersion": 1, "kind": "launch-plan", "readOnly": True,
        "execution": {"allowed": False, "reason": "read-only launch preview"},
        "profile": {"label": "Safe", "width": 1280, "height": 720, "fps": 60, "bitrateMbps": 7},
        "selection": {"peer": "00:11:22:33:44:55", "mode": "mirror", "source": "display", "networkBackend": "direct", "wifiInterface": "wlan42", "wifiFrequencyMhz": 2412, "p2pFrequencyMhz": 2412, "monitor": "eDP-1", "audioSource": "sink.monitor", "videoEncoder": "vaapi"},
        "command": ["fluxcast", "--protocol", "wfd", "--output-res", "1280x720", "--fps", "60", "--bitrate", "7M", "--wfd-video-encoder", "vaapi", "--wfd-p2p-backend", "supplicant", "--wfd-supplicant-mode", "connect", "--wfd-peer", "00:11:22:33:44:55", "--wfd-interface", "wlan42", "--wfd-timeout", "15", "--wfd-supplicant-frequency", "2412", "--wfd-no-firewall", "--monitor", "eDP-1", "--wfd-capture-backend", "gpu-screen-recorder", "--wfd-audio-device", "sink.monitor"],
        "warnings": [],
    }


def executable_plan_fixture() -> dict[str, object]:
    result = plan()
    result["execution"] = {"allowed": True, "reason": "guarded-session-supervisor"}
    return result


class TransportTest(unittest.TestCase):
    def test_guard_stderr_is_continuously_drained_with_fixed_retention(self) -> None:
        read_descriptor, write_descriptor = os.pipe()
        drain = _BoundedPipeDrain(os.fdopen(read_descriptor, "rb", buffering=0))
        drain.start()

        def write_pressure() -> None:
            with os.fdopen(write_descriptor, "wb", buffering=0) as stream:
                remaining = memoryview(b"x" * (MAX_GUARD_DIAGNOSTIC_BYTES * 4))
                while remaining:
                    written = stream.write(remaining)
                    remaining = remaining[written:]

        writer = threading.Thread(target=write_pressure)
        writer.start()
        writer.join(timeout=2)
        self.assertFalse(writer.is_alive(), "guard diagnostics blocked on an undrained pipe")
        drain.stop()
        self.assertTrue(drain.overflowed)
        self.assertLessEqual(len(drain.text().encode("utf-8")), MAX_GUARD_DIAGNOSTIC_BYTES)

    def test_guard_drain_stops_while_a_writer_keeps_the_pipe_open(self) -> None:
        read_descriptor, write_descriptor = os.pipe()
        drain = _BoundedPipeDrain(os.fdopen(read_descriptor, "rb", buffering=0))
        drain.start()
        started = time.monotonic()
        drain.stop()
        os.close(write_descriptor)
        self.assertLess(time.monotonic() - started, 1)

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
        with self.assertRaisesRegex(TransportError, "exactly match"):
            validate_transport_plan(unsafe)
        boolean_schema = plan()
        boolean_schema["schemaVersion"] = True
        with self.assertRaisesRegex(TransportError, "versioned"):
            validate_transport_plan(boolean_schema)
        boolean_execution = plan()
        boolean_execution["execution"] = {"allowed": 0, "reason": "read-only launch preview"}
        with self.assertRaisesRegex(TransportError, "execution permission"):
            validate_transport_plan(boolean_execution)

        mismatched_frequency = plan()
        mismatched_frequency["selection"] = dict(mismatched_frequency["selection"])
        mismatched_frequency["selection"]["p2pFrequencyMhz"] = 0
        with self.assertRaisesRegex(TransportError, "P2P frequency does not match"):
            validate_transport_plan(mismatched_frequency)
        numeric_execution = executable_plan_fixture()
        numeric_execution["execution"] = {"allowed": 1, "reason": "guarded-session-supervisor"}
        with self.assertRaisesRegex(TransportError, "execution permission"):
            validate_transport_plan(numeric_execution, executable=True)

    def test_capture_backend_must_match_the_display_selection(self) -> None:
        mismatched_source = plan()
        mismatched_source["selection"] = {**mismatched_source["selection"], "source": "window"}
        with self.assertRaisesRegex(TransportError, "unsupported source"):
            validate_transport_plan(mismatched_source)

        mismatched_backend = plan()
        command = list(mismatched_backend["command"])
        command[command.index("--wfd-capture-backend") + 1] = "other"
        mismatched_backend["command"] = command
        with self.assertRaisesRegex(TransportError, "exactly match"):
            validate_transport_plan(mismatched_backend)

    def test_production_plan_rejects_every_override_and_open_field(self) -> None:
        mutations = []
        extra_field = plan()
        extra_field["legacy"] = True
        mutations.append(extra_field)
        boolean_profile = plan()
        boolean_profile["profile"] = {**boolean_profile["profile"], "fps": True}
        mutations.append(boolean_profile)
        open_execution = plan()
        open_execution["execution"] = {**open_execution["execution"], "debug": True}
        mutations.append(open_execution)
        mismatched_interface = plan()
        mismatched_interface["selection"] = {**mismatched_interface["selection"], "wifiInterface": "wlan99"}
        mutations.append(mismatched_interface)
        for flag, value in (("--bitrate", "99M"), ("--wfd-supplicant-mode", "manage")):
            changed = plan()
            command = list(changed["command"])
            command[command.index(flag) + 1] = value
            changed["command"] = command
            mutations.append(changed)
        duplicate = plan()
        duplicate["command"] = list(duplicate["command"]) + ["--bitrate", "7M"]
        mutations.append(duplicate)
        arbitrary = plan()
        arbitrary["command"] = list(arbitrary["command"]) + ["--verbose"]
        mutations.append(arbitrary)
        for candidate in mutations:
            with self.subTest(candidate=candidate):
                with self.assertRaises(TransportError):
                    validate_transport_plan(candidate)

    def test_executable_plan_rechecks_receiver_address_and_selection(self) -> None:
        executable = executable_plan_fixture()
        validate_transport_plan(executable, executable=True)

        for command_peer, selection_peer in (
            ("tv-01", "tv-01"),
            ("00:11:22:33:44:55", "AA:BB:CC:DD:EE:FF"),
        ):
            with self.subTest(command_peer=command_peer, selection_peer=selection_peer):
                invalid = executable_plan_fixture()
                command = list(invalid["command"])
                command[command.index("--wfd-peer") + 1] = command_peer
                invalid["command"] = command
                invalid["selection"] = {**invalid["selection"], "peer": selection_peer}
                with self.assertRaises(TransportError):
                    validate_transport_plan(invalid, executable=True)

    def test_rtsp_state_requires_an_established_7236_socket(self) -> None:
        sockets = "sl local_address rem_address st tx_queue rx_queue tr tm->when retrnsmt uid timeout inode\n  0: 00000000:1C44 00000000:0000 0A 0:0 0:0 0 0 0 111\n  1: C0A81B41:1C44 C0A81B72:A540 01 0:0 0:0 0 0 0 222\n"
        with patch("omarchy_cast.transport._bounded_stream_read", return_value=(sockets, True)), patch.object(GuardedTransportAdapter, "_socket_inodes", return_value={"222"}):
            self.assertTrue(GuardedTransportAdapter._rtsp_established(1234))
        with patch("omarchy_cast.transport._bounded_stream_read", return_value=(sockets, True)), patch.object(GuardedTransportAdapter, "_socket_inodes", return_value={"333"}):
            self.assertFalse(GuardedTransportAdapter._rtsp_established(1234))
        with patch("omarchy_cast.transport._bounded_stream_read", return_value=(sockets, False)), patch.object(GuardedTransportAdapter, "_socket_inodes", return_value={"222"}):
            self.assertIsNone(GuardedTransportAdapter._rtsp_established(1234))

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

    def test_receiver_liveness_is_unknown_after_bounded_sysfs_entries(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            network_root = Path(directory)
            for index in range(MAX_SYSFS_INTERFACE_ENTRIES):
                (network_root / f"p2p-wlan42-flood-{index}").write_text("not a directory", encoding="utf-8")
            actual = network_root / "p2p-wlan42-z-real"
            actual.mkdir()
            ordered_root = Mock()
            ordered_root.glob.return_value = [
                *(network_root / f"p2p-wlan42-flood-{index}" for index in range(MAX_SYSFS_INTERFACE_ENTRIES)),
                actual,
            ]
            self.assertIsNone(GuardedTransportAdapter._p2p_group_present("wlan42", ordered_root))

    def test_streaming_requires_a_completed_video_progress_record(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            progress = Path(directory) / "progress"
            self.assertFalse(GuardedTransportAdapter._media_started(progress))
            progress.write_text("frame=0\nprogress=continue\n", encoding="utf-8")
            self.assertFalse(GuardedTransportAdapter._media_started(progress))
            progress.write_text("frame=0\nprogress=continue\nframe=1\nout_time_ms=33333\nprogress=continue\n", encoding="utf-8")
            self.assertTrue(GuardedTransportAdapter._media_started(progress))
            self.assertFalse(GuardedTransportAdapter._media_started_text("frame=" + "9" * 100_000 + "\nprogress=continue\n"))
            self.assertFalse(GuardedTransportAdapter._media_started_text("frame=9223372036854775808\nprogress=continue\n"))

    def test_rtsp_liveness_is_unknown_after_descriptor_observation_cap(self) -> None:
        entries = [Mock(readlink=Mock(return_value="socket:[1]")) for _ in range(MAX_PROCESS_DESCRIPTORS + 1)]
        with patch("omarchy_cast.transport.Path.iterdir", side_effect=lambda: iter(entries)):
            self.assertIsNone(GuardedTransportAdapter._socket_inodes(1234))
            self.assertIsNone(GuardedTransportAdapter._rtsp_established(1234))

    def test_group_timeout_is_not_coupled_to_session_duration(self) -> None:
        from omarchy_cast.guard import GuardRequest
        executable = executable_plan_fixture()
        paths = {name: Path("/run/user/1000/omarchy-cast") / name for name in ("progress", "latency", "packets")}
        adapter = GuardedTransportAdapter(GuardRequest(1, "a" * 32, 1000, "wlan42", "00:11:22:33:44:55", 2437, 60), env={})
        command = adapter._engine_command(executable, paths, "/run/omarchy-cast/" + "a" * 32 + "/user/trigger", "/run/omarchy-cast/" + "a" * 32 + "/supplicant.sock")
        self.assertEqual(command[command.index("--omacast-session") + 1], "a" * 32)
        self.assertEqual(command[command.index("--wfd-supplicant-hold") + 1], "45")
        self.assertNotIn("--wfd-packet-log", command)

        traced = GuardedTransportAdapter(
            GuardRequest(1, "a" * 32, 1000, "wlan42", "00:11:22:33:44:55", 2437, 60),
            env={"OMARCHY_CAST_PACKET_TELEMETRY": "1"},
        )._engine_command(executable, paths, "/run/omarchy-cast/" + "a" * 32 + "/user/trigger", "/run/omarchy-cast/" + "a" * 32 + "/supplicant.sock")
        self.assertNotIn("--wfd-packet-log", traced)

    def test_pairing_pin_is_inherited_only_as_an_anonymous_descriptor(self) -> None:
        from omarchy_cast.guard import GuardRequest
        executable = executable_plan_fixture()
        paths = {name: Path("/run/user/1000/omarchy-cast") / name for name in ("progress", "latency", "packets")}
        adapter = GuardedTransportAdapter(
            GuardRequest(1, "a" * 32, 1000, "wlan42", "00:11:22:33:44:55", 2437, 60),
            pairing_pin=b"12345670",
        )
        command = adapter._engine_command(
            executable, paths,
            "/run/omarchy-cast/" + "a" * 32 + "/user/trigger",
            "/run/omarchy-cast/" + "a" * 32 + "/supplicant.sock",
            pairing_pin_fd=7,
        )
        self.assertNotIn("12345670", command)
        self.assertEqual(command[command.index("--wfd-pairing-pin-fd") + 1], "7")

    def test_session_lease_is_private_and_renewable(self) -> None:
        self.assertEqual(GUARD_LEASE_SECONDS, 60)
        with tempfile.TemporaryDirectory() as directory:
            heartbeat = Path(directory) / "heartbeat"
            lease = SessionLease(heartbeat, interval_seconds=0.01)
            lease.start()
            lease.stop()
            self.assertRegex(heartbeat.read_text(encoding="ascii"), r"^[0-9]+\n$")
            self.assertEqual(heartbeat.stat().st_mode & 0o777, 0o600)

    def test_session_lease_rejects_links_without_changing_their_targets(self) -> None:
        for link_kind in ("symlink", "hardlink"):
            with self.subTest(link_kind=link_kind), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                heartbeat = root / "heartbeat"
                target = root / "unrelated"
                target.write_text("preserve", encoding="utf-8")
                target.chmod(0o600)
                if link_kind == "symlink":
                    heartbeat.symlink_to(target)
                else:
                    os.link(target, heartbeat)

                with self.assertRaises(OSError):
                    SessionLease(heartbeat).start()

                self.assertEqual(target.read_text(encoding="utf-8"), "preserve")

    def test_session_lease_rejects_fifo_without_blocking(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            heartbeat = Path(directory) / "heartbeat"
            os.mkfifo(heartbeat, mode=0o600)
            probe = (
                "import pathlib,sys\n"
                "from omarchy_cast.transport import SessionLease\n"
                "try:\n"
                "    SessionLease(pathlib.Path(sys.argv[1])).start()\n"
                "except OSError as error:\n"
                "    print(error)\n"
                "else:\n"
                "    raise SystemExit('FIFO was accepted as the session heartbeat')\n"
            )
            result = subprocess.run(
                (sys.executable, "-c", probe, str(heartbeat)),
                check=False,
                capture_output=True,
                text=True,
                timeout=2,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("not a regular file", result.stdout)

    def test_session_lease_rejects_oversized_or_public_files_before_truncation(self) -> None:
        for content, mode, message in (("x" * 33, 0o600, "size limit"), ("preserve", 0o644, "permissions")):
            with self.subTest(message=message), tempfile.TemporaryDirectory() as directory:
                heartbeat = Path(directory) / "heartbeat"
                heartbeat.write_text(content, encoding="ascii")
                heartbeat.chmod(mode)

                with self.assertRaisesRegex(OSError, message):
                    SessionLease(heartbeat).start()

                self.assertEqual(heartbeat.read_text(encoding="ascii"), content)
                self.assertEqual(heartbeat.stat().st_mode & 0o777, mode)

    def test_session_lease_keeps_renewing_the_pinned_inode_after_path_replacement(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            heartbeat = root / "heartbeat"
            target = root / "unrelated"
            target.write_text("preserve", encoding="utf-8")
            target.chmod(0o600)
            lease = SessionLease(heartbeat, interval_seconds=60)
            with patch("omarchy_cast.transport.time.time", side_effect=(100, 200)):
                lease.start()
                guard_descriptor = os.open(heartbeat, os.O_RDONLY)
                try:
                    heartbeat.unlink()
                    heartbeat.symlink_to(target)
                    lease.renew()
                    self.assertEqual(os.pread(guard_descriptor, 32, 0), b"200\n")
                finally:
                    os.close(guard_descriptor)
                    lease.stop()

            self.assertEqual(target.read_text(encoding="utf-8"), "preserve")

    def test_session_lease_rejects_an_unsafe_parent_without_repairing_it(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory) / "markers"
            parent.mkdir(mode=0o755)
            with self.assertRaisesRegex(OSError, "directory ownership or permissions"):
                SessionLease(parent / "heartbeat").start()
            self.assertEqual(parent.stat().st_mode & 0o777, 0o755)
            self.assertFalse((parent / "heartbeat").exists())

    def test_engine_detail_is_bounded_to_recent_lines(self) -> None:
        detail = GuardedTransportAdapter._bounded_detail("first\nsecond\nthird\nfourth\nfifth\n", "fallback")
        self.assertEqual(detail, "second | third | fourth | fifth")
        self.assertEqual(GuardedTransportAdapter._bounded_detail(None, "fallback"), "fallback")

    def test_guard_stop_uses_the_unprivileged_session_marker(self) -> None:
        from omarchy_cast.guard import GuardRequest
        adapter = GuardedTransportAdapter(GuardRequest(1, "a" * 32, 1000, "wlan42", "00:11:22:33:44:55", 2437, 60))
        with patch.object(adapter, "_write_private_marker", return_value=True) as write_marker:
            adapter._stop_guard()
        write_marker.assert_called_once_with("/run/omarchy-cast/" + "a" * 32 + "/user/stop")

    def test_stop_marker_rejects_fifo_and_hardlink_without_blocking_or_truncating(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            marker = root / "stop"
            os.mkfifo(marker, mode=0o600)
            self.assertFalse(GuardedTransportAdapter._write_private_marker(str(marker)))
            marker.unlink()
            target = root / "target"
            target.write_text("preserve", encoding="utf-8")
            target.chmod(0o600)
            os.link(target, marker)
            self.assertFalse(GuardedTransportAdapter._write_private_marker(str(marker)))
            self.assertEqual(target.read_text(encoding="utf-8"), "preserve")
            marker.unlink()
            self.assertTrue(GuardedTransportAdapter._write_private_marker(str(marker)))
            self.assertEqual(marker.stat().st_mode & 0o777, 0o600)

    def test_authorization_wait_observes_stop_without_touching_the_guard(self) -> None:
        from omarchy_cast.guard import GuardRequest
        request = GuardRequest(1, "a" * 32, 1000, "wlan42", "00:11:22:33:44:55", 2437, 60)
        process = Mock()
        self.assertIsNone(GuardedTransportAdapter._read_ready(
            process, request, lambda: True,
            stdout_drain=StatusDrainFixture(), stderr_drain=StatusDrainFixture(),
        ))

    def test_authorization_ready_binds_trigger_and_broker_to_one_session(self) -> None:
        from omarchy_cast.guard import GuardRequest
        session = "a" * 32
        request = GuardRequest(1, session, 1000, "wlan42", "00:11:22:33:44:55", 2437, 60)
        payload = json.dumps({
            "schemaVersion": 1,
            "kind": "omarchy-cast-guard-status",
            "ok": True,
            "phase": "ready",
            "sessionId": session,
            "error": None,
            "triggerPath": f"/run/omarchy-cast/{session}/user/trigger",
            "brokerPath": f"/run/omarchy-cast/{session}/supplicant.sock",
        }) + "\n"
        process = Mock()
        process.poll.return_value = None
        self.assertEqual(
            GuardedTransportAdapter._read_ready(
                process, request, lambda: False,
                stdout_drain=StatusDrainFixture(payload), stderr_drain=StatusDrainFixture(),
            ),
            (f"/run/omarchy-cast/{session}/user/trigger", f"/run/omarchy-cast/{session}/supplicant.sock"),
        )
        process = Mock()
        process.poll.return_value = None
        with self.assertRaisesRegex(TransportError, "invalid readiness"):
            GuardedTransportAdapter._read_ready(
                process, request, lambda: False,
                stdout_drain=StatusDrainFixture("[" * 2_000 + "0" + "]" * 2_000 + "\n"),
                stderr_drain=StatusDrainFixture(),
            )

    def test_authorization_ready_cannot_deadlock_on_stderr_pressure(self) -> None:
        from omarchy_cast.guard import GuardRequest
        session = "a" * 32
        request = GuardRequest(1, session, 1000, "wlan42", "00:11:22:33:44:55", 2437, 60)
        payload = json.dumps({
            "schemaVersion": 1, "kind": "omarchy-cast-guard-status", "ok": True,
            "phase": "ready", "sessionId": session, "error": None,
            "triggerPath": f"/run/omarchy-cast/{session}/user/trigger",
            "brokerPath": f"/run/omarchy-cast/{session}/supplicant.sock",
        })
        script = (
            "import sys\n"
            f"sys.stderr.write('x' * {MAX_GUARD_DIAGNOSTIC_BYTES * 4})\n"
            "sys.stderr.flush()\n"
            f"print({payload!r}, flush=True)\n"
        )
        process = subprocess.Popen(
            (sys.executable, "-c", script), stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        assert process.stdout is not None and process.stderr is not None
        stdout_drain = _BoundedPipeDrain(process.stdout)
        stderr_drain = _BoundedPipeDrain(process.stderr)
        stdout_drain.start()
        stderr_drain.start()
        try:
            self.assertEqual(
                GuardedTransportAdapter._read_ready(
                    process, request, lambda: False, timeout=2,
                    stdout_drain=stdout_drain, stderr_drain=stderr_drain,
                ),
                (f"/run/omarchy-cast/{session}/user/trigger", f"/run/omarchy-cast/{session}/supplicant.sock"),
            )
            process.wait(timeout=2)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=2)
            stdout_drain.stop()
            stderr_drain.stop()
        self.assertTrue(stderr_drain.overflowed)

    def test_dismissed_authorization_is_an_actionable_no_change_failure(self) -> None:
        from omarchy_cast.guard import GuardRequest
        request = GuardRequest(1, "a" * 32, 1000, "wlan42", "00:11:22:33:44:55", 2437, 60)
        process = Mock(returncode=126)
        process.poll.return_value = 126
        with self.assertRaises(TransportError) as caught:
            GuardedTransportAdapter._read_ready(
                process, request, lambda: False,
                stdout_drain=StatusDrainFixture(),
                stderr_drain=StatusDrainFixture("Error executing command as another user: Request dismissed"),
            )
        self.assertEqual(caught.exception.code, "authorization-cancelled")
        self.assertIn("Nothing was changed", str(caught.exception))

    def test_detached_launcher_waits_for_root_worker_status_without_busy_spinning(self) -> None:
        from omarchy_cast.guard import GuardRequest
        request = GuardRequest(1, "a" * 32, 1000, "wlan42", "00:11:22:33:44:55", 2437, 60)
        process = Mock(returncode=0)
        process.poll.return_value = 0
        with (
            patch("omarchy_cast.transport.read_guard_status", return_value=None),
            patch("omarchy_cast.transport.time.monotonic", side_effect=(0.0, 0.0, 0.0, 2.0)),
            patch("omarchy_cast.transport.time.sleep") as sleep,
            self.assertRaises(TransportError) as caught,
        ):
            GuardedTransportAdapter._read_ready(
                process, request, lambda: False, timeout=1,
                stdout_drain=StatusDrainFixture(), stderr_drain=StatusDrainFixture(),
            )
        self.assertEqual(caught.exception.code, "authorization-timeout")
        sleep.assert_called_once_with(0.05)

    def test_helper_cleanup_status_is_bounded_session_scoped_and_explicit(self) -> None:
        from omarchy_cast.guard import GuardRequest
        request = GuardRequest(1, "a" * 32, 1000, "wlan42", "00:11:22:33:44:55", 2437, 60)

        def process(*, exited: bool = True) -> Mock:
            helper = Mock()
            helper.poll.return_value = 0 if exited else None
            return helper

        active = '{"schemaVersion":1,"kind":"omarchy-cast-guard-status","ok":true,"phase":"active","sessionId":"' + "a" * 32 + '","error":null}\n'
        cleaned = '{"schemaVersion":1,"kind":"omarchy-cast-guard-status","ok":true,"phase":"cleaned","sessionId":"' + "a" * 32 + '","error":null}\n'
        failed = '{"schemaVersion":1,"kind":"omarchy-cast-guard-status","ok":false,"phase":"error","sessionId":"' + "a" * 32 + '","error":"cleanup incomplete"}\n'
        wrong = cleaned.replace("a" * 32, "b" * 32)
        self.assertTrue(GuardedTransportAdapter._cleanup_confirmed(process(), request, StatusDrainFixture(active + cleaned)))
        self.assertFalse(GuardedTransportAdapter._cleanup_confirmed(process(), request, StatusDrainFixture(active + failed)))
        self.assertFalse(GuardedTransportAdapter._cleanup_confirmed(process(), request, StatusDrainFixture(wrong)))
        self.assertFalse(GuardedTransportAdapter._cleanup_confirmed(process(exited=False), request, StatusDrainFixture(cleaned)))
        self.assertFalse(GuardedTransportAdapter._cleanup_confirmed(process(), request, StatusDrainFixture("x" * 65_537, overflowed=True)))
        self.assertFalse(GuardedTransportAdapter._cleanup_confirmed(process(), request, StatusDrainFixture("[" * 2_000 + "0" + "]" * 2_000)))

    def test_root_owned_terminal_status_requires_private_acknowledgment(self) -> None:
        from omarchy_cast.guard import GuardError, GuardRequest
        request = GuardRequest(1, "a" * 32, 1000, "wlan42", "00:11:22:33:44:55", 2437, 60)
        adapter = GuardedTransportAdapter(request, env={})
        cleaned = {
            "schemaVersion": 1, "kind": "omarchy-cast-guard-status", "ok": True,
            "phase": "cleaned", "sessionId": "a" * 32, "error": None,
        }
        failed = {**cleaned, "ok": False, "phase": "error", "error": "cleanup incomplete"}
        with (
            patch("omarchy_cast.transport.read_guard_status", side_effect=[None, cleaned]),
            patch("omarchy_cast.transport.time.sleep"),
            patch.object(adapter, "_write_private_marker", return_value=True) as acknowledge,
        ):
            self.assertTrue(adapter._wait_cleanup_status(timeout=1))
        acknowledge.assert_called_once_with(f"/run/omarchy-cast/{'a' * 32}/user/status-ack")
        with (
            patch("omarchy_cast.transport.read_guard_status", return_value=failed),
            patch.object(adapter, "_write_private_marker", return_value=True),
        ):
            self.assertFalse(adapter._wait_cleanup_status(timeout=1))
        with patch("omarchy_cast.transport.read_guard_status", side_effect=GuardError("unsafe")):
            self.assertFalse(adapter._wait_cleanup_status(timeout=1))

    def test_root_owned_helper_cleanup_does_not_mask_setup_failure(self) -> None:
        from omarchy_cast.guard import GuardRequest
        request = GuardRequest(1, "a" * 32, 1000, "wlan42", "00:11:22:33:44:55", 2437, 60)
        helper = Mock(stderr=io.StringIO(""))
        helper.poll.return_value = None
        helper.terminate.side_effect = PermissionError(1, "Operation not permitted")
        helper.wait.return_value = 2
        executable = executable_plan_fixture()
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
            "receiver rejected push-button pairing (Wi-Fi Direct status 10: incompatible provisioning method); this display may require PIN pairing": "pairing-method-unsupported",
            "receiver rejected the pairing PIN; check the displayed digits and try again": "pairing-pin-failed",
            "wpa_supplicant P2P group formation failed": "p2p-negotiation-failed",
            "Waiting for DHCP before timed out waiting for a direct supplicant P2P group": "p2p-negotiation-failed",
            "selected adapter has no unique NetworkManager P2P device": "network-backend-unavailable",
            "NetworkManager deactivated the Wi-Fi Direct connection": "network-backend-unavailable",
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
