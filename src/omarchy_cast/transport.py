"""The guarded media-engine boundary used by supervised Omacast sessions."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from collections import deque
import errno
import json
import os
from pathlib import Path
import re
import selectors
import stat
import subprocess
import threading
import time
from typing import Any, Callable, Mapping, Protocol

from .guard import GuardError, GuardRequest, prepare_command, read_guard_status, validate_helper_result
from .identity import receiver_address
from .pairing import validate_pairing_pin
from .telemetry import MAX_PROCESS_DESCRIPTORS, MAX_PROC_NETWORK_BYTES, MAX_SYSFS_INTERFACE_ENTRIES, BoundedOutputCollector, TelemetrySampler, TelemetryWorkspace, _bounded_read, _bounded_stream_read, cleanup_live_telemetry


CONNECT_TIMEOUT_SECONDS = 75
CAPTURE_START_TIMEOUT_SECONDS = 30
SUPPLICANT_GROUP_TIMEOUT_SECONDS = 45
GUARD_LEASE_SECONDS = 60
LEASE_RENEW_SECONDS = 5
RECEIVER_DISCONNECT_GRACE_SECONDS = 3
MAX_GUARD_DIAGNOSTIC_BYTES = 65_536
_INTERFACE = re.compile(r"^[A-Za-z0-9_.-]{1,15}$")
_DISPLAY_NAME = re.compile(r"^[^\x00-\x1f\x7f]{1,128}$")
_AUDIO_SOURCE = re.compile(r"^[^\x00-\x1f\x7f]{1,240}$")
_FRAME_COUNT = re.compile(r"^[0-9]{1,20}$")


class TransportError(RuntimeError):
    def __init__(self, message: str, *, code: str = "transport-failed") -> None:
        super().__init__(message)
        self.code = code


class TransportDisabled(TransportError):
    pass


class _BoundedPipeDrain:
    """Continuously drain one long-lived helper pipe with fixed retention."""

    def __init__(self, stream: Any, *, limit: int = MAX_GUARD_DIAGNOSTIC_BYTES) -> None:
        self._stream = stream
        self._limit = limit
        self._buffer = bytearray()
        self._pending = bytearray()
        self._lines: deque[str] = deque(maxlen=8)
        self._lock = threading.Lock()
        self._condition = threading.Condition(self._lock)
        self._overflow = threading.Event()
        self._stop = threading.Event()
        self._eof = False
        self._thread = threading.Thread(target=self._run, name="omarchy-cast-guard-stderr", daemon=True)

    @property
    def overflowed(self) -> bool:
        return self._overflow.is_set()

    def start(self) -> None:
        self._thread.start()

    def _run(self) -> None:
        selector = selectors.DefaultSelector()
        try:
            descriptor = self._stream.fileno()
            os.set_blocking(descriptor, False)
            selector.register(descriptor, selectors.EVENT_READ)
            while True:
                events = selector.select(0.1)
                if not events:
                    if self._stop.is_set():
                        break
                    continue
                for _key, _mask in events:
                    try:
                        encoded = os.read(descriptor, 8_192)
                    except BlockingIOError:
                        continue
                    if not encoded:
                        return
                    with self._lock:
                        self._buffer.extend(encoded)
                        if len(self._buffer) > self._limit:
                            self._overflow.set()
                            del self._buffer[:len(self._buffer) - self._limit]
                        self._pending.extend(encoded)
                        if len(self._pending) > self._limit:
                            self._overflow.set()
                            del self._pending[:len(self._pending) - self._limit]
                        while b"\n" in self._pending:
                            raw_line, _separator, remainder = self._pending.partition(b"\n")
                            self._pending = bytearray(remainder)
                            if len(self._lines) == self._lines.maxlen:
                                self._overflow.set()
                            self._lines.append(raw_line.decode("utf-8", errors="replace"))
                        self._condition.notify_all()
        except (OSError, TypeError, ValueError):
            return
        finally:
            selector.close()
            with self._condition:
                self._eof = True
                self._condition.notify_all()
            try:
                self._stream.close()
            except (OSError, ValueError):
                pass

    def text(self) -> str:
        with self._lock:
            encoded = bytes(self._buffer)
        return encoded.decode("utf-8", errors="replace")

    def next_line(self, timeout: float) -> str | None:
        deadline = time.monotonic() + timeout
        with self._condition:
            while not self._lines and not self._eof:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                self._condition.wait(remaining)
            return self._lines.popleft() if self._lines else None

    def lines(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(self._lines)

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=1)
        if self._thread.is_alive():
            raise TransportError("The networking helper diagnostic drain did not stop.", code="guard-cleanup-incomplete")


StageCallback = Callable[[str], None]
CancelCheck = Callable[[], bool]


@dataclass(frozen=True)
class TransportResult:
    status: str
    detail: str
    cleanup_complete: bool
    code: str | None = None


class TransportAdapter(Protocol):
    def run(self, plan: Mapping[str, Any], *, timeout_seconds: float | None, cancelled: CancelCheck, stage: StageCallback) -> TransportResult: ...


class SessionLease:
    """Renew one user-owned heartbeat until the supervised session ends."""

    def __init__(self, path: Path, *, interval_seconds: float = LEASE_RENEW_SECONDS) -> None:
        self.path = path
        self.interval_seconds = interval_seconds
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._directory_descriptor: int | None = None
        self._descriptor: int | None = None
        self._lock = threading.Lock()

    @staticmethod
    def _unsafe(message: str) -> OSError:
        return OSError(errno.EPERM, message)

    def _open(self) -> None:
        if self._descriptor is not None:
            return
        if self.path.name != "heartbeat":
            raise self._unsafe("session heartbeat name is unsafe")
        directory_flags = os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_DIRECTORY", 0)
        directory_flags |= getattr(os, "O_NOFOLLOW", 0)
        try:
            directory_descriptor = os.open(self.path.parent, directory_flags)
        except OSError as exc:
            raise self._unsafe("session heartbeat directory is unavailable or unsafe") from exc
        descriptor = -1
        try:
            directory_metadata = os.fstat(directory_descriptor)
            if (
                not stat.S_ISDIR(directory_metadata.st_mode)
                or directory_metadata.st_uid != os.getuid()
                or stat.S_IMODE(directory_metadata.st_mode) != 0o700
            ):
                raise self._unsafe("session heartbeat directory ownership or permissions are unsafe")
            flags = os.O_RDWR | os.O_CLOEXEC | getattr(os, "O_NONBLOCK", 0)
            flags |= getattr(os, "O_NOFOLLOW", 0)
            created = False
            try:
                descriptor = os.open(
                    self.path.name,
                    flags | os.O_CREAT | os.O_EXCL,
                    0o600,
                    dir_fd=directory_descriptor,
                )
                created = True
            except FileExistsError:
                descriptor = os.open(self.path.name, flags, dir_fd=directory_descriptor)
            if created:
                os.fchmod(descriptor, 0o600)
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode):
                raise self._unsafe("session heartbeat is not a regular file")
            if metadata.st_uid != os.getuid() or stat.S_IMODE(metadata.st_mode) != 0o600:
                raise self._unsafe("session heartbeat ownership or permissions are unsafe")
            if metadata.st_nlink != 1:
                raise self._unsafe("session heartbeat has an unsafe link count")
            if metadata.st_size > 32:
                raise self._unsafe("session heartbeat exceeds the safe size limit")
        except Exception:
            if descriptor >= 0:
                os.close(descriptor)
            os.close(directory_descriptor)
            raise
        self._directory_descriptor = directory_descriptor
        self._descriptor = descriptor

    def renew(self) -> None:
        with self._lock:
            self._open()
            assert self._descriptor is not None
            metadata = os.fstat(self._descriptor)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_uid != os.getuid()
                or stat.S_IMODE(metadata.st_mode) != 0o600
                or metadata.st_nlink not in (0, 1)
            ):
                raise self._unsafe("session heartbeat descriptor became unsafe")
            encoded = f"{int(time.time())}\n".encode("ascii")
            os.ftruncate(self._descriptor, 0)
            view = memoryview(encoded)
            offset = 0
            while view:
                written = os.pwrite(self._descriptor, view, offset)
                if written <= 0:
                    raise OSError(errno.EIO, "session heartbeat renewal made no progress")
                view = view[written:]
                offset += written
            os.ftruncate(self._descriptor, len(encoded))

    def start(self) -> None:
        if self._thread is not None:
            raise self._unsafe("session heartbeat renewal already started")
        try:
            self.renew()
        except Exception:
            self._close()
            raise
        self._thread = threading.Thread(target=self._run, name="omarchy-cast-lease", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        while not self._stop.wait(self.interval_seconds):
            try:
                self.renew()
            except OSError:
                return

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
            self._thread = None
        self._close()

    def _close(self) -> None:
        with self._lock:
            if self._descriptor is not None:
                os.close(self._descriptor)
                self._descriptor = None
            if self._directory_descriptor is not None:
                os.close(self._directory_descriptor)
                self._directory_descriptor = None


def validate_transport_plan(plan: Mapping[str, Any], *, executable: bool = False) -> None:
    """Require the exact independently reconstructed supported FluxCast plan."""
    command = plan.get("command")
    execution = plan.get("execution")
    if set(plan) != {"schemaVersion", "readOnly", "kind", "execution", "profile", "selection", "command", "warnings"}:
        raise TransportError("transport plan has unexpected fields")
    if type(plan.get("schemaVersion")) is not int or plan.get("schemaVersion") != 1 or plan.get("kind") != "launch-plan" or plan.get("readOnly") is not True:
        raise TransportError("transport requires a versioned read-only launch plan")
    expected_execution = {"allowed": True, "reason": "guarded-session-supervisor"} if executable else {"allowed": False, "reason": "read-only launch preview"}
    if not isinstance(execution, Mapping) or set(execution) != set(expected_execution) or any(
        type(execution.get(key)) is not type(value) or execution.get(key) != value
        for key, value in expected_execution.items()
    ):
        raise TransportError("transport plan has an unexpected execution permission")
    profile = plan.get("profile")
    selection = plan.get("selection")
    expected_profile = {"label": "Safe", "width": 1280, "height": 720, "fps": 60, "bitrateMbps": 7}
    if not isinstance(profile, Mapping) or set(profile) != set(expected_profile) or any(
        type(profile.get(key)) is not type(value) or profile.get(key) != value
        for key, value in expected_profile.items()
    ):
        raise TransportError("transport plan has an unsupported profile")
    expected_selection = {"peer", "mode", "source", "networkBackend", "wifiInterface", "wifiFrequencyMhz", "p2pFrequencyMhz", "monitor", "audioSource", "videoEncoder"}
    if not isinstance(selection, Mapping) or set(selection) != expected_selection:
        raise TransportError("transport plan has an unexpected selection")
    try:
        peer = receiver_address(selection.get("peer"))
    except ValueError as exc:
        raise TransportError("transport plan receiver address is invalid") from exc
    interface = selection.get("wifiInterface")
    monitor = selection.get("monitor")
    audio_source = selection.get("audioSource")
    encoder = selection.get("videoEncoder")
    frequency = selection.get("wifiFrequencyMhz")
    p2p_frequency = selection.get("p2pFrequencyMhz")
    backend = selection.get("networkBackend")
    if selection.get("mode") != "mirror" or selection.get("source") != "display":
        raise TransportError("transport plan has an unsupported source selection")
    if backend not in {"direct", "networkmanager"}:
        raise TransportError("transport plan has an unsupported network backend")
    if not isinstance(interface, str) or not _INTERFACE.fullmatch(interface):
        raise TransportError("transport plan Wi-Fi interface is invalid")
    if not isinstance(monitor, str) or not _DISPLAY_NAME.fullmatch(monitor):
        raise TransportError("transport plan monitor is invalid")
    if not isinstance(audio_source, str) or not _AUDIO_SOURCE.fullmatch(audio_source):
        raise TransportError("transport plan audio source is invalid")
    if encoder not in {"vaapi", "libx264"}:
        raise TransportError("transport plan encoder is invalid")
    if frequency is not None and (type(frequency) is not int or not 2300 <= frequency <= 7125):
        raise TransportError("transport plan Wi-Fi frequency is invalid")
    expected_p2p_frequency = frequency if type(frequency) is int and 2400 <= frequency <= 2500 else 0
    if type(p2p_frequency) is not int or p2p_frequency != expected_p2p_frequency:
        raise TransportError("transport plan P2P frequency does not match its Wi-Fi selection")
    expected_command = [
        "fluxcast", "--protocol", "wfd", "--output-res", "1280x720",
        "--fps", "60", "--bitrate", "7M", "--wfd-video-encoder", encoder,
        "--wfd-p2p-backend", "supplicant", "--wfd-supplicant-mode", "connect",
        "--wfd-peer", peer, "--wfd-interface", interface, "--wfd-timeout", "15",
        "--wfd-supplicant-frequency", str(p2p_frequency), "--wfd-no-firewall",
        "--monitor", monitor, "--wfd-capture-backend", "gpu-screen-recorder",
        "--wfd-audio-device", audio_source,
    ]
    if command != expected_command:
        raise TransportError("transport plan command does not exactly match its reviewed selection")
    warnings = plan.get("warnings")
    if not isinstance(warnings, list) or len(warnings) > 8 or any(not isinstance(item, str) or len(item) > 240 for item in warnings):
        raise TransportError("transport plan warnings are invalid")


def validate_test_transport_plan(plan: Mapping[str, Any]) -> None:
    """Validate only the deliberately non-executable injected-adapter fixture."""
    command = plan.get("command")
    selection = plan.get("selection")
    execution = plan.get("execution")
    if type(plan.get("schemaVersion")) is not int or plan.get("schemaVersion") != 1 or plan.get("kind") != "launch-plan" or plan.get("readOnly") is not True:
        raise TransportError("test transport requires a versioned read-only plan")
    if not isinstance(execution, Mapping) or execution.get("allowed") is not False:
        raise TransportError("test transport plan must remain non-executable")
    if not isinstance(selection, Mapping) or selection.get("source") != "display":
        raise TransportError("test transport plan has an invalid source")
    if not isinstance(command, list) or not command or command[0] != "fluxcast" or len(command) > 64 or any(not isinstance(item, str) or len(item) > 240 for item in command):
        raise TransportError("test transport plan has an invalid argument fixture")


def executable_plan(plan: Mapping[str, Any]) -> dict[str, object]:
    """Turn a reviewed preview into a private supervisor-owned execution plan."""
    validate_transport_plan(plan)
    result = dict(plan)
    result["execution"] = {"allowed": True, "reason": "guarded-session-supervisor"}
    return result


class GuardedTransportAdapter:
    """Run only the installed FluxCast engine behind the package-owned helper."""

    def __init__(self, request: GuardRequest, *, authorization_command: tuple[str, ...] = ("pkexec",), env: Mapping[str, str] | None = None, pairing_pin: bytes | None = None) -> None:
        self.request = request.validate()
        self.authorization_command = authorization_command
        self.env = dict(os.environ if env is None else env)
        self.pairing_pin = validate_pairing_pin(pairing_pin) if pairing_pin is not None else None

    @staticmethod
    def _read_ready(process: subprocess.Popen[str], request: GuardRequest, cancelled: CancelCheck, *, stdout_drain: _BoundedPipeDrain, stderr_drain: _BoundedPipeDrain, timeout: float = 90) -> tuple[str, str] | None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if cancelled():
                return None
            line = stdout_drain.next_line(min(0.2, deadline - time.monotonic()))
            if line is not None:
                if stdout_drain.overflowed or len(line.encode("utf-8")) > 65_536:
                    raise TransportError("The networking helper returned an oversized readiness status.", code="guard-setup-failed")
                try:
                    payload = validate_helper_result(json.loads(line))
                except (json.JSONDecodeError, ValueError, RecursionError) as exc:
                    raise TransportError("The networking helper returned an invalid readiness status.", code="guard-setup-failed") from exc
                if payload.get("sessionId") != request.session_id:
                    raise TransportError("The networking helper status belongs to another session.", code="guard-setup-failed")
                if payload.get("ok") is True and payload.get("phase") == "ready":
                    trigger = payload.get("triggerPath")
                    broker = payload.get("brokerPath")
                    expected = f"/run/omarchy-cast/{request.session_id}/user/trigger"
                    expected_broker = f"/run/omarchy-cast/{request.session_id}/supplicant.sock"
                    if trigger != expected or broker != expected_broker:
                        raise TransportError("The networking helper returned unexpected session paths.", code="guard-setup-failed")
                    return expected, expected_broker
                raise TransportError(str(payload.get("error") or "The networking helper refused session preparation."), code="guard-setup-failed")
            try:
                payload = read_guard_status(request.session_id)
            except GuardError as exc:
                raise TransportError("The networking helper returned an unsafe status file.", code="guard-setup-failed") from exc
            if payload is not None:
                if payload.get("phase") == "ready" and payload.get("ok") is True:
                    return (
                        f"/run/omarchy-cast/{request.session_id}/user/trigger",
                        f"/run/omarchy-cast/{request.session_id}/supplicant.sock",
                    )
                if payload.get("phase") == "error":
                    raise TransportError(str(payload.get("error") or "The networking helper refused session preparation."), code="guard-setup-failed")
            if process.poll() is not None:
                error = stderr_drain.text().strip()
                if process.returncode in {126, 127}:
                    raise TransportError("Administrator approval was cancelled. Nothing was changed.", code="authorization-cancelled")
                if process.returncode != 0:
                    raise TransportError(error or "The networking helper exited before it was ready.", code="guard-setup-failed")
                time.sleep(0.05)
        raise TransportError("Administrator approval or guarded setup took too long. Try again and answer the approval prompt.", code="authorization-timeout")

    @staticmethod
    def _cleanup_confirmed(process: subprocess.Popen[str], request: GuardRequest, stdout_drain: _BoundedPipeDrain) -> bool:
        """Require the exited helper's final bounded status to confirm cleanup."""
        if process.poll() is None or stdout_drain.overflowed:
            return False
        lines = [line for line in stdout_drain.lines() if line.strip()]
        if not lines:
            return False
        try:
            payload = validate_helper_result(json.loads(lines[-1]))
        except (json.JSONDecodeError, ValueError, RecursionError):
            return False
        return payload.get("sessionId") == request.session_id and payload.get("ok") is True and payload.get("phase") == "cleaned"

    def _wait_cleanup_status(self, *, timeout: float = 15) -> bool:
        """Acknowledge only a terminal status from the root-owned session file."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                payload = read_guard_status(self.request.session_id)
            except GuardError:
                return False
            if payload is None or payload.get("phase") in {"ready", "active"}:
                time.sleep(0.1)
                continue
            acknowledged = self._write_private_marker(
                f"/run/omarchy-cast/{self.request.session_id}/user/status-ack"
            )
            return acknowledged and payload.get("phase") == "cleaned" and payload.get("ok") is True
        return False

    def _stop_guard(self) -> None:
        """Signal the user-owned guard marker without a second authorization."""
        stop_path = f"/run/omarchy-cast/{self.request.session_id}/user/stop"
        for _ in range(20):
            if self._write_private_marker(stop_path):
                return
            time.sleep(0.1)

    @staticmethod
    def _write_private_marker(path: str) -> bool:
        """Create one private regular marker without joining a special file."""
        descriptor: int | None = None
        try:
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_NONBLOCK | os.O_NOFOLLOW, 0o600)
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid() or metadata.st_mode & 0o077 or metadata.st_nlink != 1:
                return False
            os.fchmod(descriptor, 0o600)
            os.ftruncate(descriptor, 0)
            return True
        except OSError:
            # A missing marker directory means the helper already cleaned up.
            return False
        finally:
            if descriptor is not None:
                os.close(descriptor)

    @staticmethod
    def _bounded_detail(stream: str | None, fallback: str) -> str:
        """Keep a useful engine failure reason without turning state into a log sink."""
        if not stream:
            return fallback
        lines = [line.strip() for line in stream.splitlines() if line.strip()]
        return " | ".join(lines[-4:])[-2048:] or fallback

    @staticmethod
    def _failure_code(detail: str) -> str:
        lowered = detail.lower()
        if "incompatible provisioning method" in lowered or "may require pin pairing" in lowered:
            return "pairing-method-unsupported"
        if "pairing pin" in lowered or "pin pairing" in lowered:
            return "pairing-pin-failed"
        if "timed out waiting for a direct supplicant p2p group" in lowered or "p2p group formation failed" in lowered:
            return "p2p-negotiation-failed"
        if "dhcp" in lowered or "ip address" in lowered:
            return "dhcp-failed"
        if "networkmanager" in lowered:
            return "network-backend-unavailable"
        if "p2p" in lowered or "group formation" in lowered or "supplicant" in lowered:
            return "p2p-negotiation-failed"
        if "rtsp" in lowered or "miracast negotiation" in lowered:
            return "receiver-negotiation-failed"
        if "gpu screen recorder" in lowered or "capture" in lowered or "encoder" in lowered:
            return "capture-failed"
        return "engine-exited"

    @staticmethod
    def _media_started(progress_path: Path) -> bool:
        """Require a completed FFmpeg progress record with an encoded video frame."""
        return GuardedTransportAdapter._media_started_text(_bounded_read(progress_path))

    @staticmethod
    def _media_started_text(progress: str) -> bool:
        record: dict[str, str] = {}
        completed: dict[str, str] = {}
        for line in progress.splitlines():
            key, separator, value = line.partition("=")
            if not separator:
                continue
            record[key.strip()] = value.strip()
            if key.strip() == "progress":
                completed = record
                record = {}
        frame = completed.get("frame", "")
        if not _FRAME_COUNT.fullmatch(frame):
            return False
        parsed = int(frame)
        return 0 < parsed <= 2**63 - 1

    @staticmethod
    def _p2p_group_present(interface: str, network_root: Path = Path("/sys/class/net")) -> bool | None:
        """Prove that this session's interface still has a P2P group device."""
        for entry_index, path in enumerate(network_root.glob(f"p2p-{interface}-*")):
            if entry_index >= MAX_SYSFS_INTERFACE_ENTRIES:
                return None
            if path.is_dir():
                return True
        return False

    def _engine_command(self, plan: Mapping[str, Any], paths: Mapping[str, Path], trigger: str, broker: str, pairing_pin_fd: int | None = None) -> list[str]:
        """Attach session-owned instrumentation without coupling it to cast lifetime."""
        command = [str(value) for value in plan["command"]]
        command.extend(("--omacast-session", self.request.session_id))
        command.extend(("--wfd-progress-log", str(paths["progress"]), "--wfd-latency-log", str(paths["latency"])))
        command.extend((
            "--wfd-supplicant-network-trigger", trigger,
            "--wfd-supplicant-broker", broker,
            "--wfd-supplicant-hold", str(SUPPLICANT_GROUP_TIMEOUT_SECONDS),
        ))
        if pairing_pin_fd is not None:
            if pairing_pin_fd <= 2:
                raise TransportError("The pairing PIN descriptor is unsafe.")
            command.extend(("--wfd-pairing-pin-fd", str(pairing_pin_fd)))
        return command

    @staticmethod
    def _socket_inodes(pid: int) -> set[str] | None:
        """Return only sockets owned by the supervised engine process."""
        inodes: set[str] = set()
        try:
            descriptors = Path(f"/proc/{pid}/fd").iterdir()
            for descriptor_index, descriptor in enumerate(descriptors):
                if descriptor_index >= MAX_PROCESS_DESCRIPTORS:
                    return None
                try:
                    target = str(descriptor.readlink())
                except OSError:
                    continue
                if target.startswith("socket:[") and target.endswith("]"):
                    inodes.add(target[8:-1])
        except OSError:
            pass
        return inodes

    @classmethod
    def _rtsp_established(cls, engine_pid: int) -> bool | None:
        """Require an established RTSP socket owned by this session's engine."""
        owned = cls._socket_inodes(engine_pid)
        if owned is None:
            return None
        if not owned:
            return False
        text, complete = _bounded_stream_read(Path("/proc/net/tcp"), MAX_PROC_NETWORK_BYTES)
        if not complete:
            return None
        lines = text.splitlines()[1:]
        for line in lines:
            fields = line.split()
            if len(fields) >= 10 and fields[1].endswith(":1C44") and fields[3] == "01" and fields[9] in owned:
                return True
        return False

    def run(self, plan: Mapping[str, Any], *, timeout_seconds: float | None, cancelled: CancelCheck, stage: StageCallback) -> TransportResult:
        validate_transport_plan(plan, executable=True)
        selection = plan.get("selection")
        if not isinstance(selection, Mapping) or selection.get("networkBackend") != self.request.backend:
            raise TransportError("the guarded backend does not match the reviewed selection")
        if timeout_seconds is not None and not 60 <= timeout_seconds <= 86_400:
            raise TransportError("a bounded guarded transport must run between 60 seconds and 24 hours")
        helper: subprocess.Popen[str] | None = None
        engine: subprocess.Popen[str] | None = None
        sampler: TelemetrySampler | None = None
        telemetry: TelemetryWorkspace | None = None
        lease: SessionLease | None = None
        engine_output_collector: BoundedOutputCollector | None = None
        helper_stderr_drain: _BoundedPipeDrain | None = None
        helper_stdout_drain: _BoundedPipeDrain | None = None
        pairing_pin_read_fd: int | None = None
        guard_ready = False
        guard_cleanup_confirmed = False
        sampler_stopped = True
        startup_started_at = time.monotonic()
        try:
            helper = subprocess.Popen((*self.authorization_command, *prepare_command(self.request)), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=self.env)
            if helper.stderr is None:
                raise TransportError("The networking helper did not expose its diagnostic stream.", code="guard-setup-failed")
            if helper.stdout is None:
                raise TransportError("The networking helper did not provide a status stream.", code="guard-setup-failed")
            helper_stdout_drain = _BoundedPipeDrain(helper.stdout)
            helper_stderr_drain = _BoundedPipeDrain(helper.stderr)
            helper_stdout_drain.start()
            helper_stderr_drain.start()
            ready = self._read_ready(helper, self.request, cancelled, stdout_drain=helper_stdout_drain, stderr_drain=helper_stderr_drain)
            if ready is None:
                return TransportResult("cancelled", "stop requested during authorization", True)
            trigger, broker = ready
            guard_ready = True
            lease = SessionLease(Path(trigger).with_name("heartbeat"))
            lease.start()
            telemetry = TelemetryWorkspace(self.request.session_id, self.env)
            engine_paths = telemetry.prepare_engine_outputs()
            # Per-packet framecrc remains a research-only engine capability. It
            # is deliberately absent here so production diagnostics cannot add
            # a second unbounded FFmpeg output or steal time from capture.
            pass_fds: tuple[int, ...] = ()
            if self.pairing_pin is not None:
                pairing_pin_read_fd, pairing_pin_write_fd = os.pipe2(os.O_CLOEXEC)
                try:
                    if os.write(pairing_pin_write_fd, self.pairing_pin) != len(self.pairing_pin):
                        raise TransportError("The pairing PIN pipe could not be prepared.")
                finally:
                    os.close(pairing_pin_write_fd)
                pass_fds = (pairing_pin_read_fd,)
            command = self._engine_command(plan, engine_paths, trigger, broker, pairing_pin_read_fd)
            stage("connecting")
            engine = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=self.env, pass_fds=pass_fds)
            if pairing_pin_read_fd is not None:
                os.close(pairing_pin_read_fd)
                pairing_pin_read_fd = None
            if engine.stdout is None:
                raise TransportError("FluxCast did not expose its diagnostic stream", code="engine-exited")
            engine_output_collector = BoundedOutputCollector(engine.stdout, telemetry)
            engine_output_collector.start()
            engine_output_collector.note_startup("engine-started", time.monotonic() - startup_started_at)
            sampler = TelemetrySampler(
                session_id=self.request.session_id,
                engine_pid=engine.pid,
                wifi_interface=self.request.interface,
                environ=self.env,
                workspace=telemetry,
            )
            sampler.start()
            deadline = time.monotonic() + timeout_seconds if timeout_seconds is not None else float("inf")
            connect_deadline = time.monotonic() + CONNECT_TIMEOUT_SECONDS
            rtsp_ready_at: float | None = None
            p2p_missing_at: float | None = None
            streaming = False
            while engine.poll() is None and time.monotonic() < deadline:
                if cancelled():
                    return TransportResult("cancelled", "stop requested", True)
                now = time.monotonic()
                if rtsp_ready_at is None and self._rtsp_established(engine.pid) is True:
                    rtsp_ready_at = now
                    engine_output_collector.note_startup("rtsp-established", now - startup_started_at)
                if not streaming and rtsp_ready_at is not None and self._media_started_text(telemetry.read_text("progress")):
                    engine_output_collector.note_startup("first-frame", now - startup_started_at)
                    stage("streaming")
                    streaming = True
                if streaming:
                    group_present = self._p2p_group_present(self.request.interface)
                    if group_present is True:
                        p2p_missing_at = None
                    elif group_present is False and p2p_missing_at is None:
                        p2p_missing_at = now
                    elif group_present is False and now - p2p_missing_at >= RECEIVER_DISCONNECT_GRACE_SECONDS:
                        return TransportResult("completed", "receiver disconnected", True)
                if rtsp_ready_at is None and now >= connect_deadline:
                    return TransportResult(
                        "failed",
                        "The receiver did not complete Miracast negotiation within 75 seconds. Return it to Display Mirroring, then try again.",
                        True,
                        "receiver-negotiation-timeout",
                    )
                if not streaming and rtsp_ready_at is not None and now - rtsp_ready_at >= CAPTURE_START_TIMEOUT_SECONDS:
                    return TransportResult(
                        "failed",
                        "Desktop capture produced no video frames after the receiver connected.",
                        True,
                        "capture-failed",
                    )
                time.sleep(0.2)
            if engine.poll() is None:
                if not streaming and rtsp_ready_at is None:
                    return TransportResult(
                        "failed",
                        "The receiver did not complete Miracast negotiation before the bounded session ended. Return it to Display Mirroring, then try again.",
                        True,
                        "receiver-negotiation-timeout",
                    )
                if not streaming:
                    return TransportResult("failed", "Desktop capture produced no video frames.", True, "capture-failed")
                return TransportResult("timeout", "guarded stream duration elapsed", True)
            if engine_output_collector is not None:
                engine_output_collector.stop()
            engine_output = telemetry.read_text("engineLog")
            detail = self._bounded_detail(engine_output, "FluxCast exited" if engine.returncode == 0 else f"FluxCast exited with status {engine.returncode}")
            return TransportResult("completed", detail, True) if engine.returncode == 0 else TransportResult("failed", detail, True, self._failure_code(engine_output))
        finally:
            if pairing_pin_read_fd is not None:
                os.close(pairing_pin_read_fd)
            if sampler is not None:
                sampler_stopped = sampler.stop()
            if engine is not None and engine.poll() is None:
                engine.terminate()
                try:
                    engine.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    engine.kill()
                    try:
                        engine.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        pass
            if sampler is not None and not sampler_stopped:
                sampler_stopped = sampler.stop()
            if lease is not None:
                lease.stop()
            if guard_ready:
                self._stop_guard()
            if helper is not None:
                # The process launched through pkexec may already have exec'd
                # the root-owned helper. Always try the session-owned stop
                # marker first, and never let EPERM mask the real setup error.
                if not guard_ready:
                    self._stop_guard()
                if not guard_ready and helper.poll() is None:
                    try:
                        helper.terminate()
                    except (PermissionError, ProcessLookupError):
                        pass
                try:
                    helper.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    try:
                        helper.kill()
                    except (PermissionError, ProcessLookupError):
                        pass
                    try:
                        helper.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        pass
            if helper_stdout_drain is not None:
                helper_stdout_drain.stop()
            if guard_ready:
                guard_cleanup_confirmed = self._wait_cleanup_status()
            if helper_stderr_drain is not None:
                helper_stderr_drain.stop()
            if engine_output_collector is not None:
                engine_output_collector.stop()
            if telemetry is not None and sampler_stopped:
                telemetry.close()
            if sampler_stopped:
                cleanup_live_telemetry(self.request.session_id, self.env)
            if guard_ready and not guard_cleanup_confirmed:
                raise TransportError(
                    "The networking helper could not confirm complete P2P cleanup. Re-scan before casting again.",
                    code="guard-cleanup-incomplete",
                )


