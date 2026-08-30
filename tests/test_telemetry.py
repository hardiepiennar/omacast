from __future__ import annotations

import json
import fcntl
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import unittest.mock

from omarchy_cast.bounds import MAX_TELEMETRY_BYTES
from omarchy_cast.telemetry import (
    BoundedOutputCollector,
    MAX_DESCENDANT_PROCESSES,
    MAX_ARCHIVED_TELEMETRY_BYTES,
    MAX_ENGINE_LOG_BYTES,
    MAX_SYSFS_INTERFACE_ENTRIES,
    TelemetrySampler,
    TelemetryWorkspace,
    _bounded_read,
    _bounded_stream_read,
    cleanup_live_telemetry,
    parse_ffmpeg_progress,
    parse_iw_station,
    read_telemetry,
    telemetry_paths,
)


class TelemetryTest(unittest.TestCase):
    def test_progress_parser_uses_last_complete_record(self) -> None:
        progress = parse_ffmpeg_progress(
            "frame=10\nout_time_us=333333\nprogress=continue\n"
            "frame=20\nout_time_us=666666\ndup_frames=1\ndrop_frames=0\nprogress=continue\n"
            "frame=21\n"
        )
        self.assertEqual(progress["frame"], "20")
        self.assertEqual(progress["dup_frames"], "1")

    def test_station_parser_keeps_only_pipeline_health_signals(self) -> None:
        station = parse_iw_station(
            "tx retries:\t3\ntx failed:\t1\nbeacon loss:\t0\n"
            "signal:\t-55 [-59, -57] dBm\ntx bitrate:\t117.0 MBit/s\n"
        )
        self.assertEqual(station["txRetries"], 3)
        self.assertEqual(station["txFailed"], 1)
        self.assertEqual(station["signalDbm"], -55)
        self.assertEqual(station["txBitrateMbps"], 117)

    def test_station_parser_rejects_partial_wide_and_nonfinite_numbers(self) -> None:
        station = parse_iw_station(
            f"tx retries:\t{'9' * 10_000}\n"
            "tx failed:\tInfinity\n"
            "signal:\t-55.1234567890 dBm\n"
            "tx bitrate:\t1e999 MBit/s\n"
        )
        self.assertEqual(station, {})

    def test_live_snapshot_is_private_versioned_and_session_scoped(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            environment = {"XDG_RUNTIME_DIR": str(Path(temp) / "run"), "XDG_STATE_HOME": str(Path(temp) / "state")}
            session_id = "a" * 32
            paths = telemetry_paths(session_id, environment)
            self.assertNotIn("qos", paths)
            payload = {"schemaVersion": 1, "sessionId": session_id, "health": {"status": "healthy"}}
            paths["current"].write_text(json.dumps(payload))
            paths["current"].chmod(0o600)
            self.assertEqual(read_telemetry(session_id, environment), payload)
            paths["current"].write_text(json.dumps({**payload, "schemaVersion": True}))
            self.assertIsNone(read_telemetry(session_id, environment))
            paths["current"].write_text("[" * 2_000 + "0" + "]" * 2_000, encoding="ascii")
            self.assertIsNone(read_telemetry(session_id, environment))
            with self.assertRaisesRegex(ValueError, "controller-issued"):
                telemetry_paths("unsafe", environment)

    def test_oversized_or_linked_live_snapshot_is_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            environment = {"XDG_RUNTIME_DIR": str(Path(temp) / "run"), "XDG_STATE_HOME": str(Path(temp) / "state")}
            session_id = "c" * 32
            paths = telemetry_paths(session_id, environment)
            paths["current"].write_bytes(b"{" + b" " * MAX_TELEMETRY_BYTES + b"}")
            paths["current"].chmod(0o600)
            self.assertIsNone(read_telemetry(session_id, environment))
            paths["current"].unlink()
            target = Path(temp) / "telemetry.json"
            target.write_text(json.dumps({"schemaVersion": 1, "sessionId": session_id}), encoding="utf-8")
            paths["current"].symlink_to(target)
            self.assertIsNone(read_telemetry(session_id, environment))

    def test_live_snapshot_fifo_is_ignored_without_blocking_on_open(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            environment = {"XDG_RUNTIME_DIR": str(Path(temp) / "run"), "XDG_STATE_HOME": str(Path(temp) / "state")}
            session_id = "f" * 32
            path = telemetry_paths(session_id, environment)["current"]
            os.mkfifo(path, mode=0o600)
            probe = (
                "import sys\n"
                "from omarchy_cast.telemetry import read_telemetry\n"
                "environment = {'XDG_RUNTIME_DIR': sys.argv[1], 'XDG_STATE_HOME': sys.argv[2]}\n"
                "if read_telemetry(sys.argv[3], environment) is not None:\n"
                "    raise SystemExit('FIFO was accepted as current telemetry')\n"
            )
            result = subprocess.run(
                (sys.executable, "-c", probe, environment["XDG_RUNTIME_DIR"], environment["XDG_STATE_HOME"], session_id),
                check=False,
                capture_output=True,
                text=True,
                timeout=2,
            )
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_telemetry_rejects_symlinked_private_directories(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            runtime = Path(temp) / "run"
            runtime.mkdir(mode=0o700)
            product = runtime / "omarchy-cast"
            product.mkdir(mode=0o700)
            target = Path(temp) / "unrelated"
            target.mkdir(mode=0o700)
            (product / "telemetry").symlink_to(target, target_is_directory=True)
            environment = {"XDG_RUNTIME_DIR": str(runtime), "XDG_STATE_HOME": str(Path(temp) / "state")}

            with self.assertRaises(OSError):
                telemetry_paths("a" * 32, environment)

            self.assertEqual(list(target.iterdir()), [])

    def test_existing_owned_telemetry_directories_are_tightened_by_descriptor(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            runtime = Path(temp) / "run"
            runtime.mkdir(mode=0o700)
            product = runtime / "omarchy-cast"
            live = product / "telemetry"
            session = live / ("a" * 32)
            session.mkdir(mode=0o755, parents=True)
            product.chmod(0o700)
            live.chmod(0o755)
            state_product = Path(temp) / "state" / "omarchy-cast"
            archive = state_product / "telemetry"
            archive.mkdir(mode=0o755, parents=True)
            state_product.chmod(0o755)
            environment = {"XDG_RUNTIME_DIR": str(runtime), "XDG_STATE_HOME": str(Path(temp) / "state")}

            telemetry_paths("a" * 32, environment)

            for directory in (live, session, state_product, archive):
                self.assertEqual(directory.stat().st_mode & 0o777, 0o700)

    def test_pinned_workspace_cannot_be_redirected_by_directory_replacement(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            environment = {"XDG_RUNTIME_DIR": str(Path(temp) / "run"), "XDG_STATE_HOME": str(Path(temp) / "state")}
            session_id = "a" * 32
            workspace = TelemetryWorkspace(session_id, environment)
            live = workspace.paths["current"].parent
            detached = live.with_name("detached")
            replacement = Path(temp) / "replacement"
            replacement.mkdir(mode=0o700)
            live.rename(detached)
            live.symlink_to(replacement, target_is_directory=True)
            try:
                workspace.write_current({"schemaVersion": 1, "sessionId": session_id})
            finally:
                workspace.close()

            self.assertFalse((replacement / "current.json").exists())
            self.assertTrue((detached / "current.json").is_file())

    def test_engine_output_paths_remain_bound_to_preopened_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            environment = {"XDG_RUNTIME_DIR": str(Path(temp) / "run"), "XDG_STATE_HOME": str(Path(temp) / "state")}
            workspace = TelemetryWorkspace("a" * 32, environment)
            engine_paths = workspace.prepare_engine_outputs()
            visible = workspace.paths["progress"]
            target = Path(temp) / "unrelated-progress"
            target.write_text("preserve", encoding="utf-8")
            visible.unlink()
            visible.symlink_to(target)
            try:
                child = (
                    "import subprocess,sys\n"
                    "result = subprocess.run((sys.executable, '-c', "
                    "'import pathlib,sys; pathlib.Path(sys.argv[1]).write_text(sys.argv[2])', "
                    "sys.argv[1], 'frame=1\\nprogress=continue\\n'))\n"
                    "raise SystemExit(result.returncode)\n"
                )
                result = subprocess.run(
                    (sys.executable, "-c", child, str(engine_paths["progress"])),
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=2,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("frame=1", workspace.read_text("progress"))
            finally:
                workspace.close()

            self.assertEqual(target.read_text(encoding="utf-8"), "preserve")

    def test_engine_outputs_can_only_be_prepared_once(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            environment = {"XDG_RUNTIME_DIR": str(Path(temp) / "run"), "XDG_STATE_HOME": str(Path(temp) / "state")}
            workspace = TelemetryWorkspace("a" * 32, environment)
            try:
                workspace.prepare_engine_outputs()
                with self.assertRaisesRegex(ValueError, "already prepared"):
                    workspace.prepare_engine_outputs()
            finally:
                workspace.close()

    def test_archive_append_rejects_a_symlink_without_changing_its_target(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            environment = {"XDG_RUNTIME_DIR": str(Path(temp) / "run"), "XDG_STATE_HOME": str(Path(temp) / "state")}
            workspace = TelemetryWorkspace("a" * 32, environment)
            target = Path(temp) / "unrelated-archive"
            target.write_text("preserve", encoding="utf-8")
            workspace.paths["samples"].symlink_to(target)
            try:
                with self.assertRaises(OSError):
                    workspace.append_sample({"schemaVersion": 1})
            finally:
                workspace.close()
            self.assertEqual(target.read_text(encoding="utf-8"), "preserve")

    def test_archive_append_rejects_public_mode_without_repairing_it(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            environment = {"XDG_RUNTIME_DIR": str(Path(temp) / "run"), "XDG_STATE_HOME": str(Path(temp) / "state")}
            workspace = TelemetryWorkspace("a" * 32, environment)
            workspace.paths["samples"].write_text("preserve\n", encoding="utf-8")
            workspace.paths["samples"].chmod(0o644)
            try:
                with self.assertRaisesRegex(ValueError, "archive is unsafe"):
                    workspace.append_sample({"schemaVersion": 1})
            finally:
                workspace.close()
            self.assertEqual(workspace.paths["samples"].read_text(encoding="utf-8"), "preserve\n")
            self.assertEqual(workspace.paths["samples"].stat().st_mode & 0o777, 0o644)

    def test_archive_append_has_a_bounded_lock_and_shape_budget(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            environment = {"XDG_RUNTIME_DIR": str(Path(temp) / "run"), "XDG_STATE_HOME": str(Path(temp) / "state")}
            workspace = TelemetryWorkspace("a" * 32, environment)
            workspace.paths["samples"].write_text("", encoding="utf-8")
            workspace.paths["samples"].chmod(0o600)
            try:
                with workspace.paths["samples"].open("rb") as locked:
                    fcntl.flock(locked.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    started = time.monotonic()
                    with self.assertRaisesRegex(ValueError, "archive is busy"):
                        workspace.append_sample({"schemaVersion": 1})
                    self.assertLess(time.monotonic() - started, 0.75)
                with self.assertRaisesRegex(ValueError, "non-finite"):
                    workspace.append_sample({"schemaVersion": 1, "value": float("inf")})
            finally:
                workspace.close()

    def test_proc_stream_read_reports_truncation(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "records"
            path.write_text("abcdef", encoding="ascii")
            self.assertEqual(_bounded_stream_read(path, 6), ("abcdef", True))
            self.assertEqual(_bounded_stream_read(path, 5), ("abcde", False))

    def test_sampler_stop_does_not_close_a_workspace_while_its_thread_is_live(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            environment = {"XDG_RUNTIME_DIR": str(Path(temp) / "run"), "XDG_STATE_HOME": str(Path(temp) / "state")}
            sampler = TelemetrySampler(session_id="a" * 32, engine_pid=999999, wifi_interface="wlan42", environ=environment)
            gate = threading.Event()
            sampler._run = lambda: gate.wait(1.0)  # type: ignore[method-assign]
            sampler.start()
            self.assertFalse(sampler.stop(timeout=0.01))
            self.assertGreaterEqual(sampler._workspace.live_descriptor, 0)
            gate.set()
            self.assertTrue(sampler.stop(timeout=1.0))

    def test_archive_stops_at_its_quota_without_stopping_live_snapshots(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            environment = {"XDG_RUNTIME_DIR": str(Path(temp) / "run"), "XDG_STATE_HOME": str(Path(temp) / "state")}
            workspace = TelemetryWorkspace("a" * 32, environment)
            try:
                payload = {"schemaVersion": 1, "sample": "x" * 1024}
                while workspace.append_sample(payload):
                    pass
                self.assertTrue(workspace.archive_capped)
                self.assertLessEqual(workspace.paths["samples"].stat().st_size, MAX_ARCHIVED_TELEMETRY_BYTES)
                workspace.write_current({"schemaVersion": 1, "historyCapped": True})
                self.assertIn("historyCapped", workspace.paths["current"].read_text())
            finally:
                workspace.close()

    def test_engine_output_collector_drains_and_keeps_only_its_tail(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            environment = {"XDG_RUNTIME_DIR": str(Path(temp) / "run"), "XDG_STATE_HOME": str(Path(temp) / "state")}
            workspace = TelemetryWorkspace("a" * 32, environment)
            workspace.prepare_engine_outputs()
            read_descriptor, write_descriptor = os.pipe()
            stream = os.fdopen(read_descriptor, "rb", buffering=0)
            collector = BoundedOutputCollector(stream, workspace)
            collector.start()
            try:
                os.write(write_descriptor, b"old\n" + b"x" * MAX_ENGINE_LOG_BYTES)
                os.write(write_descriptor, b"\nlatest failure\n")
            finally:
                os.close(write_descriptor)
            collector.stop()
            try:
                retained = workspace.read_text("engineLog", MAX_ENGINE_LOG_BYTES)
                self.assertNotIn("old", retained)
                self.assertTrue(retained.endswith("latest failure\n"))
                self.assertLessEqual(workspace.paths["engineLog"].stat().st_size, MAX_ENGINE_LOG_BYTES)
            finally:
                workspace.close()

    def test_engine_output_collector_adds_bounded_identifier_free_startup_markers(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            environment = {"XDG_RUNTIME_DIR": str(Path(temp) / "run"), "XDG_STATE_HOME": str(Path(temp) / "state")}
            workspace = TelemetryWorkspace("a" * 32, environment)
            workspace.prepare_engine_outputs()
            read_descriptor, write_descriptor = os.pipe()
            collector = BoundedOutputCollector(os.fdopen(read_descriptor, "rb", buffering=0), workspace)
            try:
                collector.start()
                collector.note_startup("rtsp-established", 1.2345)
                os.close(write_descriptor)
                collector.stop()
                self.assertIn(
                    "[Omacast startup] milestone=rtsp-established elapsed_ms=1234",
                    workspace.read_text("engineLog"),
                )
                with self.assertRaisesRegex(ValueError, "milestone is invalid"):
                    collector.note_startup("peer=00:11:22:33:44:55", 1)
            finally:
                workspace.close()

    def test_engine_output_collector_stops_while_writer_remains_open(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            environment = {"XDG_RUNTIME_DIR": str(Path(temp) / "run"), "XDG_STATE_HOME": str(Path(temp) / "state")}
            workspace = TelemetryWorkspace("a" * 32, environment)
            workspace.prepare_engine_outputs()
            read_descriptor, write_descriptor = os.pipe()
            collector = BoundedOutputCollector(os.fdopen(read_descriptor, "rb", buffering=0), workspace)
            try:
                collector.start()
                started = time.monotonic()
                collector.stop()
                self.assertLess(time.monotonic() - started, 1)
            finally:
                os.close(write_descriptor)
                workspace.close()

    def test_engine_output_collector_keeps_its_preopened_output_after_path_replacement(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            environment = {"XDG_RUNTIME_DIR": str(Path(temp) / "run"), "XDG_STATE_HOME": str(Path(temp) / "state")}
            workspace = TelemetryWorkspace("a" * 32, environment)
            workspace.prepare_engine_outputs()
            visible = workspace.paths["engineLog"]
            visible.unlink()
            visible.write_text("preserve", encoding="utf-8")
            visible.chmod(0o600)
            read_descriptor, write_descriptor = os.pipe()
            collector = BoundedOutputCollector(os.fdopen(read_descriptor, "rb", buffering=0), workspace)
            collector.start()
            try:
                os.write(write_descriptor, b"latest diagnostic\n")
            finally:
                os.close(write_descriptor)
            collector.stop()
            try:
                self.assertEqual(workspace.read_text("engineLog"), "latest diagnostic\n")
                self.assertEqual(visible.read_text(encoding="utf-8"), "preserve")
            finally:
                workspace.close()

    def test_bounded_reader_rejects_fifo_and_hardlink_without_blocking(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            fifo = root / "fifo"
            os.mkfifo(fifo, mode=0o600)
            self.assertEqual(_bounded_read(fifo), "")
            target = root / "target"
            target.write_text("preserve", encoding="utf-8")
            target.chmod(0o600)
            linked = root / "linked"
            os.link(target, linked)
            self.assertEqual(_bounded_read(linked, require_single_link=True), "")
            self.assertEqual(target.read_text(encoding="utf-8"), "preserve")

    def test_periodic_process_tree_and_baseline_cache_are_bounded(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            proc_root = Path(temp)
            task = proc_root / "100" / "task" / "100"
            task.mkdir(parents=True)
            task.joinpath("children").write_text(
                " ".join(str(pid) for pid in range(101, 1_000)), encoding="ascii",
            )
            sampler = TelemetrySampler(
                session_id="a" * 32, engine_pid=100, wifi_interface="wlan42",
                environ={"XDG_RUNTIME_DIR": str(Path(temp) / "run"), "XDG_STATE_HOME": str(Path(temp) / "state")},
            )
            descendants = sampler._descendants(proc_root)
            self.assertEqual(descendants[0], 100)
            self.assertEqual(len(descendants), MAX_DESCENDANT_PROCESSES)

            sampler._previous_process = {100: (0, 0, 0, 0, 0, 0, 0), 999: (0, 0, 0, 0, 0, 0, 0)}
            with unittest.mock.patch.object(sampler, "_descendants", return_value=[100]), unittest.mock.patch.object(
                sampler, "_process", return_value=None,
            ):
                sampler._processes(1.0)
            self.assertEqual(set(sampler._previous_process), {100})
            sampler.stop()

    def test_periodic_sysfs_enumeration_and_counters_are_bounded(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "net"
            root.mkdir()
            for index in range(MAX_SYSFS_INTERFACE_ENTRIES):
                (root / f"p2p-wlan42-flood-{index}").write_text("not an interface", encoding="utf-8")
            actual = root / "p2p-wlan42-z-real"
            (actual / "statistics").mkdir(parents=True)
            (actual / "operstate").write_text("up\n", encoding="ascii")
            (actual / "statistics" / "tx_bytes").write_text("123\n", encoding="ascii")
            sampler = TelemetrySampler(
                session_id="a" * 32, engine_pid=999999, wifi_interface="wlan42",
                environ={"XDG_RUNTIME_DIR": str(Path(temp) / "run"), "XDG_STATE_HOME": str(Path(temp) / "state")},
            )
            ordered_root = unittest.mock.Mock()
            ordered_root.glob.return_value = [
                *(root / f"p2p-wlan42-flood-{index}" for index in range(MAX_SYSFS_INTERFACE_ENTRIES)),
                actual,
            ]
            self.assertIsNone(sampler._p2p_interface(ordered_root))
            self.assertEqual(sampler._counter(actual.name, "tx_bytes", root), 123)
            (actual / "statistics" / "tx_bytes").write_text("9" * 10_000, encoding="ascii")
            self.assertEqual(sampler._counter(actual.name, "tx_bytes", root), 0)
            sampler.stop()

    def test_process_numeric_records_reject_oversized_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            proc_root = Path(temp) / "proc"
            proc = proc_root / "100"
            proc.mkdir(parents=True)
            fields = ["S", *("0" for _ in range(21))]
            fields[11] = "9" * 10_000
            (proc / "stat").write_text(f"100 (engine) {' '.join(fields)}\n", encoding="ascii")
            (proc / "schedstat").write_text("0 0 0\n", encoding="ascii")
            sampler = TelemetrySampler(
                session_id="a" * 32, engine_pid=100, wifi_interface="wlan42",
                environ={"XDG_RUNTIME_DIR": str(Path(temp) / "run"), "XDG_STATE_HOME": str(Path(temp) / "state")},
            )
            with unittest.mock.patch("omarchy_cast.telemetry.Path", side_effect=lambda value: proc_root if value == "/proc" else Path(value)):
                self.assertIsNone(sampler._process(100, 1.0))
            sampler.stop()

    def test_packet_timing_reports_audio_video_gaps_and_skew(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            environment = {"XDG_RUNTIME_DIR": str(Path(temp) / "run"), "XDG_STATE_HOME": str(Path(temp) / "state")}
            sampler = TelemetrySampler(session_id="a" * 32, engine_pid=999999, wifi_interface="wlan42", environ=environment)
            sampler.paths["packets"].write_text(
                "#tb 0: 1/1000\n#tb 1: 1/48000\n"
                "0,0,0,33,1000,0x0\n1,0,0,1024,300,0x0\n"
                "1,1024,1024,1024,300,0x0\n0,33,33,33,1000,0x0\n"
            )
            sampler.paths["packets"].chmod(0o600)
            timing = sampler._packet_timing()
            self.assertEqual(timing["video"]["maxGapMs"], 33.0)
            self.assertEqual(timing["audio"]["maxGapMs"], 21.333)
            self.assertEqual(timing["avSkewMs"], 11.667)

    def test_packet_and_progress_numbers_are_lexically_and_numerically_bounded(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            environment = {"XDG_RUNTIME_DIR": str(Path(temp) / "run"), "XDG_STATE_HOME": str(Path(temp) / "state")}
            sampler = TelemetrySampler(session_id="a" * 32, engine_pid=999999, wifi_interface="wlan42", environ=environment)
            huge = "9" * 10_000
            sampler.paths["packets"].write_text(
                f"#tb 0: {huge}/1\n0,0,{huge},33,{huge},0x0\n", encoding="utf-8",
            )
            sampler.paths["packets"].chmod(0o600)
            sampler.paths["progress"].write_text(
                f"frame={huge}\nout_time_us={huge}\nfps=1e999\nprogress=continue\n", encoding="utf-8",
            )
            sampler.paths["progress"].chmod(0o600)
            self.assertEqual(sampler._packet_timing()["video"]["packets"], 0)
            output = sampler._output(1.0)
            self.assertEqual(output["frame"], 0)
            self.assertEqual(output["reportedFps"], 0.0)
            sampler.stop()

    def test_negotiated_mode_numbers_are_bounded(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            environment = {"XDG_RUNTIME_DIR": str(Path(temp) / "run"), "XDG_STATE_HOME": str(Path(temp) / "state")}
            sampler = TelemetrySampler(session_id="a" * 32, engine_pid=999999, wifi_interface="wlan42", environ=environment)
            sampler.paths["latency"].write_text(
                "[" * 2_000 + "0" + "]" * 2_000 + "\n"
                + json.dumps({"event": "media_starting", "mode": "99999x1080p60", "tv_ip": "192.0.2.1"}) + "\n",
                encoding="utf-8",
            )
            sampler.paths["latency"].chmod(0o600)
            self.assertEqual(sampler._negotiated(), {"mode": "99999x1080p60", "tvIp": "192.0.2.1"})
            sampler.stop()

    def test_finished_session_cleanup_removes_only_known_live_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            environment = {"XDG_RUNTIME_DIR": str(Path(temp) / "run"), "XDG_STATE_HOME": str(Path(temp) / "state")}
            session_id = "a" * 32
            paths = telemetry_paths(session_id, environment)
            paths["current"].write_text("{}")
            paths["engineLog"].write_text("bounded")
            self.assertTrue(cleanup_live_telemetry(session_id, environment))
            self.assertFalse(paths["current"].parent.exists())
            with self.assertRaisesRegex(ValueError, "controller-issued"):
                cleanup_live_telemetry("unsafe", environment)

    def test_cleanup_refuses_to_remove_a_directory_with_unknown_content(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            environment = {"XDG_RUNTIME_DIR": str(Path(temp) / "run"), "XDG_STATE_HOME": str(Path(temp) / "state")}
            session_id = "b" * 32
            paths = telemetry_paths(session_id, environment)
            unexpected = paths["current"].parent / "unowned.txt"
            unexpected.write_text("preserve")
            self.assertFalse(cleanup_live_telemetry(session_id, environment))
            self.assertEqual(unexpected.read_text(), "preserve")

    def test_one_unsafe_live_entry_does_not_shield_other_known_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            environment = {"XDG_RUNTIME_DIR": str(Path(temp) / "run"), "XDG_STATE_HOME": str(Path(temp) / "state")}
            session_id = "c" * 32
            paths = telemetry_paths(session_id, environment)
            paths["current"].mkdir()
            paths["engineLog"].write_text("remove", encoding="utf-8")
            self.assertFalse(cleanup_live_telemetry(session_id, environment))
            self.assertTrue(paths["current"].is_dir())
            self.assertFalse(paths["engineLog"].exists())
