"""The safe command surface for Omacast."""

from __future__ import annotations

import argparse
import json
import os
import re
import signal
import sys
from uuid import uuid4

from .bounds import MAX_UI_RESPONSE_BYTES, bounded_text
from .discovery import discover_host
from .engine import LaunchPlanError, build_launch_plan
from .guard import GuardError, GuardRequest, orphan_parent_interfaces, reclaim_orphan_interfaces
from .identity import receiver_address
from .media_probe import MediaProbeError, probe_media
from .pairing import PairingError, read_pairing_credential, read_pairing_pin_stdin
from .receivers import DEMO_FIRE_TV, FixtureReceiverDiscovery, FluxCastReceiverDiscovery, Receiver, ReceiverDiscoveryUnavailable, ReceiverError, discovery_payload, receiver_payload
from .service import ServiceError, start_session_service, stop_pending_session_service
from .session import DryRunSupervisor, SessionError, SimulatedSupervisor, TransportTestSupervisor, read_session_events, recover_stale_session, request_stop, session_history
from .state import StateError, read_state, session_lock_is_held
from .telemetry import read_telemetry
from .transport import GUARD_LEASE_SECONDS, FakeTransportAdapter, GuardedTransportAdapter, executable_plan
from .wfd_fixture import INCOMPATIBLE_VIDEO_FIXTURE, SUCCESS_FIXTURE, TIMEOUT_FIXTURE, result_payload as wfd_result_payload, run_wfd_fixture


MAX_STREAMED_SCAN_SNAPSHOTS = 64
DEFAULT_SCAN_TIMEOUT_SECONDS = 16


def _bounded_cli_integer(*, minimum: int, maximum: int, label: str):
    maximum_width = len(str(maximum))

    def parse(value: str) -> int:
        if not re.fullmatch(rf"[0-9]{{1,{maximum_width}}}", value):
            raise argparse.ArgumentTypeError(f"{label} must be between {minimum} and {maximum}")
        parsed = int(value)
        if not minimum <= parsed <= maximum:
            raise argparse.ArgumentTypeError(f"{label} must be between {minimum} and {maximum}")
        return parsed

    return parse


_SCAN_TIMEOUT = _bounded_cli_integer(minimum=1, maximum=30, label="scan timeout")
_SESSION_DURATION = _bounded_cli_integer(minimum=0, maximum=86_400, label="session duration")
_LOG_LIMIT = _bounded_cli_integer(minimum=1, maximum=50, label="history limit")


def _emit(value: object) -> None:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n"
    if len(payload.encode("utf-8")) > MAX_UI_RESPONSE_BYTES:
        payload = json.dumps({
            "schemaVersion": 1,
            "ok": False,
            "error": {"code": "response-too-large", "message": "Controller response exceeded its bounded UI contract."},
        }, sort_keys=True, separators=(",", ":")) + "\n"
    sys.stdout.write(payload)
    sys.stdout.flush()


class _ScanCancelled(Exception):
    """Turn SIGTERM into normal unwinding so discovery always calls StopFind."""


