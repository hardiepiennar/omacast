from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from omarchy_cast.bounds import MAX_TELEMETRY_BYTES
from omarchy_cast.telemetry import TelemetrySampler, cleanup_live_telemetry, parse_ffmpeg_progress, parse_iw_station, read_telemetry, telemetry_paths


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

    def test_packet_timing_reports_audio_video_gaps_and_skew(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            environment = {"XDG_RUNTIME_DIR": str(Path(temp) / "run"), "XDG_STATE_HOME": str(Path(temp) / "state")}
            sampler = TelemetrySampler(session_id="a" * 32, engine_pid=999999, wifi_interface="wlan42", environ=environment)
            sampler.paths["packets"].write_text(
                "#tb 0: 1/1000\n#tb 1: 1/48000\n"
                "0,0,0,33,1000,0x0\n1,0,0,1024,300,0x0\n"
                "1,1024,1024,1024,300,0x0\n0,33,33,33,1000,0x0\n"
            )
            timing = sampler._packet_timing()
            self.assertEqual(timing["video"]["maxGapMs"], 33.0)
            self.assertEqual(timing["audio"]["maxGapMs"], 21.333)
            self.assertEqual(timing["avSkewMs"], 11.667)

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
