"""Time-aligned, bounded telemetry for a live Miracast session."""

from __future__ import annotations

from collections import deque
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import re
import stat
import threading
import time
from typing import Any, Mapping
from uuid import uuid4

from .bounds import BoundError, MAX_TELEMETRY_BYTES, read_bounded_regular_file, validate_json_budget
from .command import run_command
from .state import _open_session_runtime_descriptor, runtime_directory


_SESSION_ID = re.compile(r"^[a-f0-9]{32}$")
_NUMBER = re.compile(r"-?[0-9]+(?:\.[0-9]+)?")
_LIVE_FILENAMES = frozenset({"current.json", "ffmpeg.progress", "mux-packets.csv", "engine.jsonl", "engine.log"})
_PATH_KEYS = {
    "current": "current.json",
    "progress": "ffmpeg.progress",
    "packets": "mux-packets.csv",
    "latency": "engine.jsonl",
    "engineLog": "engine.log",
}


def _open_private_child(directory_descriptor: int, name: str, *, create: bool) -> int:
    flags = os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_DIRECTORY", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    if create:
        try:
            os.mkdir(name, mode=0o700, dir_fd=directory_descriptor)
        except FileExistsError:
            pass
    descriptor = os.open(name, flags, dir_fd=directory_descriptor)
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != os.getuid():
            raise ValueError("telemetry directory ownership or permissions are unsafe")
        if stat.S_IMODE(metadata.st_mode) != 0o700:
            os.fchmod(descriptor, 0o700)
            metadata = os.fstat(descriptor)
        if stat.S_IMODE(metadata.st_mode) != 0o700:
            raise ValueError("telemetry directory ownership or permissions are unsafe")
        return descriptor
    except Exception:
        os.close(descriptor)
        raise


def _open_live_parent(environ: Mapping[str, str] | None, *, create: bool) -> int:
    runtime_descriptor = _open_session_runtime_descriptor(environ, create=create)
    try:
        return _open_private_child(runtime_descriptor, "telemetry", create=create)
    finally:
        os.close(runtime_descriptor)


def _state_home(environ: Mapping[str, str]) -> Path:
    return Path(environ.get("XDG_STATE_HOME", str(Path.home() / ".local" / "state")))


def _open_archive_directory(environ: Mapping[str, str], *, create: bool) -> int:
    state_home = _state_home(environ)
    if create:
        state_home.mkdir(mode=0o700, parents=True, exist_ok=True)
    flags = os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_DIRECTORY", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    state_descriptor = os.open(state_home, flags)
    try:
        metadata = os.fstat(state_descriptor)
        if not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != os.getuid() or metadata.st_mode & 0o022:
            raise ValueError("telemetry state directory ownership or permissions are unsafe")
        product_descriptor = _open_private_child(state_descriptor, "omarchy-cast", create=create)
    finally:
        os.close(state_descriptor)
    try:
        return _open_private_child(product_descriptor, "telemetry", create=create)
    finally:
        os.close(product_descriptor)


def _write_all(descriptor: int, encoded: bytes) -> None:
    view = memoryview(encoded)
    while view:
        written = os.write(descriptor, view)
        view = view[written:]


def _read_descriptor_text(descriptor: int, limit: int) -> str:
    metadata = os.fstat(descriptor)
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or metadata.st_mode & 0o077
        or metadata.st_nlink not in (0, 1)
    ):
        return ""
    size = metadata.st_size
    try:
        if size > limit:
            header = os.pread(descriptor, min(4096, limit), 0)
            tail_size = max(0, limit - len(header))
            tail = os.pread(descriptor, tail_size, max(0, size - tail_size))
            payload = header + (b"\n" if tail_size else b"") + tail
        else:
            payload = os.pread(descriptor, limit, 0)
    except OSError:
        return ""
    return payload[:limit].decode("utf-8", errors="replace")


