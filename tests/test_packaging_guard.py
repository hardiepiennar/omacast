from __future__ import annotations

from pathlib import Path
import json
import os
import re
import shutil
import socket
import subprocess
import tempfile
import time
import unittest
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]


class PackagingGuardTest(unittest.TestCase):
    def test_plugin_and_controller_versions_match(self) -> None:
        manifest_version = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))["version"]
        project = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        match = re.search(r'^version\s*=\s*"([^"]+)"$', project, re.MULTILINE)
        self.assertIsNotNone(match)
        self.assertEqual(match.group(1), manifest_version)

    def test_guard_scripts_are_shell_valid_and_session_scoped(self) -> None:
        guard = ROOT / "packaging" / "arch" / "omarchy-cast-guard"
        recovery = ROOT / "packaging" / "arch" / "omarchy-cast-guard-recover"
        for script in (guard, recovery):
            result = subprocess.run(("bash", "-n", str(script)), check=False, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
        version = subprocess.run(("bash", str(guard), "--version"), check=False, capture_output=True, text=True)
        self.assertEqual(version.returncode, 0, version.stderr)
        self.assertEqual(json.loads(version.stdout), {"schemaVersion": 1, "kind": "omarchy-cast-guard-version", "apiRevision": 10})
        source = guard.read_text(encoding="utf-8")
        self.assertIn('[[ "$action" == prepare ]] || usage', source)
        self.assertNotIn('"$action" == stop', source)
        self.assertNotIn('stop() {', source)
        self.assertNotIn(']] && prepare || stop', source)
        self.assertIn('parse_request "$@"', source)
        self.assertNotIn('action="$(parse_request', source)
        self.assertIn('duration="${14}"', source)
        self.assertNotIn('policy user=', source)
        self.assertNotIn('/etc/dbus-1/system.d', source)
        self.assertNotIn('work/fluxcast', source)
        self.assertIn('p2p-$interface-*', source)
        self.assertIn('omarchy-cast-guard-recover', source)
        self.assertIn('heartbeat_file="$user_root/heartbeat"', source)
        self.assertIn('heartbeat_fd=', source)
        self.assertIn('stat -Lc \'%F:%u:%a:%s\' "/proc/self/fd/$heartbeat_fd"', source)
        self.assertIn('read -r -n 32 -t 0.1 renewed < "/proc/self/fd/$heartbeat_fd"', source)
        self.assertNotIn('renewed="$(<"$heartbeat_file")"', source)
        self.assertIn('lease_fresh', source)
        self.assertNotIn('active_deadline', source)
        self.assertIn('prepare_network_runtime', source)
        self.assertIn('networkd_state_file="$session_root/networkd-units"', source)
        self.assertIn("systemd network runtime directory is unsafe", source)
        self.assertIn('restore_networkd_state', source)
        self.assertIn('runtime_dirs_created=false', source)
        self.assertIn("api_revision=10", source)
        self.assertIn('user_root="$session_root/user"', source)
        self.assertNotIn('user_root="/run/user/', source)
        self.assertIn('install -d -m711 "$session_root"', source)
        self.assertIn("root session directory is unsafe", source)
        self.assertIn("user marker directory is unsafe", source)
        self.assertIn('interfaces_file="$session_root/p2p-interfaces"', source)
        self.assertIn('interfaces_armed_file="$session_root/p2p-armed"', source)
        self.assertIn('broker_socket="$session_root/supplicant.sock"', source)
        self.assertIn('omarchy-cast-supplicant-broker', source)
        self.assertIn('systemd-run --quiet --collect --unit="$broker_unit"', source)
        self.assertIn('network_manager_resume_file="$session_root/network-manager-resume-required"', source)
        self.assertIn("record_session_interfaces", source)
        self.assertIn("remove_session_interfaces", source)
        self.assertIn("verify_clean_interface_baseline", source)
        self.assertNotIn("remove_stale_interfaces", source)
        active_loop = source.split('while owns_session && [[ ! -e "$stop_file" ]]; do', 1)[1].split("done", 1)[0]
        self.assertNotIn("record_session_interfaces", active_loop)
        self.assertNotIn("iw dev", active_loop)
        self.assertIn('[[ "${PKEXEC_UID:-}" == "$uid" ]]', source)
        self.assertNotIn("systemd-networkd is already active", source)
        self.assertIn("systemctl reload systemd-networkd.service", source)
        self.assertIn("if systemctl is-active --quiet systemd-networkd.service", source)
        self.assertIn('"$1" == --version', source)
        prepare_body = source.split('prepare() {', 1)[1].split('}', 1)[0]
        self.assertLess(prepare_body.index("trap 'cleanup' EXIT"), prepare_body.index('create_session_identity'))
        self.assertLess(prepare_body.index('create_session_identity'), prepare_body.index('arm_recovery'))
        self.assertLess(prepare_body.index('arm_recovery'), prepare_body.index('write_privileged_runtime'))
        self.assertLess(prepare_body.index('write_privileged_runtime'), prepare_body.index('start_broker'))
        identity_body = source.split('create_session_identity() {', 1)[1].split('\n}', 1)[0]
        self.assertLess(identity_body.index('[[ ! -e "$session_root"'), identity_body.index('install -d -m711 "$session_root"'))
        self.assertNotIn('systemctl', identity_body)
        self.assertNotIn('prepare_network_runtime', identity_body)
        privileged_body = source.split('write_privileged_runtime() {', 1)[1].split('\n}', 1)[0]
        self.assertIn('prepare_network_runtime', privileged_body)
        self.assertNotIn('dbus.service', privileged_body)
        recovery_source = recovery.read_text(encoding="utf-8")
        self.assertIn('lease_seconds="$2"', recovery_source)
        self.assertIn('heartbeat_file="$user_root/heartbeat"', recovery_source)
        self.assertIn('user_root="$root/user"', recovery_source)
        self.assertIn('interfaces_file="$root/p2p-interfaces"', recovery_source)
        self.assertIn('interfaces_armed_file="$root/p2p-armed"', recovery_source)
        self.assertIn('network_manager_resume_file="$root/network-manager-resume-required"', recovery_source)
        self.assertIn('broker_unit="omarchy-cast-supplicant-$session.service"', recovery_source)
        self.assertIn('clear_owned_wfd_ies', recovery_source)
        self.assertIn("record_session_interfaces", recovery_source)
        self.assertIn("remove_session_interfaces", recovery_source)
        self.assertIn('heartbeat_fd=', recovery_source)
        self.assertIn('stat -Lc \'%F:%u:%a:%s\' "/proc/self/fd/$heartbeat_fd"', recovery_source)
        self.assertIn('read -r -n 32 -t 0.1 renewed < "/proc/self/fd/$heartbeat_fd"', recovery_source)
        self.assertNotIn('renewed="$(<"$heartbeat_file")"', recovery_source)
        self.assertIn('networkd_state_file="$root/networkd-units"', recovery_source)
        self.assertIn('recovery_ready_file="$root/recovery-ready"', recovery_source)
        self.assertIn('publish_recovery_ready || exit 1', recovery_source)
        for removed_surface in ("qos.pid", "qos_file", "apply_media_qos", "renice", "/proc/$root_pid"):
            self.assertNotIn(removed_surface, source)
            self.assertNotIn(removed_surface, recovery_source)
        self.assertIn("systemctl reload systemd-networkd.service", recovery_source)
        self.assertNotIn('systemctl stop systemd-networkd.service systemd-networkd.socket', recovery_source)
        for helper_source in (source, recovery_source):
            self.assertNotIn("nm_pid", helper_source)
            self.assertNotRegex(helper_source, r"kill\s+-(?:STOP|CONT)")
            self.assertIn("systemctl kill --kill-whom=main --signal=SIGCONT NetworkManager.service", helper_source)
        self.assertIn("systemctl kill --kill-whom=main --signal=SIGSTOP NetworkManager.service", source)
        self.assertIn("remove_cleanup_file", source)
        self.assertIn("remove_recovery_file", recovery_source)
        self.assertIn("recover_session", recovery_source)

    def test_guard_requires_recovery_readiness_before_returning(self) -> None:
        guard = ROOT / "packaging" / "arch" / "omarchy-cast-guard"
        harness = r'''
source <(sed '/^if \[\[ \$# -eq 1/,$d' "$1")
recovery_ready_file="$2/recovery-ready"
duration=60
session_id=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
token=bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb
uid=1000
interface=wlan0
setsid() { install -m600 /dev/null "$recovery_ready_file"; sleep 2; }
arm_recovery
recovery_ready_marker_valid
for child in $(jobs -pr); do kill "$child" 2>/dev/null || true; wait "$child" 2>/dev/null || true; done
rm -f -- "$recovery_ready_file"
setsid() { return 1; }
if arm_recovery; then exit 3; fi
[[ ! -e "$recovery_ready_file" ]]
'''
        with tempfile.TemporaryDirectory() as temp:
            result = subprocess.run(
                ("bash", "-euo", "pipefail", "-c", harness, "_", str(guard), temp),
                check=False,
                capture_output=True,
                text=True,
                timeout=2,
            )
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_broker_socket_cleanup_is_exact_type_owner_and_mode_only(self) -> None:
        guard = ROOT / "packaging" / "arch" / "omarchy-cast-guard"
        harness = r'''
source <(sed '/^if \[\[ \$# -eq 1/,$d' "$1")
broker_socket="$2"
uid="$3"
remove_broker_socket
'''
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            path = root / "supplicant.sock"
            listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            try:
                listener.bind(str(path))
                path.chmod(0o600)
                result = subprocess.run(
                    ("bash", "-euo", "pipefail", "-c", harness, "_", str(guard), str(path), str(os.getuid())),
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=2,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertFalse(path.exists())
            finally:
                listener.close()

            target = root / "target"
            target.write_text("preserve", encoding="ascii")
            path.symlink_to(target)
            result = subprocess.run(
                ("bash", "-euo", "pipefail", "-c", harness, "_", str(guard), str(path), str(os.getuid())),
                check=False,
                capture_output=True,
                text=True,
                timeout=2,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertTrue(path.is_symlink())
            self.assertEqual(target.read_text(encoding="ascii"), "preserve")

    def test_recovery_publishes_readiness_only_for_protected_identity(self) -> None:
        recovery = ROOT / "packaging" / "arch" / "omarchy-cast-guard-recover"
        harness = r'''
source <(sed -n '/^record_session_interfaces()/,/^publish_recovery_ready ||/p' "$1" | sed '$d')
root="$2/session"
user_root="$root/user"
token_file="$root/token"
recovery_ready_file="$root/recovery-ready"
uid="$3"
token=cccccccccccccccccccccccccccccccccccccccccccccccc
install -d -m711 "$root"
install -d -m700 "$user_root"
printf '%s\n' "$token" > "$token_file"
chmod 600 "$token_file"
publish_recovery_ready
[[ -f "$recovery_ready_file" && ! -L "$recovery_ready_file" ]]
rm -f -- "$recovery_ready_file"
chmod 644 "$token_file"
if publish_recovery_ready; then exit 3; fi
[[ ! -e "$recovery_ready_file" ]]
'''
        with tempfile.TemporaryDirectory() as temp:
            result = subprocess.run(
                ("bash", "-euo", "pipefail", "-c", harness, "_", str(recovery), temp, str(os.getuid())),
                check=False,
                capture_output=True,
                text=True,
                timeout=2,
            )
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_recovery_handles_every_partial_privileged_initialization_stage(self) -> None:
        recovery = ROOT / "packaging" / "arch" / "omarchy-cast-guard-recover"
        harness = r'''
source <(sed -n '/^record_session_interfaces()/,/^publish_recovery_ready ||/p' "$1" | sed '$d')
root="$2/session"
user_root="$root/user"
network_root="$2/network"
calls="$3"
phase="$4"
session=deadbeef
interface=wlan0
token_file="$root/token"
ready_file="$root/ready.json"
trigger_file="$user_root/trigger"
heartbeat_file="$user_root/heartbeat"
stop_file="$user_root/stop"
network_file="$network_root/session.network"
networkd_state_file="$root/networkd-units"
networkd_state_pending_file="$root/networkd-units.pending"
network_root_marker="$root/network-root-created"
interfaces_file="$root/p2p-interfaces"
interfaces_armed_file="$root/p2p-armed"
network_manager_resume_file="$root/network-manager-resume-required"
recovery_ready_file="$root/recovery-ready"
broker_unit="omarchy-cast-supplicant-$session.service"
broker_socket="$root/supplicant.sock"
broker_wfd_file="$root/supplicant-wfd-owned"
recovery_cleanup_ok=true
mkdir -p "$user_root" "$network_root"
touch "$token_file" "$recovery_ready_file"
case "$phase" in
  minimal) ;;
  wfd) touch "$broker_wfd_file"; chmod 600 "$broker_wfd_file" ;;
  pending) touch "$networkd_state_pending_file" ;;
  state) touch "$networkd_state_file" ;;
  *) exit 4 ;;
esac
resume_network_manager() { return 0; }
restore_networkd_state() { printf '%s\n' restore-networkd >> "$calls"; return 0; }
systemctl() { [[ "$1" == is-active ]] && return 1; printf '%s\n' "$*" >> "$calls"; }
gdbus() { printf '%s\n' clear-wfd >> "$calls"; }
ufw() { return 1; }
recover_session
[[ "$recovery_cleanup_ok" == true ]]
[[ ! -e "$token_file" && ! -e "$recovery_ready_file" && ! -e "$networkd_state_pending_file" ]]
case "$phase" in
  minimal|pending) ! grep -Fq restore-networkd "$calls"; ! grep -Fq clear-wfd "$calls" ;;
  wfd) grep -Fxq clear-wfd "$calls"; [[ ! -e "$broker_wfd_file" ]]; ! grep -Fq restore-networkd "$calls" ;;
  state) grep -Fxq restore-networkd "$calls"; ! grep -Fq clear-wfd "$calls" ;;
esac
'''
        for phase in ("minimal", "wfd", "pending", "state"):
            with self.subTest(phase=phase), tempfile.TemporaryDirectory() as temp:
                calls = Path(temp) / "calls"
                result = subprocess.run(
                    ("bash", "-euo", "pipefail", "-c", harness, "_", str(recovery), temp, str(calls), phase),
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=2,
                )
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_guard_cleanup_continues_after_user_marker_removal_failure(self) -> None:
        guard = ROOT / "packaging" / "arch" / "omarchy-cast-guard"
        harness = r'''
source <(sed '/^if \[\[ \$# -eq 1/,$d' "$1")
session_root="$2/session"
user_root="$session_root/user"
calls="$3"
token_file="$session_root/token"
ready_file="$session_root/ready.json"
trigger_file="$user_root/trigger"
heartbeat_file="$user_root/heartbeat"
stop_file="$user_root/stop"
network_file="$2/session.network"
networkd_state_file="$session_root/networkd-units"
networkd_state_pending_file="$session_root/networkd-units.pending"
network_root_marker="$session_root/network-root-created"
interfaces_file="$session_root/p2p-interfaces"
interfaces_armed_file="$session_root/p2p-armed"
network_manager_resume_file="$session_root/network-manager-resume-required"
recovery_ready_file="$session_root/recovery-ready"
broker_socket="$session_root/supplicant.sock"
broker_unit="omarchy-cast-supplicant-deadbeef.service"
broker_wfd_file="$session_root/supplicant-wfd-owned"
broker_started=false
token=owned
runtime_dirs_created=true
mkdir -p "$user_root" "$user_root/$4"
printf '%s\n' "$token" > "$token_file"
touch "$ready_file" "$network_file" "$networkd_state_file" "$interfaces_file"
for marker in trigger heartbeat stop; do [[ "$marker" == "$4" ]] || touch "$user_root/$marker"; done
resume_network_manager() { printf '%s\n' resume >> "$calls"; }
restore_networkd_state() { printf '%s\n' restore-networkd >> "$calls"; }
systemctl() { [[ "$1" == is-active ]] && return 1; printf '%s\n' "$*" >> "$calls"; }
cleanup
[[ "$cleanup_ok" == false ]]
[[ -d "$user_root/$4" ]]
[[ -f "$token_file" ]]
[[ ! -e "$network_file" ]]
for marker in trigger heartbeat stop; do [[ "$marker" == "$4" || ! -e "$user_root/$marker" ]]; done
grep -Fxq restore-networkd "$calls"
'''
        for marker in ("trigger", "heartbeat", "stop"):
            with self.subTest(marker=marker), tempfile.TemporaryDirectory() as temp:
                calls = Path(temp) / "calls"
                result = subprocess.run(
                    ("bash", "-euo", "pipefail", "-c", harness, "_", str(guard), temp, str(calls), marker),
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=2,
                )
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_recovery_attempts_every_restoration_after_independent_failures(self) -> None:
        recovery = ROOT / "packaging" / "arch" / "omarchy-cast-guard-recover"
        harness = r'''
source <(sed -n '/^record_session_interfaces()/,/^publish_recovery_ready ||/p' "$1" | sed '$d')
root="$2/session"
user_root="$root/user"
network_root="$2/network"
calls="$3"
session=deadbeef
interface=wlan0
token_file="$root/token"
ready_file="$root/ready.json"
trigger_file="$user_root/trigger"
heartbeat_file="$user_root/heartbeat"
stop_file="$user_root/stop"
network_file="$network_root/session.network"
networkd_state_file="$root/networkd-units"
networkd_state_pending_file="$root/networkd-units.pending"
network_root_marker="$root/network-root-created"
interfaces_file="$root/p2p-interfaces"
interfaces_armed_file="$root/p2p-armed"
network_manager_resume_file="$root/network-manager-resume-required"
recovery_ready_file="$root/recovery-ready"
broker_unit="omarchy-cast-supplicant-$session.service"
broker_socket="$root/supplicant.sock"
broker_wfd_file="$root/supplicant-wfd-owned"
recovery_cleanup_ok=true
mkdir -p "$user_root" "$network_root" "$user_root/$4"
touch "$token_file" "$ready_file" "$network_file" "$networkd_state_file" "$interfaces_file"
for marker in trigger heartbeat stop; do [[ "$marker" == "$4" ]] || touch "$user_root/$marker"; done
resume_network_manager() { printf '%s\n' resume >> "$calls"; return 1; }
restore_networkd_state() { printf '%s\n' restore-networkd >> "$calls"; return 1; }
systemctl() { printf '%s\n' "$*" >> "$calls"; }
ufw() { return 1; }
recover_session
[[ "$recovery_cleanup_ok" == false ]]
[[ -d "$user_root/$4" ]]
[[ -f "$token_file" && -f "$networkd_state_file" ]]
[[ ! -e "$network_file" ]]
for marker in trigger heartbeat stop; do [[ "$marker" == "$4" || ! -e "$user_root/$marker" ]]; done
grep -Fxq resume "$calls"
grep -Fxq restore-networkd "$calls"
'''
        for marker in ("trigger", "heartbeat", "stop"):
            with self.subTest(marker=marker), tempfile.TemporaryDirectory() as temp:
                calls = Path(temp) / "calls"
                result = subprocess.run(
                    ("bash", "-euo", "pipefail", "-c", harness, "_", str(recovery), temp, str(calls), marker),
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=2,
                )
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_network_manager_pause_is_unit_scoped_owned_and_idempotent(self) -> None:
        guard = ROOT / "packaging" / "arch" / "omarchy-cast-guard"
        harness = r'''
source <(sed '/^if \[\[ \$# -eq 1/,$d' "$1")
network_manager_resume_file="$2"
calls="$3"
stop_result=0
systemctl() {
  if [[ "$1" == is-active ]]; then return 0; fi
  printf '%s\n' "$*" >> "$calls"
  if [[ "$*" == *SIGSTOP* ]]; then return "$stop_result"; fi
}
pause_network_manager
network_manager_marker_valid
resume_network_manager
[[ ! -e "$network_manager_resume_file" ]]
resume_network_manager
stop_result=1
if pause_network_manager; then exit 3; fi
[[ ! -e "$network_manager_resume_file" ]]
network_manager_resume_file="$2/missing/marker"
stop_result=0
if pause_network_manager; then exit 4; fi
'''
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            marker = root / "network-manager-resume-required"
            calls = root / "calls"
            result = subprocess.run(
                ("bash", "-euo", "pipefail", "-c", harness, "_", str(guard), str(marker), str(calls)),
                check=False,
                capture_output=True,
                text=True,
                timeout=2,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                calls.read_text(encoding="ascii").splitlines(),
                [
                    "kill --kill-whom=main --signal=SIGSTOP NetworkManager.service",
                    "kill --kill-whom=main --signal=SIGCONT NetworkManager.service",
                    "kill --kill-whom=main --signal=SIGSTOP NetworkManager.service",
                ],
            )

    def test_recovery_preserves_broker_ownership_if_unit_cannot_stop(self) -> None:
        recovery = ROOT / "packaging" / "arch" / "omarchy-cast-guard-recover"
        harness = r'''
source <(sed -n '/^record_session_interfaces()/,/^publish_recovery_ready ||/p' "$1" | sed '$d')
root="$2/session"; user_root="$root/user"; network_root="$2/network"; calls="$3"
session=deadbeef; interface=wlan0; uid="$4"
token_file="$root/token"; ready_file="$root/ready.json"; trigger_file="$user_root/trigger"; heartbeat_file="$user_root/heartbeat"; stop_file="$user_root/stop"
network_file="$network_root/session.network"; networkd_state_file="$root/networkd-units"; networkd_state_pending_file="$root/networkd-units.pending"; network_root_marker="$root/network-root-created"
interfaces_file="$root/p2p-interfaces"; interfaces_armed_file="$root/p2p-armed"; network_manager_resume_file="$root/network-manager-resume-required"; recovery_ready_file="$root/recovery-ready"
broker_unit="omarchy-cast-supplicant-$session.service"; broker_socket="$root/supplicant.sock"; broker_wfd_file="$root/supplicant-wfd-owned"; recovery_cleanup_ok=true
mkdir -p "$user_root" "$network_root"; touch "$token_file" "$network_file" "$networkd_state_file" "$interfaces_file" "$broker_wfd_file"; chmod 600 "$broker_wfd_file"
resume_network_manager() { printf '%s\n' resume >> "$calls"; return 0; }
restore_networkd_state() { printf '%s\n' restore-networkd >> "$calls"; return 0; }
systemctl() { if [[ "$1" == is-active && "${3:-}" == "$broker_unit" ]]; then return 0; fi; [[ "$1" == stop && "${2:-}" == "$broker_unit" ]] && return 1; return 0; }
gdbus() { printf '%s\n' unexpected-wfd-clear >> "$calls"; return 0; }
ufw() { return 1; }
recover_session
[[ "$recovery_cleanup_ok" == false ]]
[[ -f "$token_file" && -f "$broker_wfd_file" ]]
[[ ! -e "$network_file" ]]
grep -Fxq resume "$calls"
grep -Fxq restore-networkd "$calls"
! grep -Fq unexpected-wfd-clear "$calls"
'''
        with tempfile.TemporaryDirectory() as temp:
            calls = Path(temp) / "calls"
            result = subprocess.run(
                ("bash", "-euo", "pipefail", "-c", harness, "_", str(recovery), temp, str(calls), str(os.getuid())),
                check=False,
                capture_output=True,
                text=True,
                timeout=3,
            )
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_network_manager_resume_failure_retains_recovery_ownership(self) -> None:
        guard = ROOT / "packaging" / "arch" / "omarchy-cast-guard"
        harness = r'''
source <(sed '/^if \[\[ \$# -eq 1/,$d' "$1")
network_manager_resume_file="$2"
attempts="$3"
systemctl() {
  printf '%s\n' "$*" >> "$attempts"
  return 1
}
install -m600 /dev/null "$network_manager_resume_file"
if resume_network_manager; then exit 3; fi
network_manager_marker_valid
systemctl() {
  printf '%s\n' "$*" >> "$attempts"
  return 0
}
rm() { return 1; }
if resume_network_manager; then exit 4; fi
network_manager_marker_valid
'''
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            marker = root / "network-manager-resume-required"
            attempts = root / "attempts"
            result = subprocess.run(
                ("bash", "-euo", "pipefail", "-c", harness, "_", str(guard), str(marker), str(attempts)),
                check=False,
                capture_output=True,
                text=True,
                timeout=2,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(marker.is_file())
            self.assertEqual(len(attempts.read_text(encoding="ascii").splitlines()), 4)

    def test_root_recovery_never_traverses_user_telemetry(self) -> None:
        recovery_source = (ROOT / "packaging" / "arch" / "omarchy-cast-guard-recover").read_text(encoding="utf-8")
        self.assertNotIn("/run/user/", recovery_source)
        self.assertNotIn("telemetry", recovery_source)
        for live_name in ("current.json", "ffmpeg.progress", "mux-packets.csv", "engine.jsonl", "engine.log"):
            self.assertNotIn(live_name, recovery_source)

    def test_recipe_installs_the_immutable_privilege_boundary(self) -> None:
        recipe = (ROOT / "packaging" / "arch" / "PKGBUILD").read_text(encoding="utf-8")
        self.assertIn('omarchy-cast-guard"', recipe)
        self.assertIn('omarchy-cast-guard-recover"', recipe)
        self.assertIn('omarchy-cast-supplicant-broker"', recipe)
        self.assertIn('com.omacast.guard.policy"', recipe)
        self.assertIn("PYTHONPATH=src python -m unittest discover -s tests", recipe)
        self.assertNotIn("PYSTRAY_BACKEND", recipe)
        depends = recipe.split("depends=(", 1)[1].split(")", 1)[0]
        for dependency in ("ffmpeg", "networkmanager", "wpa_supplicant", "iw", "libpulse", "polkit", "systemd", "iproute2", "glib2"):
            self.assertIn(f"'{dependency}'", depends)
        for removed in ("gstreamer", "gst-plugin-pipewire", "gst-plugins-base-libs"):
            self.assertNotIn(f"'{removed}'", depends)

    def test_guard_pins_the_verified_heartbeat_descriptor(self) -> None:
        guard = ROOT / "packaging" / "arch" / "omarchy-cast-guard"
        harness = r'''
source <(sed '/^if \[\[ \$# -eq 1/,$d' "$1")
heartbeat_file="$2"
uid="$3"
duration=60
open_heartbeat
unlink "$heartbeat_file"
mkfifo -m 600 "$heartbeat_file"
lease_fresh
'''
        with tempfile.TemporaryDirectory() as temp:
            heartbeat = Path(temp) / "heartbeat"
            heartbeat.write_text(f"{int(time.time())}\n", encoding="ascii")
            heartbeat.chmod(0o600)
            result = subprocess.run(
                ("bash", "-euo", "pipefail", "-c", harness, "_", str(guard), str(heartbeat), str(os.getuid())),
                check=False,
                capture_output=True,
                text=True,
                timeout=2,
            )
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_guard_rejects_a_fifo_heartbeat_without_blocking(self) -> None:
        guard = ROOT / "packaging" / "arch" / "omarchy-cast-guard"
        harness = r'''
source <(sed '/^if \[\[ \$# -eq 1/,$d' "$1")
heartbeat_file="$2"
uid="$3"
if open_heartbeat; then exit 3; fi
'''
        with tempfile.TemporaryDirectory() as temp:
            heartbeat = Path(temp) / "heartbeat"
            os.mkfifo(heartbeat, mode=0o600)
            result = subprocess.run(
                ("bash", "-euo", "pipefail", "-c", harness, "_", str(guard), str(heartbeat), str(os.getuid())),
                check=False,
                capture_output=True,
                text=True,
                timeout=2,
            )
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_guard_removes_only_recorded_session_p2p_clients(self) -> None:
        guard = ROOT / "packaging" / "arch" / "omarchy-cast-guard"
        harness = r'''
source <(sed '/^if \[\[ \$# -eq 1/,$d' "$1")
interfaces_file="$2"
deleted_file="$3"
interface=wlan42
delete_attempts=0
interface_present=true
interface_type=P2P-client
iw() {
  if [[ "$#" -eq 1 && "$1" == dev ]]; then
    printf '%s\n' 'phy#0' '  Interface p2p-wlan42-0' '  Interface p2p-wlan99-0'
  elif [[ "$#" -eq 3 && "$1" == dev && "$3" == info ]]; then
    [[ "$interface_present" == true ]] || return 1
    printf '%s\n' 'Interface details' "type $interface_type"
  elif [[ "$#" -eq 3 && "$1" == dev && "$3" == del ]]; then
    delete_attempts=$((delete_attempts + 1))
    (( delete_attempts >= 3 )) || return 1
    interface_present=false
    printf '%s\n' "$2" >> "$deleted_file"
  else
    return 2
  fi
}
if (verify_clean_interface_baseline); then exit 4; fi
: > "$interfaces_file"
chmod 600 "$interfaces_file"
record_session_interfaces
remove_session_interfaces
interface_present=true
interface_type=managed
printf '%s\n' p2p-wlan42-1 > "$interfaces_file"
if remove_session_interfaces; then exit 5; fi
printf '%s\n' p2p-wlan42-0 > "$interfaces_file"
'''
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            interfaces = root / "interfaces"
            deleted = root / "deleted"
            result = subprocess.run(
                ("bash", "-euo", "pipefail", "-c", harness, "_", str(guard), str(interfaces), str(deleted)),
                check=False,
                capture_output=True,
                text=True,
                timeout=2,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(interfaces.read_text(encoding="ascii"), "p2p-wlan42-0\n")
            self.assertEqual(deleted.read_text(encoding="ascii"), "p2p-wlan42-0\n")

    def test_guard_rejects_special_or_public_interface_records(self) -> None:
        guard = ROOT / "packaging" / "arch" / "omarchy-cast-guard"
        harness = r'''
source <(sed '/^if \[\[ \$# -eq 1/,$d' "$1")
interfaces_file="$2"
interface=wlan42
iw() { printf '%s\n' 'phy#0' '  Interface p2p-wlan42-0'; }
if record_session_interfaces; then exit 3; fi
'''
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            record = root / "interfaces"
            target = root / "target"
            target.write_text("untouched", encoding="ascii")
            cases = ("fifo", "symlink", "public")
            for case in cases:
                with self.subTest(case=case):
                    record.unlink(missing_ok=True)
                    if case == "fifo":
                        os.mkfifo(record, mode=0o600)
                    elif case == "symlink":
                        record.symlink_to(target)
                    else:
                        record.write_text("", encoding="ascii")
                        record.chmod(0o644)
                    result = subprocess.run(
                        ("bash", "-euo", "pipefail", "-c", harness, "_", str(guard), str(record)),
                        check=False,
                        capture_output=True,
                        text=True,
                        timeout=2,
                    )
                    self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(target.read_text(encoding="ascii"), "untouched")

    def test_polkit_action_is_exact_and_active_local_only(self) -> None:
        policy = ET.parse(ROOT / "packaging" / "arch" / "com.omacast.guard.policy").getroot()
        action = policy.find("action")
        self.assertIsNotNone(action)
        assert action is not None
        self.assertEqual(action.attrib, {"id": "com.omacast.guard.prepare"})
        defaults = action.find("defaults")
        self.assertIsNotNone(defaults)
        assert defaults is not None
        self.assertEqual({child.tag: child.text for child in defaults}, {
            "allow_any": "no", "allow_inactive": "no", "allow_active": "yes",
        })
        annotations = {item.attrib["key"]: item.text for item in action.findall("annotate")}
        self.assertEqual(annotations, {
            "org.freedesktop.policykit.exec.path": "/usr/lib/omarchy-cast/omarchy-cast-guard",
            "org.freedesktop.policykit.exec.argv1": "prepare",
        })

    def test_bootstrap_and_package_share_the_complete_patch_series(self) -> None:
        series = (ROOT / "patches" / "production" / "series").read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(series), 32)
        expected_numbers = (
            [f"{number:04d}" for number in range(1, 7)]
            + [f"{number:04d}" for number in range(9, 23)]
            + ["0027", "0028", "0029", "0030", "0031", "0032", "0033", "0034", "0035", "0036", "0037", "0038"]
        )
        self.assertEqual([name[:4] for name in series], expected_numbers)
        for name in series:
            self.assertTrue((ROOT / "patches" / "production" / name).is_file(), name)
        bootstrap = (ROOT / "scripts" / "bootstrap-fluxcast").read_text(encoding="utf-8")
        recipe = (ROOT / "packaging" / "arch" / "PKGBUILD").read_text(encoding="utf-8")
        self.assertIn('series_file="$repo_root/patches/production/series"', bootstrap)
        self.assertIn('done < "$startdir/../../patches/production/series"', recipe)
        commits = re.findall(r"#commit=([0-9a-f]+)", recipe)
        self.assertEqual(commits, ["9d27c39670940ada3a0e520a1d70574910646083"])

    def test_release_builder_uses_an_exact_clean_clone(self) -> None:
        builder = (ROOT / "scripts" / "build-release-artifact").read_text(encoding="utf-8")
        self.assertIn("status --porcelain --untracked-files=normal", builder)
        self.assertIn('git clone --quiet --no-local "$repo_root"', builder)
        self.assertIn('[[ "$clone_commit" == "$source_commit" ]]', builder)
        self.assertIn('sha256sum "$package_name" > SHA256SUMS', builder)
        self.assertIn('pacman -Q | sort > BUILD-ENVIRONMENT.txt', builder)
        self.assertIn('mktemp -d -p /tmp "omacast-release-build.${UID}.XXXXXXXX"', builder)
        self.assertIn('"$(stat -c %u:%a "$build_root")" == "$UID:700"', builder)
        self.assertNotIn('omacast-release-build-${UID}.lock', builder)
        self.assertNotIn('exec 9>', builder)
        self.assertIn('mv -T -- "$publish_root/$artifact" "./$artifact"', builder)
        self.assertIn('output directory must not be group- or world-writable', builder)
        self.assertIn("trap 'exit 143' TERM", builder)

    def test_release_builder_replaces_output_links_without_touching_targets(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "source"
            (source / "scripts").mkdir(parents=True)
            (source / "packaging" / "arch").mkdir(parents=True)
            (source / "packaging" / "arch" / ".keep").write_text("fixture\n", encoding="utf-8")
            shutil.copy2(ROOT / "scripts" / "build-release-artifact", source / "scripts" / "build-release-artifact")
            subprocess.run(("git", "init", "-q", str(source)), check=True)
            subprocess.run(("git", "-C", str(source), "add", "."), check=True)
            subprocess.run(
                (
                    "git", "-C", str(source), "-c", "user.name=Omacast test",
                    "-c", "user.email=test@localhost", "commit", "-qm", "fixture",
                ),
                check=True,
            )

            fake_bin = root / "bin"
            fake_bin.mkdir()
            makepkg = fake_bin / "makepkg"
            makepkg.write_text(
                "#!/usr/bin/env bash\nset -eu\nprintf package > fluxcast-omarchy-cast-9.9-1-any.pkg.tar.zst\n",
                encoding="utf-8",
            )
            pacman = fake_bin / "pacman"
            pacman.write_text(
                "#!/usr/bin/env bash\nset -eu\n"
                "case \"${1:-}\" in\n"
                "  -Qip) printf 'Name : fluxcast-omarchy-cast\\n' ;;\n"
                "  -Q) printf 'base 1\\n' ;;\n"
                "  *) exit 2 ;;\n"
                "esac\n",
                encoding="utf-8",
            )
            makepkg.chmod(0o755)
            pacman.chmod(0o755)

            output = root / "output"
            output.mkdir(mode=0o700)
            artifacts = (
                "fluxcast-omarchy-cast-9.9-1-any.pkg.tar.zst",
                "PACKAGE-INFO.txt",
                "BUILD-ENVIRONMENT.txt",
                "SOURCE-COMMIT.txt",
                "SHA256SUMS",
            )
            victims = []
            for index, artifact in enumerate(artifacts):
                victim = root / f"victim-{index}"
                victim.write_text(f"keep-{index}", encoding="utf-8")
                if index % 3 == 0:
                    (output / artifact).symlink_to(victim)
                elif index % 3 == 1:
                    os.link(victim, output / artifact)
                else:
                    os.mkfifo(output / artifact, mode=0o600)
                victims.append(victim)

            environment = os.environ.copy()
            environment["PATH"] = f"{fake_bin}:{environment['PATH']}"
            result = subprocess.run(
                (str(source / "scripts" / "build-release-artifact"), str(output)),
                check=False,
                capture_output=True,
                text=True,
                env=environment,
                timeout=20,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            for index, (artifact, victim) in enumerate(zip(artifacts, victims, strict=True)):
                self.assertEqual(victim.read_text(encoding="utf-8"), f"keep-{index}")
                self.assertFalse((output / artifact).is_symlink())
                self.assertTrue((output / artifact).is_file())

    def test_artifact_audit_is_no_root_and_checks_the_runtime_contract(self) -> None:
        audit = (ROOT / "scripts" / "audit-release-artifact").read_text(encoding="utf-8")
        self.assertIn("omacast-artifact-audit.", audit)
        self.assertIn("omarchy-cast-guard-version", audit)
        self.assertIn("apiRevision", audit)
        self.assertIn("omarchy-cast-supplicant-broker", audit)
        self.assertIn("temporary system-bus policy", audit)
        self.assertIn("pinned heartbeat descriptor", audit)
        self.assertIn("bound pinned heartbeat reads", audit)
        self.assertIn("reopens the user heartbeat by pathname", audit)
        self.assertIn("anchored below the root-owned session", audit)
        self.assertIn("protected session parent", audit)
        self.assertIn("unused privileged Stop verb", audit)
        self.assertIn("recorded P2P cleanup", audit)
        self.assertIn("clean-baseline ownership marker", audit)
        self.assertIn("root recovery traverses the user runtime tree", audit)
        self.assertIn("root recovery manages user-owned telemetry", audit)
        self.assertIn("root recovery removes user-owned telemetry", audit)
        self.assertIn("com.omacast.guard.policy", audit)
        self.assertIn("allow_active", audit)
        self.assertNotIn("--wfd-gsr-handoff", audit)
        self.assertIn("--wfd-supplicant-network-trigger", audit)
        self.assertIn("--wfd-supplicant-broker", audit)
        self.assertIn("packaged engine exposes a non-WFD protocol", audit)
        self.assertIn("packaged engine retains excluded module", audit)
        self.assertIn("packaged engine retains UIBC input surface", audit)
        self.assertIn("package retains legacy integration payload", audit)
        self.assertIn("fluxcast-install-system", audit)
        self.assertIn("pypi_sysinstall.py", audit)
        self.assertIn("_fluxcast_data", audit)
        self.assertNotIn("PYSTRAY_BACKEND", audit)
        self.assertIn("package retains an unused protocol dependency", audit)
        self.assertNotIn("--wfd-portal-source", audit)
        self.assertIn('pacman -Qp "$package"', audit)
        self.assertNotIn("pacman -Qkp", audit)
        self.assertIn('pkgver = $expected_version', audit)
        self.assertNotIn("sudo", audit)
        self.assertNotIn("pacman -U", audit)
        self.assertIn("--trusted-local-artifact", audit)
        refused = subprocess.run(
            (str(ROOT / "scripts" / "audit-release-artifact"),),
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("refusing to execute package content", refused.stderr)

    def test_passwordless_policy_has_a_documented_narrow_boundary(self) -> None:
        policy = (ROOT / "packaging" / "arch" / "com.omacast.guard.policy").read_text(encoding="utf-8")
        security = (ROOT / "SECURITY.md").read_text(encoding="utf-8")
        self.assertIn("<allow_any>no</allow_any>", policy)
        self.assertIn("<allow_inactive>no</allow_inactive>", policy)
        self.assertIn("<allow_active>yes</allow_active>", policy)
        self.assertIn("org.freedesktop.policykit.exec.path", policy)
        self.assertIn("org.freedesktop.policykit.exec.argv1", policy)
        self.assertIn("same-session denial-of-service risk", security)
        self.assertIn("does not provide a general privileged command", security)

    def test_package_lifecycle_is_disposable_and_no_root(self) -> None:
        lifecycle = (ROOT / "scripts" / "test-package-lifecycle").read_text(encoding="utf-8")
        self.assertIn("omacast-package-lifecycle.", lifecycle)
        self.assertIn("fakeroot pacman", lifecycle)
        self.assertIn("-Qkk", lifecycle)
        self.assertIn("-Rdd", lifecycle)
        self.assertIn("0 altered files", lifecycle)
        self.assertIn("com.omacast.guard.policy", lifecycle)
        self.assertIn("supplicant broker", lifecycle)
        self.assertNotIn("sudo", lifecycle)
        self.assertIn("--trusted-local-artifact", lifecycle)
        refused = subprocess.run(
            (str(ROOT / "scripts" / "test-package-lifecycle"),),
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("refusing to install or execute package content", refused.stderr)

    def test_release_workflow_pins_actions_and_attests_tagged_packages(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
        action_refs = re.findall(r"uses:\s+[^@\s]+@([^\s#]+)", workflow)
        self.assertGreaterEqual(len(action_refs), 3)
        self.assertTrue(all(re.fullmatch(r"[0-9a-f]{40}", ref) for ref in action_refs))
        self.assertIn("actions/attest-build-provenance@", workflow)
        self.assertIn("scripts/audit-release-artifact", workflow)
        self.assertIn("scripts/test-package-lifecycle", workflow)
        self.assertGreaterEqual(workflow.count("--trusted-local-artifact"), 2)
        self.assertIn("scripts/lint-shell", workflow)
        self.assertIn("shellcheck", workflow)
        self.assertLess(workflow.index("scripts/lint-shell"), workflow.index("scripts/build-release-artifact"))
        self.assertLess(workflow.index("scripts/audit-release-artifact"), workflow.index("actions/attest-build-provenance@"))
        self.assertLess(workflow.index("scripts/test-package-lifecycle"), workflow.index("actions/attest-build-provenance@"))
        self.assertIn('[[ "$GITHUB_REF_NAME" == "v$version" ]]', workflow)
        self.assertIn('[[ "$(cat dist/SOURCE-COMMIT.txt)" == "$GITHUB_SHA" ]]', workflow)
        self.assertRegex(workflow, r"archlinux:base-devel@sha256:[0-9a-f]{64}")
        self.assertIn("archive.archlinux.org/repos/2026/08/24", workflow)
        for removed in ("python-pillow", "python-pychromecast", "python-pystray"):
            self.assertNotIn(removed, workflow)
        self.assertIn("BUILD-ENVIRONMENT.txt", workflow)
        self.assertIn("RELEASE-BUILDER.txt", workflow)
        for dependency in ("ffmpeg", "iproute2", "iw", "libpulse", "networkmanager", "polkit", "systemd", "wpa_supplicant"):
            self.assertIn(dependency, workflow)

    def test_shell_lint_covers_production_surfaces_without_rewriting_lab_evidence(self) -> None:
        lint = (ROOT / "scripts" / "lint-shell").read_text(encoding="utf-8")
        for path in ("omarchy-cast-guard", "omarchy-cast-guard-recover", "audit-release-artifact", "bootstrap-fluxcast", "build-release-artifact", "test-package-lifecycle", "validate-plugin", "PKGBUILD"):
            self.assertIn(path, lint)
        self.assertNotIn("scripts/lab/", lint)
