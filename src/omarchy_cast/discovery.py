"""Read-only host discovery for the production controller."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
import shutil
from typing import Callable

from .bounds import bounded_text
from .command import CommandResult, Runner, run_command


MAX_WIFI_DEVICES = 32
MAX_MONITORS = 16
MAX_DIAGNOSTICS = 16


@dataclass(frozen=True)
class WifiDevice:
    name: str
    type: str
    state: str


@dataclass(frozen=True)
class WifiLink:
    interface: str
    connected: bool
    ssid: str | None
    frequency_mhz: int | None


@dataclass(frozen=True)
class Monitor:
    name: str
    description: str
    width: int
    height: int
    refresh_rate: float
    focused: bool


def parse_nmcli_devices(text: str) -> list[WifiDevice]:
    """Parse nmcli's terse DEVICE:TYPE:STATE output without naming an adapter."""
    devices: list[WifiDevice] = []
    for line in text.splitlines():
        fields = line.split(":", 2)
        if len(fields) != 3 or not fields[0]:
            continue
        name, device_type, state = fields
        if device_type in {"wifi", "wifi-p2p"}:
            if len(devices) >= MAX_WIFI_DEVICES:
                break
            if len(name) > 64 or any(ord(character) < 32 or ord(character) == 127 for character in name):
                continue
            devices.append(WifiDevice(name=name, type=device_type, state=state))
    return devices


def parse_hyprland_monitors(text: str) -> list[Monitor]:
    """Read Hyprland's documented JSON surface; malformed rows are ignored."""
    try:
        entries = json.loads(text)
    except json.JSONDecodeError:
        return []
    if not isinstance(entries, list):
        return []

    monitors: list[Monitor] = []
    for entry in entries:
        if len(monitors) >= MAX_MONITORS:
            break
        if not isinstance(entry, dict):
            continue
        name = entry.get("name")
        width = entry.get("width")
        height = entry.get("height")
        refresh = entry.get("refreshRate", 0)
        if not isinstance(name, str) or not isinstance(width, int) or not isinstance(height, int):
            continue
        if not name or len(name) > 128 or any(ord(character) < 32 or ord(character) == 127 for character in name):
            continue
        if width <= 0 or height <= 0:
            continue
        monitors.append(
            Monitor(
                name=name,
                description=bounded_text(entry.get("description") if isinstance(entry.get("description"), str) else name, limit=240, fallback=name),
                width=width,
                height=height,
                refresh_rate=float(refresh) if isinstance(refresh, (int, float)) else 0.0,
                focused=bool(entry.get("focused")),
            )
        )
    return monitors


def parse_iw_link(interface: str, text: str) -> WifiLink:
    """Parse the connection summary from `iw dev <interface> link`."""
    if "Not connected." in text:
        return WifiLink(interface=interface, connected=False, ssid=None, frequency_mhz=None)
    ssid: str | None = None
    frequency_mhz: int | None = None
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if line.startswith("SSID:"):
            candidate = line.removeprefix("SSID:").strip()
            ssid = bounded_text(candidate, limit=128) or None
        elif line.startswith("freq:"):
            candidate = line.removeprefix("freq:").strip()
            try:
                frequency_mhz = int(float(candidate))
            except ValueError:
                pass
    return WifiLink(interface=interface, connected=True, ssid=ssid, frequency_mhz=frequency_mhz)


REQUIRED_COMMANDS = (
    "fluxcast", "nmcli", "iw", "hyprctl", "pactl", "gpu-screen-recorder",
    "ffmpeg", "systemd-run", "systemd-inhibit", "pkexec", "wpa_supplicant",
)
COMPANION_COMMANDS = frozenset(name for name in REQUIRED_COMMANDS if name != "hyprctl")
HELPER_NAMES = ("omarchy-cast-guard", "omarchy-cast-guard-recover")
GUARD_API_REVISION = 7


def _check_command(name: str, finder: Callable[[str], str | None]) -> dict[str, object]:
    path = finder(name)
    return {"name": name, "status": "ok" if path else "missing", "path": path}


def _check_helpers(guard_root: Path, runner: Runner) -> list[dict[str, object]]:
    helpers: list[dict[str, object]] = []
    for name in HELPER_NAMES:
        path = guard_root / name
        status = "ok" if path.is_file() and os.access(path, os.X_OK) else "missing"
        api_revision: int | None = None
        if status == "ok" and name == "omarchy-cast-guard":
            result = runner((str(path), "--version"))
            try:
                payload = json.loads(result.stdout) if result.returncode == 0 else None
            except json.JSONDecodeError:
                payload = None
            if not isinstance(payload, dict) or payload.get("schemaVersion") != 1 or payload.get("kind") != "omarchy-cast-guard-version" or payload.get("apiRevision") != GUARD_API_REVISION:
                status = "incompatible"
            elif isinstance(payload.get("apiRevision"), int):
                api_revision = payload["apiRevision"]
        helpers.append({"name": name, "status": status, "path": str(path), "apiRevision": api_revision})
    return helpers


def _result_error(result: CommandResult) -> str:
    message = result.stderr.strip() or result.stdout.strip() or f"exit status {result.returncode}"
    return bounded_text(message.splitlines()[0], limit=240, fallback="command failed")