class DisabledTransportAdapter:
    """An explicit fail-closed adapter for negative tests and unsupported hosts."""

    def run(self, plan: Mapping[str, Any], *, timeout_seconds: float | None, cancelled: CancelCheck, stage: StageCallback) -> TransportResult:
        del plan, timeout_seconds, cancelled, stage
        raise TransportDisabled("real transport is disabled until the P2P helper and FluxCast path pass hardware validation")


class FakeTransportAdapter:
    """Deterministic no-process adapter for success and cleanup-contract tests."""

    def __init__(self, scenario: str = "success") -> None:
        if scenario not in {"success", "timeout", "failure", "cancelled"}:
            raise TransportError("unsupported fake transport scenario")
        self.scenario = scenario
        self.calls: list[str] = []

    def run(self, plan: Mapping[str, Any], *, timeout_seconds: float | None, cancelled: CancelCheck, stage: StageCallback) -> TransportResult:
        validate_test_transport_plan(plan)
        if timeout_seconds is None or not 1 <= timeout_seconds <= 300:
            raise TransportError("transport timeout must be between 1 and 300 seconds")
        self.calls.append("start")
        stage("connecting")
        if cancelled() or self.scenario == "cancelled":
            self.calls.append("cleanup")
            return TransportResult("cancelled", "stop requested before streaming", True)
        if self.scenario == "timeout":
            self.calls.append("cleanup")
            return TransportResult("timeout", "fake transport timeout", True)
        if self.scenario == "failure":
            self.calls.append("cleanup")
            return TransportResult("failed", "fake transport failure", True)
        stage("streaming")
        self.calls.append("cleanup")
        return TransportResult("completed", "fake transport completed", True)


def result_payload(result: TransportResult) -> dict[str, object]:
    return asdict(result)
