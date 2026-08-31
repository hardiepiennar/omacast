"""Read-only translation from a host snapshot to the supported FluxCast launch."""

from __future__ import annotations

from typing import Any

from .identity import receiver_address


class LaunchPlanError(ValueError):
    """The host snapshot cannot safely describe a supported cast launch."""


PROFILES: dict[str, dict[str, int | str]] = {
    "safe": {"label": "Safe", "width": 1280, "height": 720, "fps": 60, "bitrateMbps": 7},
}
SOURCES = frozenset({"display"})


def _required_tools(snapshot: dict[str, Any]) -> None:
    available = {str(check.get("name")): check.get("status") == "ok" for check in snapshot.get("checks", []) if isinstance(check, dict)}
    missing = [name for name in ("fluxcast", "nmcli", "gpu-screen-recorder", "ffmpeg") if not available.get(name)]
    if missing:
        raise LaunchPlanError("required tools unavailable: " + ", ".join(missing))


def _select_wifi(snapshot: dict[str, Any]) -> dict[str, Any]:
    links = snapshot.get("wifiLinks", [])
    if not isinstance(links, list):
        raise LaunchPlanError("Wi-Fi discovery is invalid")
    for link in links:
        if isinstance(link, dict) and link.get("connected") is True and isinstance(link.get("interface"), str):
            return link
    raise LaunchPlanError("no connected managed Wi-Fi interface found")


def _select_monitor(snapshot: dict[str, Any], requested: str | None) -> dict[str, Any]:
    monitors = snapshot.get("monitors", [])
    if not isinstance(monitors, list):
        raise LaunchPlanError("monitor discovery is invalid")
    candidates = [monitor for monitor in monitors if isinstance(monitor, dict) and isinstance(monitor.get("name"), str)]
    if requested:
        candidates = [monitor for monitor in candidates if monitor["name"] == requested]
    if not candidates:
        raise LaunchPlanError("requested monitor is not available" if requested else "no Hyprland monitor found")
    return next((monitor for monitor in candidates if monitor.get("focused") is True), candidates[0])


def build_launch_plan(
    snapshot: dict[str, Any], *, peer: str, mode: str, profile: str,
    monitor: str | None = None, source: str = "display",
) -> dict[str, object]:
    """Create a JSON-safe command preview, without touching hardware or a peer."""
    if type(snapshot.get("schemaVersion")) is not int or snapshot.get("schemaVersion") != 1:
        raise LaunchPlanError("unsupported discovery schema")
    try:
        peer = receiver_address(peer)
    except ValueError as exc:
        raise LaunchPlanError(str(exc)) from exc
    if mode != "mirror":
        raise LaunchPlanError("unsupported output mode")
    if profile not in PROFILES:
        raise LaunchPlanError("unsupported quality profile")
    if source not in SOURCES:
        raise LaunchPlanError("unsupported capture source")
    _required_tools(snapshot)
    wifi = _select_wifi(snapshot)
    output = _select_monitor(snapshot, monitor)
    sink = snapshot.get("defaultSink")
    if not isinstance(sink, str) or not sink:
        raise LaunchPlanError("no default PipeWire sink found")

    selected_profile = dict(PROFILES[profile])
    render_nodes = snapshot.get("renderNodes")
    vaapi = type(render_nodes) is list and any(type(node) is str and node for node in render_nodes)
    encoder = "vaapi" if vaapi else "libx264"
    warnings: list[str] = []
    frequency = wifi.get("frequency_mhz")
    if type(frequency) is int and frequency >= 5000:
        warnings.append("Current Wi-Fi is on 5/6 GHz; P2P channel selection will remain automatic.")
    supplicant_frequency = frequency if type(frequency) is int and 2400 <= frequency <= 2500 else 0
    if not vaapi:
        warnings.append("No render node detected; this session would use software H.264 encoding.")

    command = [
        "fluxcast", "--protocol", "wfd", "--output-res", f"{selected_profile['width']}x{selected_profile['height']}",
        "--fps", str(selected_profile["fps"]), "--bitrate", f"{selected_profile['bitrateMbps']}M",
        "--wfd-video-encoder", encoder, "--wfd-p2p-backend", "supplicant",
        "--wfd-supplicant-mode", "connect", "--wfd-peer", peer,
        "--wfd-interface", str(wifi["interface"]), "--wfd-timeout", "15",
        "--wfd-supplicant-frequency", str(supplicant_frequency),
        "--wfd-no-firewall",
        "--monitor", str(output["name"]),
        "--wfd-capture-backend", "gpu-screen-recorder",
        "--wfd-audio-device", sink + ".monitor",
    ]
    return {
        "schemaVersion": 1,
        "readOnly": True,
        "kind": "launch-plan",
        "execution": {"allowed": False, "reason": "read-only launch preview"},
        "profile": selected_profile,
        "selection": {
            "peer": peer, "mode": mode, "source": source, "wifiInterface": wifi["interface"],
            "wifiFrequencyMhz": frequency, "p2pFrequencyMhz": supplicant_frequency,
            "monitor": output["name"], "audioSource": sink + ".monitor",
            "videoEncoder": encoder,
        },
        "command": command,
        "warnings": warnings,
    }