def _engine_capabilities(runner: Runner) -> dict[str, object]:
    result = runner(("fluxcast", "--help"))
    required = ("--wfd-p2p-backend", "--wfd-supplicant-mode", "--wfd-video-encoder", "--wfd-supplicant-network-trigger", "--wfd-progress-log")
    available = result.returncode == 0 and all(flag in result.stdout for flag in required)
    return {"schemaVersion": 1, "installed": result.returncode == 0, "compatible": available, "requiredFlags": list(required)}


def _readiness(
    *,
    checks: list[dict[str, object]],
    engine: dict[str, object],
    helpers: list[dict[str, object]],
    wifi_links: list[WifiLink],
    monitors: list[Monitor],
    default_sink: str,
) -> dict[str, object]:
    issues: list[dict[str, str]] = []
    missing_commands = [str(check["name"]) for check in checks if check["status"] != "ok"]
    for name in missing_commands:
        issues.append({
            "code": "command-missing",
            "name": name,
            "scope": "setup" if name in COMPANION_COMMANDS else "host",
            "message": f"{name} is unavailable",
        })
    if engine.get("installed") is True and engine.get("compatible") is not True:
        issues.append({
            "code": "engine-incompatible",
            "name": "FluxCast engine",
            "scope": "setup",
            "message": "FluxCast does not provide the Omacast WFD capabilities",
        })
    for helper in helpers:
        if helper["status"] == "missing":
            issues.append({
                "code": "helper-missing",
                "name": str(helper["name"]),
                "scope": "setup",
                "message": f"{helper['name']} is not installed",
            })
        elif helper["status"] != "ok":
            issues.append({
                "code": "helper-incompatible",
                "name": str(helper["name"]),
                "scope": "setup",
                "message": f"{helper['name']} must be updated",
            })
    if not any(link.connected for link in wifi_links):
        issues.append({
            "code": "wifi-disconnected",
            "name": "Wi-Fi",
            "scope": "host",
            "message": "Connect to Wi-Fi before casting",
        })
    if not monitors:
        issues.append({
            "code": "monitor-unavailable",
            "name": "Desktop source",
            "scope": "host",
            "message": "No Hyprland display is available to mirror",
        })
    if not default_sink:
        issues.append({
            "code": "audio-unavailable",
            "name": "Desktop audio",
            "scope": "host",
            "message": "No PipeWire audio output is available",
        })
    setup_required = any(issue["scope"] == "setup" for issue in issues)
    summary = (
        "Install or update the Omacast companion package"
        if setup_required
        else (issues[0]["message"] if issues else "Casting support ready")
    )
    return {
        "schemaVersion": 1,
        "ready": not issues,
        "setupRequired": setup_required,
        "summary": summary,
        "issues": issues,
    }


def discover_host(
    *,
    runner: Runner = run_command,
    render_root: Path = Path("/dev/dri"),
    guard_root: Path = Path("/usr/lib/omarchy-cast"),
    command_finder: Callable[[str], str | None] = shutil.which,
) -> dict[str, object]:
    """Return a JSON-serializable, non-invasive host snapshot.

    This deliberately does not scan P2P peers, create a network connection,
    change a Hyprland output, or open a firewall port.
    """
    checks = [_check_command(name, command_finder) for name in REQUIRED_COMMANDS]
    helpers = _check_helpers(guard_root, runner)
    engine = _engine_capabilities(runner)

    nm_result = runner(("nmcli", "-t", "-f", "DEVICE,TYPE,STATE", "device"))
    wifi_devices = parse_nmcli_devices(nm_result.stdout) if nm_result.returncode == 0 else []

    wifi_links: list[WifiLink] = []
    link_diagnostics: list[dict[str, str]] = []
    for device in wifi_devices:
        if device.type != "wifi":
            continue
        link_result = runner(("iw", "dev", device.name, "link"))
        if link_result.returncode == 0:
            wifi_links.append(parse_iw_link(device.name, link_result.stdout))
        else:
            diagnostics_message = _result_error(link_result)
            wifi_links.append(WifiLink(interface=device.name, connected=False, ssid=None, frequency_mhz=None))
            link_diagnostics.append({"source": f"Wi-Fi {device.name}", "message": diagnostics_message})

    monitor_result = runner(("hyprctl", "monitors", "-j"))
    monitors = parse_hyprland_monitors(monitor_result.stdout) if monitor_result.returncode == 0 else []

    sink_result = runner(("pactl", "get-default-sink"))
    default_sink = bounded_text(sink_result.stdout.strip(), limit=240) if sink_result.returncode == 0 else ""

    render_nodes = sorted(str(path) for path in render_root.glob("renderD*") if path.is_char_device())
    diagnostics: list[dict[str, str]] = list(link_diagnostics)
    for source, result in (("NetworkManager", nm_result), ("Hyprland", monitor_result), ("PipeWire", sink_result)):
        if result.returncode != 0:
            if len(diagnostics) < MAX_DIAGNOSTICS:
                diagnostics.append({"source": source, "message": _result_error(result)})

    readiness = _readiness(
        checks=checks,
        engine=engine,
        helpers=helpers,
        wifi_links=wifi_links,
        monitors=monitors,
        default_sink=default_sink,
    )
    return {
        "schemaVersion": 1,
        "readOnly": True,
        "checks": checks,
        "helpers": helpers,
        "engine": engine,
        "wifiDevices": [asdict(device) for device in wifi_devices],
        "wifiLinks": [asdict(link) for link in wifi_links],
        "monitors": [asdict(monitor) for monitor in monitors],
        "defaultSink": default_sink or None,
        "renderNodes": render_nodes,
        "readiness": readiness,
        "diagnostics": diagnostics,
    }
