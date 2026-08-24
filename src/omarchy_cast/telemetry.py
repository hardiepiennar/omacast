"""Time-aligned, bounded telemetry for a live Miracast session."""

from __future__ import annotations

from collections import deque
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import threading
import time
from typing import Any, Mapping

from .state import runtime_directory


_SESSION_ID = re.compile(r"^[a-f0-9]{32}$")
_NUMBER = re.compile(r"-?[0-9]+(?:\.[0-9]+)?")
_LIVE_FILENAMES = frozenset({"current.json", "ffmpeg.progress", "mux-packets.csv", "engine.jsonl", "engine.log", "qos.pid"})


def telemetry_paths(session_id: str, environ: Mapping[str, str] | None = None) -> dict[str, Path]:
    if not _SESSION_ID.fullmatch(session_id):
        raise ValueError("telemetry requires a controller-issued session id")
    environ = os.environ if environ is None else environ
    live = runtime_directory(environ) / "telemetry" / session_id
    state_home = Path(environ.get("XDG_STATE_HOME", str(Path.home() / ".local" / "state")))
    archive = state_home / "omarchy-cast" / "telemetry"
    live.mkdir(mode=0o700, parents=True, exist_ok=True)
    archive.mkdir(mode=0o700, parents=True, exist_ok=True)
    return {
        "current": live / "current.json",
        "progress": live / "ffmpeg.progress",
        "packets": live / "mux-packets.csv",
        "latency": live / "engine.jsonl",
        "engineLog": live / "engine.log",
        "qos": live / "qos.pid",
        "samples": archive / f"{session_id}.jsonl",
    }


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    descriptor, temporary_name = tempfile.mkstemp(prefix=".telemetry-", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, sort_keys=True, separators=(",", ":"))
            handle.write("\n")
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def read_telemetry(session_id: str, environ: Mapping[str, str] | None = None) -> dict[str, object] | None:
    try:
        path = telemetry_paths(session_id, environ)["current"]
        if not path.is_file() or path.stat().st_size > 262_144:
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or payload.get("schemaVersion") != 1 or payload.get("sessionId") != session_id:
        return None
    return payload


def cleanup_live_telemetry(session_id: str, environ: Mapping[str, str] | None = None) -> bool:
    """Remove only controller-owned volatile files for one finished session."""
    if not _SESSION_ID.fullmatch(session_id):
        raise ValueError("telemetry cleanup requires a controller-issued session id")
    live = runtime_directory(environ) / "telemetry" / session_id
    if not live.exists():
        return True
    if live.is_symlink() or not live.is_dir():
        return False
    try:
        for name in _LIVE_FILENAMES:
            (live / name).unlink(missing_ok=True)
        live.rmdir()
    except OSError:
        return False
    return True


def parse_ffmpeg_progress(text: str) -> dict[str, str]:
    """Return only the last complete ffmpeg progress record."""
    current: dict[str, str] = {}
    last: dict[str, str] = {}
    for line in text.splitlines():
        key, separator, value = line.partition("=")
        if not separator:
            continue
        current[key.strip()] = value.strip()
        if key.strip() == "progress":
            last = current
            current = {}
    return last


def parse_iw_station(text: str) -> dict[str, int | float]:
    fields: dict[str, int | float] = {}
    names = {
        "tx retries": "txRetries",
        "tx failed": "txFailed",
        "beacon loss": "beaconLoss",
        "signal": "signalDbm",
        "tx bitrate": "txBitrateMbps",
        "rx bitrate": "rxBitrateMbps",
    }
    for line in text.splitlines():
        key, separator, value = line.strip().partition(":")
        target = names.get(key)
        match = _NUMBER.search(value) if separator and target else None
        if match:
            number = float(match.group())
            fields[target] = int(number) if number.is_integer() else number
    return fields


