"""Bounded local encoder probe: synthetic frames only, never capture or network."""

from __future__ import annotations

from typing import Any

from .command import Runner, run_command
from .engine import PROFILES


class MediaProbeError(ValueError):
    pass


def build_probe_command(*, profile: str, render_node: str | None) -> tuple[str, ...]:
    if profile not in PROFILES:
        raise MediaProbeError("unsupported media profile")
    settings = PROFILES[profile]
    width, height, fps = int(settings["width"]), int(settings["height"]), int(settings["fps"])
    command: list[str] = ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error"]
    if render_node:
        command.extend(["-vaapi_device", render_node])
    command.extend(["-f", "lavfi", "-i", f"testsrc2=size={width}x{height}:rate={fps}", "-frames:v", str(fps), "-an"])
    if render_node:
        command.extend(["-vf", "format=nv12,hwupload", "-c:v", "h264_vaapi"])
    else:
        command.extend(["-c:v", "libx264", "-preset", "veryfast"])
    command.extend(["-f", "null", "-"])
    return tuple(command)


def probe_media(snapshot: dict[str, Any], *, profile: str, runner: Runner = run_command) -> dict[str, object]:
    """Encode exactly one synthetic second to FFmpeg's null muxer."""
    if type(snapshot.get("schemaVersion")) is not int or snapshot.get("schemaVersion") != 1:
        raise MediaProbeError("unsupported discovery schema")
    checks = {str(item.get("name")): item.get("status") for item in snapshot.get("checks", []) if isinstance(item, dict)}
    if checks.get("ffmpeg") != "ok":
        raise MediaProbeError("FFmpeg is unavailable")
    render_nodes = snapshot.get("renderNodes", [])
    render_node = render_nodes[0] if isinstance(render_nodes, list) and render_nodes and isinstance(render_nodes[0], str) else None
    command = build_probe_command(profile=profile, render_node=render_node)
    result = runner(command, timeout=20.0)
    settings = PROFILES[profile]
    detail = (result.stderr.strip() or result.stdout.strip()).splitlines()
    return {
        "schemaVersion": 1,
        "kind": "local-media-probe",
        "ok": result.returncode == 0,
        "profile": profile,
        "encoder": "vaapi" if render_node else "libx264",
        "frames": int(settings["fps"]),
        "synthetic": True,
        "network": False,
        "screenCapture": False,
        "error": None if result.returncode == 0 else (detail[0][:240] if detail else f"FFmpeg exited {result.returncode}"),
    }