class TelemetryWorkspace:
    """Pinned private directories and files for one telemetry producer."""

    def __init__(self, session_id: str, environ: Mapping[str, str] | None = None, *, archive: bool = True) -> None:
        if not _SESSION_ID.fullmatch(session_id):
            raise ValueError("telemetry requires a controller-issued session id")
        self.session_id = session_id
        self.environ = dict(os.environ if environ is None else environ)
        parent_descriptor = _open_live_parent(self.environ, create=True)
        try:
            self.live_descriptor = _open_private_child(parent_descriptor, session_id, create=True)
        finally:
            os.close(parent_descriptor)
        try:
            self.archive_descriptor = _open_archive_directory(self.environ, create=True) if archive else None
        except Exception:
            os.close(self.live_descriptor)
            self.live_descriptor = -1
            raise
        self._outputs: dict[str, int] = {}
        self.paths = _telemetry_path_values(session_id, self.environ)

    def close(self) -> None:
        for descriptor in self._outputs.values():
            os.close(descriptor)
        self._outputs.clear()
        if self.archive_descriptor is not None:
            os.close(self.archive_descriptor)
            self.archive_descriptor = None
        if self.live_descriptor >= 0:
            os.close(self.live_descriptor)
            self.live_descriptor = -1

    def prepare_engine_outputs(self) -> dict[str, Path]:
        try:
            for key in ("progress", "latency", "packets", "engineLog"):
                name = _PATH_KEYS[key]
                try:
                    os.unlink(name, dir_fd=self.live_descriptor)
                except FileNotFoundError:
                    pass
                flags = os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC
                flags |= getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
                descriptor = os.open(name, flags, 0o600, dir_fd=self.live_descriptor)
                metadata = os.fstat(descriptor)
                if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid() or metadata.st_nlink != 1:
                    os.close(descriptor)
                    raise ValueError("telemetry output is unsafe")
                os.fchmod(descriptor, 0o600)
                self._outputs[key] = descriptor
        except Exception:
            for descriptor in self._outputs.values():
                os.close(descriptor)
            self._outputs.clear()
            raise
        process_fd_root = Path(f"/proc/{os.getpid()}/fd")
        return {key: process_fd_root / str(descriptor) for key, descriptor in self._outputs.items()}

    def engine_log_handle(self):
        return os.fdopen(os.dup(self._outputs["engineLog"]), "w", encoding="utf-8")

    def read_text(self, key: str, limit: int = MAX_TELEMETRY_BYTES) -> str:
        descriptor = self._outputs.get(key)
        if descriptor is not None:
            return _read_descriptor_text(descriptor, limit)
        return _bounded_read(
            Path(_PATH_KEYS[key]),
            limit,
            directory_fd=self.live_descriptor,
            require_owner=True,
            require_private=True,
            require_single_link=True,
        )

    def write_current(self, payload: Mapping[str, Any]) -> None:
        _atomic_json(self.live_descriptor, _PATH_KEYS["current"], payload)

    def append_sample(self, payload: Mapping[str, Any]) -> None:
        if self.archive_descriptor is None:
            return
        encoded = (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
        flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_CLOEXEC
        flags |= getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
        descriptor = os.open(f"{self.session_id}.jsonl", flags, 0o600, dir_fd=self.archive_descriptor)
        try:
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid() or metadata.st_nlink != 1:
                raise ValueError("telemetry archive is unsafe")
            os.fchmod(descriptor, 0o600)
            _write_all(descriptor, encoded)
        finally:
            os.close(descriptor)


def _telemetry_path_values(session_id: str, environ: Mapping[str, str]) -> dict[str, Path]:
    live = runtime_directory(environ) / "telemetry" / session_id
    archive = _state_home(environ) / "omarchy-cast" / "telemetry"
    return {**{key: live / name for key, name in _PATH_KEYS.items()}, "samples": archive / f"{session_id}.jsonl"}


def telemetry_paths(session_id: str, environ: Mapping[str, str] | None = None) -> dict[str, Path]:
    if not _SESSION_ID.fullmatch(session_id):
        raise ValueError("telemetry requires a controller-issued session id")
    environ = os.environ if environ is None else environ
    workspace = TelemetryWorkspace(session_id, environ)
    try:
        return dict(workspace.paths)
    finally:
        workspace.close()


def _atomic_json(directory_descriptor: int, name: str, payload: Mapping[str, Any]) -> None:
    temporary_name = f".telemetry-{uuid4().hex}.tmp"
    descriptor = -1
    try:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC
        flags |= getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
        descriptor = os.open(temporary_name, flags, 0o600, dir_fd=directory_descriptor)
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid() or metadata.st_nlink != 1:
            raise ValueError("telemetry temporary file is unsafe")
        os.fchmod(descriptor, 0o600)
        encoded = (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
        if len(encoded) > MAX_TELEMETRY_BYTES:
            raise ValueError("telemetry snapshot exceeds the safe size limit")
        _write_all(descriptor, encoded)
        os.fsync(descriptor)
        os.replace(temporary_name, name, src_dir_fd=directory_descriptor, dst_dir_fd=directory_descriptor)
        os.fsync(directory_descriptor)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            os.unlink(temporary_name, dir_fd=directory_descriptor)
        except OSError:
            pass


def read_telemetry(session_id: str, environ: Mapping[str, str] | None = None) -> dict[str, object] | None:
    directory_descriptor = -1
    parent_descriptor = -1
    try:
        if not _SESSION_ID.fullmatch(session_id):
            raise ValueError("telemetry requires a controller-issued session id")
        parent_descriptor = _open_live_parent(environ, create=False)
        directory_descriptor = _open_private_child(parent_descriptor, session_id, create=False)
        encoded = read_bounded_regular_file(
            Path(_PATH_KEYS["current"]),
            limit=MAX_TELEMETRY_BYTES,
            require_owner=True,
            require_private=True,
            require_single_link=True,
            directory_fd=directory_descriptor,
        )
        payload = json.loads(encoded.decode("utf-8"))
        validate_json_budget(payload, max_nodes=4_096)
    except (OSError, ValueError, UnicodeDecodeError, json.JSONDecodeError, BoundError):
        return None
    finally:
        if directory_descriptor >= 0:
            os.close(directory_descriptor)
        if parent_descriptor >= 0:
            os.close(parent_descriptor)
    if not isinstance(payload, dict) or payload.get("schemaVersion") != 1 or payload.get("sessionId") != session_id:
        return None
    return payload


def cleanup_live_telemetry(session_id: str, environ: Mapping[str, str] | None = None) -> bool:
    """Remove only controller-owned volatile files for one finished session."""
    if not _SESSION_ID.fullmatch(session_id):
        raise ValueError("telemetry cleanup requires a controller-issued session id")
    parent_descriptor = -1
    directory_descriptor = -1
    try:
        parent_descriptor = _open_live_parent(environ, create=False)
        directory_descriptor = _open_private_child(parent_descriptor, session_id, create=False)
        for name in _LIVE_FILENAMES:
            try:
                os.unlink(name, dir_fd=directory_descriptor)
            except FileNotFoundError:
                pass
        pinned = os.fstat(directory_descriptor)
        current = os.stat(session_id, dir_fd=parent_descriptor, follow_symlinks=False)
        if (pinned.st_dev, pinned.st_ino) != (current.st_dev, current.st_ino):
            return False
        os.rmdir(session_id, dir_fd=parent_descriptor)
    except FileNotFoundError:
        return True
    except (OSError, ValueError):
        return False
    finally:
        if directory_descriptor >= 0:
            os.close(directory_descriptor)
        if parent_descriptor >= 0:
            os.close(parent_descriptor)
    return True


def remove_archived_telemetry(session_id: str, environ: Mapping[str, str] | None = None) -> bool:
    """Remove one allowlisted archive entry relative to its pinned directory."""
    if not _SESSION_ID.fullmatch(session_id):
        raise ValueError("telemetry cleanup requires a controller-issued session id")
    environment = dict(os.environ if environ is None else environ)
    descriptor = -1
    try:
        descriptor = _open_archive_directory(environment, create=False)
        os.unlink(f"{session_id}.jsonl", dir_fd=descriptor)
    except FileNotFoundError:
        return True
    except (OSError, ValueError):
        return False
    finally:
        if descriptor >= 0:
            os.close(descriptor)
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


def _bounded_read(
    path: Path,
    limit: int = 262_144,
    *,
    directory_fd: int | None = None,
    require_owner: bool = False,
    require_private: bool = False,
    require_single_link: bool = False,
) -> str:
    descriptor = -1
    try:
        flags = os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NONBLOCK", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags, dir_fd=directory_fd)
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            return ""
        if require_owner and metadata.st_uid != os.getuid():
            return ""
        if require_private and metadata.st_mode & 0o077:
            return ""
        if require_single_link and metadata.st_nlink != 1:
            return ""
        size = metadata.st_size
        if size > limit:
            header = os.pread(descriptor, min(4096, limit), 0)
            tail_size = max(0, limit - len(header))
            tail = os.pread(descriptor, tail_size, max(0, size - tail_size))
            encoded = header + (b"\n" if tail_size else b"") + tail
        else:
            encoded = os.pread(descriptor, limit, 0)
        return encoded[:limit].decode("utf-8", errors="replace")
    except (OSError, ValueError):
        return ""
    finally:
        if descriptor >= 0:
            os.close(descriptor)


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
        workspace: TelemetryWorkspace | None = None,
    ) -> None:
        self.session_id = session_id
        self.engine_pid = engine_pid
        self.wifi_interface = wifi_interface
        self.source_port = source_port
        self.environ = dict(os.environ if environ is None else environ)
        self._workspace = workspace or TelemetryWorkspace(session_id, self.environ)
        self._owns_workspace = workspace is None
        self.paths = dict(self._workspace.paths)
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
        if self._owns_workspace:
            self._workspace.close()

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
            sample = run_command(("iw", "dev", interface, "station", "dump"), timeout=0.8, env=self.environ)
            if sample.returncode == 0:
                self._radio = parse_iw_station(sample.stdout)
                if self._baseline_radio is None and self._radio:
                    self._baseline_radio = dict(self._radio)
        baseline = self._baseline_radio or {}
        result = dict(self._radio)
        for source, target in (("txRetries", "retryDelta"), ("txFailed", "failureDelta"), ("beaconLoss", "beaconLossDelta")):
            result[target] = int(result.get(source, 0)) - int(baseline.get(source, 0))
        return result

    def _packet_timing(self) -> dict[str, object]:
        streams: dict[int, list[float]] = {}
        sizes: dict[int, int] = {}
        timebases: dict[int, float] = {}
        for line in self._workspace.read_text("packets").splitlines():
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
        for line in self._workspace.read_text("latency").splitlines():
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
        progress = parse_ffmpeg_progress(self._workspace.read_text("progress"))
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
        self._workspace.write_current(payload)
        self._workspace.append_sample(payload)

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