def _error_message(exc: BaseException) -> str:
    return bounded_text(str(exc), limit=512, fallback="The controller operation failed.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="omacast", description="Cast your Omarchy desktop to a Miracast display")
    subcommands = parser.add_subparsers(dest="command", required=True)
    subcommands.add_parser("doctor", help="read-only dependency and host discovery")
    subcommands.add_parser("monitors", help="read-only Hyprland monitor discovery")
    subcommands.add_parser("status", help="read session state")
    scan = subcommands.add_parser("scan", help="scan for nearby Miracast receivers without connecting")
    scan.add_argument(
        "--timeout", type=_SCAN_TIMEOUT, default=DEFAULT_SCAN_TIMEOUT_SECONDS,
        help="Wi-Fi Direct scan duration in seconds (1-30)",
    )
    scan.add_argument("--stream", action="store_true", help="emit each changed receiver snapshot as one JSON line")
    receivers = subcommands.add_parser("receivers", help="list nearby Miracast receivers")
    receivers.add_argument("--fixture", action="store_true", help="return the deterministic development Fire TV fixture")
    receivers.add_argument(
        "--timeout", type=_SCAN_TIMEOUT, default=DEFAULT_SCAN_TIMEOUT_SECONDS,
        help="Wi-Fi Direct scan duration in seconds (1-30)",
    )
    plan = subcommands.add_parser("plan", help="preview the supported FluxCast command; read-only")
    plan.add_argument("--peer", required=True, help="Wi-Fi Direct receiver MAC address")
    plan.add_argument("--mode", choices=("mirror",), default="mirror")
    plan.add_argument("--profile", choices=("safe",), default="safe")
    plan.add_argument("--backend", choices=("direct", "networkmanager"), default="direct")
    plan.add_argument("--monitor", help="Hyprland output name; uses the focused output by default")
    dry_run = subcommands.add_parser("dry-run", help="exercise guarded supervision without an engine or network")
    dry_run.add_argument("--peer", required=True, help="Wi-Fi Direct receiver MAC address")
    dry_run.add_argument("--mode", choices=("mirror",), default="mirror")
    dry_run.add_argument("--profile", choices=("safe",), default="safe")
    dry_run.add_argument("--backend", choices=("direct", "networkmanager"), default="direct")
    dry_run.add_argument("--monitor", help="Hyprland output name; uses the focused output by default")
    transport_test = subcommands.add_parser("transport-test", help="exercise fake transport ownership; never invokes FluxCast")
    transport_test.add_argument("--peer", required=True, help="Wi-Fi Direct receiver MAC address")
    transport_test.add_argument("--mode", choices=("mirror",), default="mirror")
    transport_test.add_argument("--profile", choices=("safe",), default="safe")
    transport_test.add_argument("--backend", choices=("direct", "networkmanager"), default="direct")
    transport_test.add_argument("--monitor", help="Hyprland output name; uses the focused output by default")
    transport_test.add_argument("--scenario", choices=("success", "timeout", "failure", "cancelled"), default="success")
    protocol_test = subcommands.add_parser("protocol-test", help="run offline recorded-style WFD negotiation fixtures")
    protocol_test.add_argument("--scenario", choices=("success", "incompatible", "timeout"), default="success")
    media_probe = subcommands.add_parser("media-probe", help="encode one synthetic second locally; no capture or network")
    media_probe.add_argument("--profile", choices=("safe",), default="safe")
    start = subcommands.add_parser("start", help="start a cast in a shell-independent user service")
    start.add_argument("--peer", required=True, help="Wi-Fi Direct receiver MAC address")
    start.add_argument("--mode", choices=("mirror",), default="mirror")
    start.add_argument("--profile", choices=("safe",), default="safe")
    start.add_argument("--backend", choices=("direct", "networkmanager"), default="direct")
    start.add_argument("--duration", type=_SESSION_DURATION, default=0, help="optional acceptance-test duration in seconds; 0 casts until stopped")
    start.add_argument("--simulate", action="store_true", help=argparse.SUPPRESS)
    start.add_argument("--pairing-pin-stdin", action="store_true", help="read one receiver-displayed WPS PIN from stdin")
    connect = subcommands.add_parser("connect", help="run the supported guarded session (normally started by Omacast)")
    connect.add_argument("--peer", required=True, help="Wi-Fi Direct receiver MAC address")
    connect.add_argument("--mode", choices=("mirror",), default="mirror")
    connect.add_argument("--profile", choices=("safe",), default="safe")
    connect.add_argument("--backend", choices=("direct", "networkmanager"), default="direct")
    connect.add_argument("--simulate", action="store_true", help="exercise lifecycle only; never touches hardware")
    connect.add_argument("--duration", type=_SESSION_DURATION, default=0, help="optional acceptance-test duration in seconds; 0 casts until stopped")
    connect.add_argument("--pairing-pin-credential", action="store_true", help=argparse.SUPPRESS)
    subcommands.add_parser("stop", help="request cooperative stop of an active supervised session")
    recover = subcommands.add_parser("recover", help="safely clear stale runtime state when no session owns the lock")
    logs = subcommands.add_parser("logs", help="read bounded local session history")
    logs.add_argument("--limit", type=_LOG_LIMIT, default=10, help="number of summaries to return (1-50)")
    logs.add_argument("--session", help="read detailed events for one controller-issued session id")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command in {"doctor", "monitors"}:
        snapshot = discover_host()
        if args.command == "monitors":
            _emit({"schemaVersion": 1, "monitors": snapshot["monitors"], "diagnostics": snapshot["diagnostics"]})
        else:
            _emit(snapshot)
        return 0
    if args.command == "status":
        try:
            state = read_state()
            if state["phase"] not in {"idle", "error"} and not session_lock_is_held():
                stale_phase = str(state["phase"])
                state = {
                    **state,
                    "phase": "error",
                    "error": {
                        "code": "session-owner-lost",
                        "message": f"The {stale_phase} session is no longer running. Restore casting state to continue.",
                    },
                }
            session_id = state.get("sessionId")
            if isinstance(session_id, str):
                telemetry = read_telemetry(session_id)
                if telemetry is not None:
                    state["telemetry"] = telemetry
            _emit(state)
        except StateError as exc:
            _emit({"schemaVersion": 1, "phase": "error", "error": {"code": "runtime-state-invalid", "message": _error_message(exc)}})
            return 1
        return 0
    if args.command in {"scan", "receivers"}:
        try:
            fixture = args.command == "receivers" and args.fixture
            if not fixture:
                state = read_state()
                if state["phase"] != "idle":
                    raise ReceiverDiscoveryUnavailable("stop the active cast before scanning for receivers")
            snapshot = discover_host() if not fixture else {}
            links = snapshot.get("wifiLinks", []) if isinstance(snapshot, dict) else []
            interface = next((link.get("interface") for link in links if isinstance(link, dict) and link.get("connected") and isinstance(link.get("interface"), str)), None)
            discovery = FixtureReceiverDiscovery(DEMO_FIRE_TV) if fixture else FluxCastReceiverDiscovery(interface=interface)
            if args.command == "scan" and args.stream:
                last_emitted: list[Receiver] | None = None
                emitted_snapshots = 0

                def publish(receivers: list[Receiver]) -> None:
                    nonlocal emitted_snapshots, last_emitted
                    if emitted_snapshots >= MAX_STREAMED_SCAN_SNAPSHOTS:
                        raise ReceiverDiscoveryUnavailable("receiver discovery produced too many changed snapshots")
                    _emit(receiver_payload(receivers))
                    emitted_snapshots += 1
                    last_emitted = receivers

                def cancel_scan(_signum: int, _frame: object) -> None:
                    raise _ScanCancelled()

                previous_handler = signal.signal(signal.SIGTERM, cancel_scan)
                try:
                    final_receivers = discovery.watch_receivers(timeout_seconds=args.timeout, callback=publish)
                    if final_receivers != last_emitted:
                        publish(final_receivers)
                finally:
                    signal.signal(signal.SIGTERM, previous_handler)
            else:
                _emit(discovery_payload(discovery, timeout_seconds=args.timeout))
        except _ScanCancelled:
            return 0
        except (ReceiverError, ReceiverDiscoveryUnavailable, StateError) as exc:
            _emit({"schemaVersion": 1, "ok": False, "error": {"code": "receiver-scan-failed", "message": _error_message(exc)}})
            return 2
        return 0
    if args.command == "plan":
        try:
            _emit(build_launch_plan(discover_host(), peer=args.peer, mode=args.mode, profile=args.profile, monitor=args.monitor, backend=args.backend))
        except LaunchPlanError as exc:
            _emit({"schemaVersion": 1, "ok": False, "error": {"code": "launch-plan-invalid", "message": _error_message(exc)}})
            return 1
        return 0
    if args.command == "dry-run":
        try:
            plan = build_launch_plan(discover_host(), peer=args.peer, mode=args.mode, profile=args.profile, monitor=args.monitor, backend=args.backend)
            _emit(DryRunSupervisor().run(peer=args.peer, mode=args.mode, profile=args.profile, plan=plan))
        except (LaunchPlanError, SessionError, StateError) as exc:
            _emit({"schemaVersion": 1, "ok": False, "error": {"code": "dry-run-failed", "message": _error_message(exc)}})
            return 1
        return 0
    if args.command == "transport-test":
        try:
            plan = build_launch_plan(discover_host(), peer=args.peer, mode=args.mode, profile=args.profile, monitor=args.monitor, backend=args.backend)
            result = TransportTestSupervisor(FakeTransportAdapter(args.scenario)).run(peer=args.peer, mode=args.mode, profile=args.profile, plan=plan)
            _emit(result)
            return 0 if result["ok"] else 1
        except (LaunchPlanError, SessionError, StateError) as exc:
            _emit({"schemaVersion": 1, "ok": False, "error": {"code": "transport-test-failed", "message": _error_message(exc)}})
            return 1
    if args.command == "protocol-test":
        fixture = {"success": SUCCESS_FIXTURE, "incompatible": INCOMPATIBLE_VIDEO_FIXTURE, "timeout": TIMEOUT_FIXTURE}[args.scenario]
        _emit({"schemaVersion": 1, "offline": True, "scenario": args.scenario, "result": wfd_result_payload(run_wfd_fixture(fixture))})
        return 0
    if args.command == "media-probe":
        try:
            result = probe_media(discover_host(), profile=args.profile)
            _emit(result)
            return 0 if result["ok"] else 1
        except MediaProbeError as exc:
            _emit({"schemaVersion": 1, "ok": False, "error": {"code": "media-probe-failed", "message": _error_message(exc)}})
            return 1
    if args.command == "logs":
        try:
            _emit(read_session_events(args.session) if args.session else session_history(limit=args.limit))
        except SessionError as exc:
            _emit({"schemaVersion": 1, "ok": False, "error": {"code": "log-read-failed", "message": _error_message(exc)}})
            return 1
        return 0
    if args.command == "start":
        try:
            state = read_state()
            if state["phase"] != "idle":
                raise SessionError("another Omacast session is already active")
            peer = args.peer if args.simulate else receiver_address(args.peer)
            duration = args.duration or (1 if args.simulate else 0)
            if args.simulate:
                if not 1 <= duration <= 300:
                    raise SessionError("simulation duration must be between 1 and 300 seconds")
            elif duration != 0 and not 60 <= duration <= 86_400:
                raise SessionError("a bounded guarded session must run between 60 seconds and 24 hours")
            pairing_pin = read_pairing_pin_stdin(sys.stdin.buffer) if args.pairing_pin_stdin else None
            _emit(start_session_service(
                executable=sys.argv[0],
                peer=peer,
                mode=args.mode,
                profile=args.profile,
                duration=duration,
                simulate=args.simulate,
                pairing_pin=pairing_pin,
                backend=args.backend,
            ))
            return 0
        except (ServiceError, SessionError, StateError, ValueError) as exc:
            _emit({"schemaVersion": 1, "ok": False, "error": {"code": "session-start-failed", "message": _error_message(exc)}})
            return 1
    if args.command == "recover":
        try:
            snapshot = discover_host()
            if not isinstance(snapshot, dict) or snapshot.get("wifiDevicesComplete") is not True:
                raise GuardError("Wi-Fi recovery discovery was incomplete")
            links = snapshot.get("wifiLinks", []) if isinstance(snapshot, dict) else []
            interfaces = [
                link.get("interface") for link in links
                if isinstance(link, dict) and isinstance(link.get("interface"), str)
            ]
            reclaimed = 0
            for interface in orphan_parent_interfaces(interfaces):
                reclaimed += int(reclaim_orphan_interfaces(interface)["reclaimed"])
            recovery = recover_stale_session()
            _emit({**recovery, "reclaimedP2pInterfaces": reclaimed})
        except (GuardError, SessionError, StateError) as exc:
            _emit({"schemaVersion": 1, "ok": False, "error": {"code": "recovery-unavailable", "message": _error_message(exc)}})
            return 1
        return 0
    if args.command == "connect" and args.simulate:
        if args.pairing_pin_credential:
            _emit({"schemaVersion": 1, "ok": False, "error": {"code": "simulation-failed", "message": "Pairing credentials are not accepted by simulated sessions."}})
            return 1
        try:
            _emit(SimulatedSupervisor().run(peer=args.peer, mode=args.mode, profile=args.profile, duration=args.duration or 1))
        except (SessionError, StateError) as exc:
            _emit({"schemaVersion": 1, "ok": False, "error": {"code": "simulation-failed", "message": _error_message(exc)}})
            return 1
        return 0
    if args.command == "connect":
        try:
            mode = args.mode
            profile = args.profile
            peer = receiver_address(args.peer)
            preview = build_launch_plan(discover_host(), peer=peer, mode=mode, profile=profile, backend=args.backend)
            selection = preview["selection"]
            if not isinstance(selection, dict) or not isinstance(selection.get("wifiInterface"), str):
                raise SessionError("host discovery did not provide a safe Wi-Fi interface")
            session_id = uuid4().hex
            if args.duration != 0 and not 60 <= args.duration <= 86_400:
                raise SessionError("a bounded guarded session must run between 60 seconds and 24 hours")
            p2p_frequency = selection.get("p2pFrequencyMhz")
            if type(p2p_frequency) is not int:
                raise SessionError("launch plan did not provide a safe P2P frequency")
            request = GuardRequest(
                1,
                session_id,
                os.getuid(),
                selection["wifiInterface"],
                peer,
                p2p_frequency,
                GUARD_LEASE_SECONDS,
                args.backend,
            )
            pairing_pin = read_pairing_credential(os.environ) if args.pairing_pin_credential else None
            result = TransportTestSupervisor(GuardedTransportAdapter(request, pairing_pin=pairing_pin)).run(peer=peer, mode=mode, profile=profile, plan=executable_plan(preview), timeout_seconds=args.duration or None, session_id=session_id, executable=True, production=True)
            _emit(result)
            return 0 if result["ok"] else 1
        except (LaunchPlanError, PairingError, SessionError, StateError, ValueError) as exc:
            _emit({"schemaVersion": 1, "ok": False, "error": {"code": "guarded-session-failed", "message": _error_message(exc)}})
            return 1
    if args.command == "stop":
        try:
            state = read_state()
            if state["phase"] == "idle":
                result = stop_pending_session_service()
                # systemctl returns only after the transient unit is dead, so
                # it is now safe to clear state published during cancellation.
                recovery = recover_stale_session()
                result["recovered"] = bool(recovery["recovered"])
                _emit(result)
            else:
                _emit(request_stop())
        except (ServiceError, SessionError, StateError) as exc:
            _emit({"schemaVersion": 1, "ok": False, "error": {"code": "stop-unavailable", "message": _error_message(exc)}})
            return 1
        return 0
    raise AssertionError("argparse accepted an unknown controller command")


if __name__ == "__main__":
    sys.exit(main())
