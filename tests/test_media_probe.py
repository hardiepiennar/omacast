from __future__ import annotations

import unittest

from omarchy_cast.command import CommandResult
from omarchy_cast.media_probe import MediaProbeError, build_probe_command, probe_media


def snapshot(render_nodes: list[str] | None = None) -> dict[str, object]:
    return {"schemaVersion": 1, "checks": [{"name": "ffmpeg", "status": "ok"}], "renderNodes": render_nodes or []}


class MediaProbeTest(unittest.TestCase):
    def test_vaapi_probe_is_synthetic_bounded_and_has_no_network_target(self) -> None:
        command = build_probe_command(profile="safe", render_node="/dev/dri/renderD128")
        self.assertIn("testsrc2=size=1280x720:rate=60", command)
        self.assertIn("-frames:v", command)
        self.assertIn("h264_vaapi", command)
        self.assertEqual(command[-2:], ("null", "-"))
        self.assertNotIn("wf-recorder", command)

    def test_probe_reports_runner_failure_without_fallback(self) -> None:
        def runner(args, *, timeout=5.0):
            return CommandResult(tuple(args), 1, "", "VAAPI init failed")
        result = probe_media(snapshot(["/dev/dri/renderD128"]), profile="safe", runner=runner)
        self.assertFalse(result["ok"])
        self.assertEqual((result["encoder"], result["error"]), ("vaapi", "VAAPI init failed"))

    def test_missing_ffmpeg_is_rejected(self) -> None:
        with self.assertRaisesRegex(MediaProbeError, "FFmpeg"):
            probe_media({"schemaVersion": 1, "checks": [], "renderNodes": []}, profile="safe")