def _bounded_read(path: Path, limit: int = 262_144) -> str:
    try:
        size = path.stat().st_size
        with path.open("rb") as handle:
            if size > limit:
                header = handle.read(4096)
                handle.seek(size - (limit - len(header)))
                return (header + b"\n" + handle.read(limit - len(header))).decode("utf-8", errors="replace")
            return handle.read(limit).decode("utf-8", errors="replace")
    except OSError:
        return ""


def _number(value: str | None, default: float = 0.0) -> float:
    match = _NUMBER.search(value or "")
    return float(match.group()) if match else default


class TelemetrySampler:
    """Sample the actual producer, muxer, socket and radio once per second."""

    def __init__(
        self,
        *,
        session_id: str,
        engine_pid: int,
        wifi_interface: str,
        source_port: int = 19002,
        environ: Mapping[str, str] | None = None,
    ) -> None:
        self.session_id = session_id
        self.engine_pid = engine_pid
        self.wifi_interface = wifi_interface
        self.source_port = source_port
        self.environ = dict(os.environ if environ is None else environ)
        self.paths = telemetry_paths(session_id, self.environ)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._previous_process: dict[int, tuple[float, float, float, int, int, int, int]] = {}
        self._previous_network: tuple[float, int, int] | None = None
        self._window: deque[tuple[float, int, int]] = deque()
        self._radio: dict[str, int | float] = {}
        self._radio_sampled_at = 0.0
        self._baseline_radio: dict[str, int | float] | None = None
        self._maxima = {
            "sendQueueBytes": 0.0,
            "captureCpuPercent": 0.0,
            "muxCpuPercent": 0.0,
            "cpuDelayMsPerSec": 0.0,
        }
        self._sample_count = 0

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name="omarchy-cast-telemetry", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)

    def _descendants(self) -> list[int]:
        found: list[int] = []
        pending = [self.engine_pid]
        while pending:
            pid = pending.pop()
            if pid in found:
                continue
            found.append(pid)
            children: list[str] = []
            try:
                tasks = Path(f"/proc/{pid}/task").iterdir()
                for task in tasks:
                    try:
                        children.extend((task / "children").read_text().split())
                    except OSError:
                        pass
            except OSError:
                pass
            pending.extend(int(child) for child in children if child.isdigit())
        return found

    def _process(self, pid: int, now: float) -> dict[str, int | float | str] | None:
        try:
            stat = Path(f"/proc/{pid}/stat").read_text()
            closing = stat.rfind(")")
            name = stat[stat.find("(") + 1:closing]
            fields = stat[closing + 2:].split()
            ticks = float(fields[11]) + float(fields[12])
            rss_kib = int(fields[21]) * os.sysconf("SC_PAGE_SIZE") // 1024
            sched = Path(f"/proc/{pid}/schedstat").read_text().split()
            delay_ns = float(sched[1]) if len(sched) >= 2 else 0.0
            command = Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0", 1)[0].decode(errors="replace")
            io_fields = {}
            for line in Path(f"/proc/{pid}/io").read_text().splitlines():
                key, separator, value = line.partition(":")
                if separator:
                    io_fields[key] = int(value.strip())
            rchar, wchar = io_fields.get("rchar", 0), io_fields.get("wchar", 0)
            syscr, syscw = io_fields.get("syscr", 0), io_fields.get("syscw", 0)
        except (OSError, ValueError, IndexError):
            return None
        previous = self._previous_process.get(pid)
        cpu = delay = read_mbps = write_mbps = reads_per_second = writes_per_second = 0.0
        if previous:
            elapsed = max(0.001, now - previous[0])
            cpu = max(0.0, (ticks - previous[1]) / os.sysconf("SC_CLK_TCK") / elapsed * 100.0)
            delay = max(0.0, (delay_ns - previous[2]) / 1_000_000.0 / elapsed)
            read_mbps = max(0, rchar - previous[3]) * 8 / elapsed / 1_000_000
            write_mbps = max(0, wchar - previous[4]) * 8 / elapsed / 1_000_000
            reads_per_second = max(0, syscr - previous[5]) / elapsed
            writes_per_second = max(0, syscw - previous[6]) / elapsed
        self._previous_process[pid] = (now, ticks, delay_ns, rchar, wchar, syscr, syscw)
        executable = Path(command).name if command else name
        return {
            "pid": pid, "name": executable, "cpuPercent": round(cpu, 1),
            "rssMiB": round(rss_kib / 1024, 1), "cpuDelayMsPerSec": round(delay, 1),
            "readMbps": round(read_mbps, 2), "writeMbps": round(write_mbps, 2),
            "readsPerSecond": round(reads_per_second, 1), "writesPerSecond": round(writes_per_second, 1),
        }

    def _processes(self, now: float) -> dict[str, object]:
        result: dict[str, object] = {}
        for pid in self._descendants():
            sample = self._process(pid, now)
            if not sample:
                continue
            name = str(sample["name"])
            role = "capture" if name == "gpu-screen-recorder" else "mux" if name == "ffmpeg" else "engine" if pid == self.engine_pid else name
            result[role] = sample
        return result

    def _p2p_interface(self) -> str | None:
        candidates = sorted(Path("/sys/class/net").glob(f"p2p-{self.wifi_interface}-*"))
        active = [path for path in candidates if _bounded_read(path / "operstate", 32).strip() == "up"]
        selected = (active or candidates)
        return selected[-1].name if selected else None

    @staticmethod
    def _counter(interface: str, name: str) -> int:
        try:
            return int(Path(f"/sys/class/net/{interface}/statistics/{name}").read_text())
        except (OSError, ValueError):
            return 0

    def _send_queue(self) -> int:
        suffix = f":{self.source_port:04X}"
        try:
            lines = Path("/proc/net/udp").read_text().splitlines()[1:]
        except OSError:
            return 0
        for line in lines:
            fields = line.split()
            if len(fields) >= 5 and fields[1].upper().endswith(suffix):
                try:
                    return int(fields[4].split(":", 1)[0], 16)
                except ValueError:
                    return 0
        return 0

    def _sample_radio(self, interface: str, now: float) -> dict[str, int | float]:
        if now - self._radio_sampled_at >= 1.0:
            self._radio_sampled_at = now
            try:
                sample = subprocess.run(
                    ("iw", "dev", interface, "station", "dump"), capture_output=True,
                    text=True, timeout=0.8, check=False, env=self.environ,
                )
                if sample.returncode == 0:
                    self._radio = parse_iw_station(sample.stdout)
                    if self._baseline_radio is None and self._radio:
                        self._baseline_radio = dict(self._radio)
            except (OSError, subprocess.TimeoutExpired):
                pass
        baseline = self._baseline_radio or {}
        result = dict(self._radio)
        for source, target in (("txRetries", "retryDelta"), ("txFailed", "failureDelta"), ("beaconLoss", "beaconLossDelta")):
            result[target] = int(result.get(source, 0)) - int(baseline.get(source, 0))
        return result

    def _packet_timing(self) -> dict[str, object]:
        streams: dict[int, list[float]] = {}
        sizes: dict[int, int] = {}
        timebases: dict[int, float] = {}
        for line in _bounded_read(self.paths["packets"]).splitlines():
            timebase = re.fullmatch(r"#tb\s+(\d+):\s+(\d+)/(\d+)", line.strip())
            if timebase and int(timebase[3]):
                timebases[int(timebase[1])] = int(timebase[2]) / int(timebase[3])
                continue
            if line.startswith("#"):
                continue
            fields = line.split(",")
            if len(fields) < 5:
                continue
            try:
                stream = int(fields[0].strip())
                timestamp = int(fields[2].strip()) * timebases.get(stream, 1.0)
                size = int(fields[4].strip())
            except ValueError:
                continue
            streams.setdefault(stream, []).append(timestamp)
            sizes[stream] = sizes.get(stream, 0) + size

        def cadence(values: list[float]) -> dict[str, object]:
            recent = values[-180:]
            gaps = [right - left for left, right in zip(recent, recent[1:]) if right >= left]
            if not gaps:
                return {"packets": len(recent), "maxGapMs": 0.0, "p95GapMs": 0.0}
            ordered = sorted(gaps)
            p95 = ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))]
            return {"packets": len(recent), "maxGapMs": round(max(gaps) * 1000, 3), "p95GapMs": round(p95 * 1000, 3)}

        video = cadence(streams.get(0, []))
        audio = cadence(streams.get(1, []))
        video["bytesInWindow"] = sizes.get(0, 0)
        audio["bytesInWindow"] = sizes.get(1, 0)
        av_skew = 0.0
        if streams.get(0) and streams.get(1):
            av_skew = (streams[0][-1] - streams[1][-1]) * 1000
        return {"video": video, "audio": audio, "avSkewMs": round(av_skew, 3)}

    def _network(self, now: float) -> dict[str, int | float | str | None]:
        interface = self._p2p_interface()
        if not interface:
            return {"interface": None, "txMbps": 0.0, "packetRate": 0.0, "sendQueueBytes": 0}
        tx_bytes = self._counter(interface, "tx_bytes")
        tx_packets = self._counter(interface, "tx_packets")
        tx_mbps = packet_rate = 0.0
        if self._previous_network:
            elapsed = max(0.001, now - self._previous_network[0])
            tx_mbps = max(0.0, tx_bytes - self._previous_network[1]) * 8 / elapsed / 1_000_000
            packet_rate = max(0.0, tx_packets - self._previous_network[2]) / elapsed
        self._previous_network = (now, tx_bytes, tx_packets)
        return {
            "interface": interface,
            "txMbps": round(tx_mbps, 2),
            "packetRate": round(packet_rate, 1),
            "sendQueueBytes": self._send_queue(),
            "txErrors": self._counter(interface, "tx_errors"),
            "txDropped": self._counter(interface, "tx_dropped"),
        }

    def _negotiated(self) -> dict[str, object]:
        result: dict[str, object] = {}
        for line in _bounded_read(self.paths["latency"]).splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(event, dict) and event.get("event") == "media_starting":
                mode = str(event.get("mode", ""))
                match = re.fullmatch(r"(\d+)x(\d+)p(\d+)", mode)
                result = {"mode": mode, "tvIp": event.get("tv_ip")}
                if match:
                    result.update({"width": int(match[1]), "height": int(match[2]), "fps": int(match[3])})
        return result

    def _output(self, now: float) -> dict[str, int | float | str]:
        progress = parse_ffmpeg_progress(_bounded_read(self.paths["progress"]))
        frame = int(_number(progress.get("frame")))
        out_us = int(_number(progress.get("out_time_us")))
        self._window.append((now, frame, out_us))
        while len(self._window) > 2 and now - self._window[0][0] > 3.0:
            self._window.popleft()
        measured_fps = realtime = 0.0
        if len(self._window) >= 2:
            elapsed = max(0.001, now - self._window[0][0])
            measured_fps = max(0, frame - self._window[0][1]) / elapsed
            realtime = max(0, out_us - self._window[0][2]) / 1_000_000 / elapsed
        return {
            "frame": frame,
            "measuredFps": round(measured_fps, 2),
            "realtimeRatio": round(realtime, 3),
            "reportedFps": round(_number(progress.get("fps")), 2),
            "bitrateKbps": round(_number(progress.get("bitrate")), 1),
            "totalBytes": int(_number(progress.get("total_size"))),
            "outTimeMs": out_us // 1000,
            "dupFrames": int(_number(progress.get("dup_frames"))),
            "dropFrames": int(_number(progress.get("drop_frames"))),
            "speed": progress.get("speed", "0x"),
        }

    def _health(self, negotiated: Mapping[str, object], output: Mapping[str, object], processes: Mapping[str, object], transport: Mapping[str, object], radio: Mapping[str, object], packet_timing: Mapping[str, object]) -> dict[str, object]:
        issues: list[str] = []
        if not negotiated.get("mode") or not output.get("frame"):
            return {"status": "warming", "issues": []}
        expected_fps = float(negotiated.get("fps", 0) or 0)
        measured_fps = float(output.get("measuredFps", 0) or 0)
        realtime = float(output.get("realtimeRatio", 0) or 0)
        if self._sample_count > 8 and realtime and realtime < 0.985:
            issues.append("pipeline is falling behind realtime")
        if expected_fps and self._sample_count > 8 and measured_fps and measured_fps < expected_fps * 0.97:
            issues.append("delivered frame cadence is below the negotiated rate")
        if int(output.get("dropFrames", 0) or 0) > 0:
            issues.append("muxer reports dropped frames")
        if int(transport.get("sendQueueBytes", 0) or 0) > 65_536:
            issues.append("RTP socket queue is accumulating")
        if int(radio.get("failureDelta", 0) or 0) > 0 or int(radio.get("beaconLossDelta", 0) or 0) > 0:
            issues.append("radio reports transmission loss")
        video_timing = packet_timing.get("video")
        audio_timing = packet_timing.get("audio")
        if isinstance(video_timing, Mapping) and float(video_timing.get("maxGapMs", 0) or 0) > 50:
            issues.append("video timestamps contain a discontinuity")
        if isinstance(audio_timing, Mapping) and float(audio_timing.get("maxGapMs", 0) or 0) > 30:
            issues.append("audio timestamps contain a discontinuity")
        if abs(float(packet_timing.get("avSkewMs", 0) or 0)) > 75:
            issues.append("audio and video timestamps are drifting apart")
        for role in ("capture", "mux"):
            process = processes.get(role)
            if isinstance(process, Mapping) and float(process.get("cpuDelayMsPerSec", 0) or 0) > 120:
                issues.append(role + " is waiting excessively for CPU")
        return {"status": "attention" if issues else "healthy" if self._sample_count > 8 else "warming", "issues": issues}

    def sample(self) -> dict[str, object]:
        now = time.monotonic()
        self._sample_count += 1
        negotiated = self._negotiated()
        output = self._output(now)
        processes = self._processes(now)
        transport = self._network(now)
        interface = transport.get("interface")
        radio = self._sample_radio(str(interface), now) if interface else {}
        packet_timing = self._packet_timing()
        for key, role, field in (
            ("captureCpuPercent", "capture", "cpuPercent"),
            ("muxCpuPercent", "mux", "cpuPercent"),
            ("cpuDelayMsPerSec", "capture", "cpuDelayMsPerSec"),
        ):
            process = processes.get(role)
            if isinstance(process, Mapping):
                self._maxima[key] = max(self._maxima[key], float(process.get(field, 0) or 0))
        self._maxima["sendQueueBytes"] = max(self._maxima["sendQueueBytes"], float(transport.get("sendQueueBytes", 0) or 0))
        payload: dict[str, object] = {
            "schemaVersion": 1,
            "sessionId": self.session_id,
            "sampledAt": datetime.now(UTC).isoformat(timespec="milliseconds"),
            "sampleNumber": self._sample_count,
            "negotiated": negotiated,
            "output": output,
            "processes": processes,
            "transport": transport,
            "radio": radio,
            "packetTiming": packet_timing,
            "maxima": {key: round(value, 1) for key, value in self._maxima.items()},
        }
        payload["health"] = self._health(negotiated, output, processes, transport, radio, packet_timing)
        return payload

    def _record(self, payload: Mapping[str, object]) -> None:
        _atomic_json(self.paths["current"], payload)
        with self.paths["samples"].open("a", encoding="utf-8") as handle:
            os.chmod(self.paths["samples"], 0o600)
            handle.write(json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n")

    def _run(self) -> None:
        deadline = time.monotonic()
        while not self._stop.is_set():
            try:
                self._record(self.sample())
            except (OSError, ValueError):
                pass
            # Diagnostics must never compete with capture. One-second samples
            # retain meaningful rates and health trends without repeatedly
            # parsing growing progress/packet logs on a latency-sensitive CPU.
            deadline += 1.0
            self._stop.wait(max(0.01, deadline - time.monotonic()))
