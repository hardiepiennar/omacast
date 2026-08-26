from __future__ import annotations

from pathlib import Path
import json
import os
import re
import subprocess
import tempfile
import time
import unittest
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]


class PackagingGuardTest(unittest.TestCase):
    def test_guard_scripts_are_shell_valid_and_session_scoped(self) -> None:
        guard = ROOT / "packaging" / "arch" / "omarchy-cast-guard"
        recovery = ROOT / "packaging" / "arch" / "omarchy-cast-guard-recover"
        for script in (guard, recovery):
            result = subprocess.run(("bash", "-n", str(script)), check=False, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
        version = subprocess.run(("bash", str(guard), "--version"), check=False, capture_output=True, text=True)
        self.assertEqual(version.returncode, 0, version.stderr)
        self.assertEqual(json.loads(version.stdout), {"schemaVersion": 1, "kind": "omarchy-cast-guard-version", "apiRevision": 8})
        source = guard.read_text(encoding="utf-8")
        self.assertIn('[[ "$action" == prepare ]] || usage', source)
        self.assertNotIn('"$action" == stop', source)
        self.assertNotIn('stop() {', source)
        self.assertNotIn(']] && prepare || stop', source)
        self.assertIn('parse_request "$@"', source)
        self.assertNotIn('action="$(parse_request', source)
        self.assertIn('duration="${10}"', source)
        self.assertIn('policy user=', source)
        self.assertNotIn('policy context="default"', source)
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
        self.assertIn("api_revision=8", source)
        self.assertIn('user_root="$session_root/user"', source)
        self.assertNotIn('user_root="/run/user/', source)
        self.assertIn('install -d -m711 "$session_root"', source)
        self.assertIn("root session directory is unsafe", source)
        self.assertIn("user marker directory is unsafe", source)
        self.assertIn('interfaces_file="$session_root/p2p-interfaces"', source)
        self.assertIn('interfaces_armed_file="$session_root/p2p-armed"', source)
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
        self.assertLess(prepare_body.index("trap 'cleanup' EXIT"), prepare_body.index('write_runtime_files'))
        write_body = source.split('write_runtime_files() {', 1)[1].split('\n}', 1)[0]
        self.assertLess(write_body.index('[[ ! -e "$session_root"'), write_body.index('install -d -m711 "$session_root"'))
        recovery_source = recovery.read_text(encoding="utf-8")
        self.assertIn('lease_seconds="$2"', recovery_source)
        self.assertIn('heartbeat_file="$user_root/heartbeat"', recovery_source)
        self.assertIn('user_root="$root/user"', recovery_source)
        self.assertIn('interfaces_file="$root/p2p-interfaces"', recovery_source)
        self.assertIn('interfaces_armed_file="$root/p2p-armed"', recovery_source)
        self.assertIn('network_manager_resume_file="$root/network-manager-resume-required"', recovery_source)
        self.assertIn("record_session_interfaces", recovery_source)
        self.assertIn("remove_session_interfaces", recovery_source)
        self.assertIn('heartbeat_fd=', recovery_source)
        self.assertIn('stat -Lc \'%F:%u:%a:%s\' "/proc/self/fd/$heartbeat_fd"', recovery_source)
        self.assertIn('read -r -n 32 -t 0.1 renewed < "/proc/self/fd/$heartbeat_fd"', recovery_source)
        self.assertNotIn('renewed="$(<"$heartbeat_file")"', recovery_source)
        self.assertIn('networkd_state_file="$root/networkd-units"', recovery_source)
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
policy_file="$2/session.conf"
networkd_state_file="$session_root/networkd-units"
network_root_marker="$session_root/network-root-created"
interfaces_file="$session_root/p2p-interfaces"
interfaces_armed_file="$session_root/p2p-armed"
network_manager_resume_file="$session_root/network-manager-resume-required"
token=owned
runtime_dirs_created=true
mkdir -p "$user_root" "$user_root/$4"
printf '%s\n' "$token" > "$token_file"
touch "$ready_file" "$network_file" "$policy_file" "$networkd_state_file" "$interfaces_file"
for marker in trigger heartbeat stop; do [[ "$marker" == "$4" ]] || touch "$user_root/$marker"; done
resume_network_manager() { printf '%s\n' resume >> "$calls"; }
restore_networkd_state() { printf '%s\n' restore-networkd >> "$calls"; }
systemctl() { printf '%s\n' "$*" >> "$calls"; }
cleanup
[[ "$cleanup_ok" == false ]]
[[ -d "$user_root/$4" ]]
[[ -f "$token_file" ]]
[[ ! -e "$network_file" && ! -e "$policy_file" ]]
for marker in trigger heartbeat stop; do [[ "$marker" == "$4" || ! -e "$user_root/$marker" ]]; done
grep -Fxq 'reload dbus.service' "$calls"
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
source <(sed -n '/^record_session_interfaces()/,/^started=/p' "$1" | sed '$d')
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
policy_file="$2/session.conf"
networkd_state_file="$root/networkd-units"
network_root_marker="$root/network-root-created"
interfaces_file="$root/p2p-interfaces"
interfaces_armed_file="$root/p2p-armed"
network_manager_resume_file="$root/network-manager-resume-required"
recovery_cleanup_ok=true
mkdir -p "$user_root" "$network_root" "$user_root/$4"
touch "$token_file" "$ready_file" "$network_file" "$policy_file" "$networkd_state_file" "$interfaces_file"
for marker in trigger heartbeat stop; do [[ "$marker" == "$4" ]] || touch "$user_root/$marker"; done
resume_network_manager() { printf '%s\n' resume >> "$calls"; return 1; }
restore_networkd_state() { printf '%s\n' restore-networkd >> "$calls"; return 1; }
systemctl() { printf '%s\n' "$*" >> "$calls"; }
ufw() { return 1; }
recover_session
[[ "$recovery_cleanup_ok" == false ]]
[[ -d "$user_root/$4" ]]
[[ -f "$token_file" && -f "$networkd_state_file" ]]
[[ ! -e "$network_file" && ! -e "$policy_file" ]]
for marker in trigger heartbeat stop; do [[ "$marker" == "$4" || ! -e "$user_root/$marker" ]]; done
grep -Fxq resume "$calls"
grep -Fxq 'reload dbus.service' "$calls"
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
        self.assertIn('com.omacast.guard.policy"', recipe)
        self.assertIn("PYSTRAY_BACKEND=dummy PYTHONPATH=src", recipe)
        depends = recipe.split("depends=(", 1)[1].split(")", 1)[0]
        for dependency in ("ffmpeg", "networkmanager", "wpa_supplicant", "iw", "libpulse", "polkit", "systemd", "iproute2"):
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
        self.assertEqual(len(series), 23)
        expected_numbers = (
            [f"{number:04d}" for number in range(1, 7)]
            + [f"{number:04d}" for number in range(9, 23)]
            + ["0027", "0028", "0029"]
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
        self.assertIn('build_root="/tmp/omacast-release-build-${UID}"', builder)
        self.assertIn('flock -n 9', builder)
        self.assertIn('[[ "$(stat -c %u "$build_root")" == "$UID" ]]', builder)

    def test_artifact_audit_is_no_root_and_checks_the_runtime_contract(self) -> None:
        audit = (ROOT / "scripts" / "audit-release-artifact").read_text(encoding="utf-8")
        self.assertIn("omacast-artifact-audit.", audit)
        self.assertIn("omarchy-cast-guard-version", audit)
        self.assertIn("apiRevision", audit)
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
        self.assertNotIn("--wfd-portal-source", audit)
        self.assertIn('pacman -Qp "$package"', audit)
        self.assertNotIn("pacman -Qkp", audit)
        self.assertIn('pkgver = $expected_version', audit)
        self.assertNotIn("sudo", audit)
        self.assertNotIn("pacman -U", audit)

    def test_package_lifecycle_is_disposable_and_no_root(self) -> None:
        lifecycle = (ROOT / "scripts" / "test-package-lifecycle").read_text(encoding="utf-8")
        self.assertIn("omacast-package-lifecycle.", lifecycle)
        self.assertIn("fakeroot pacman", lifecycle)
        self.assertIn("-Qkk", lifecycle)
        self.assertIn("-Rdd", lifecycle)
        self.assertIn("0 altered files", lifecycle)
        self.assertIn("com.omacast.guard.policy", lifecycle)
        self.assertNotIn("sudo", lifecycle)

    def test_release_workflow_pins_actions_and_attests_tagged_packages(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
        action_refs = re.findall(r"uses:\s+[^@\s]+@([^\s#]+)", workflow)
        self.assertGreaterEqual(len(action_refs), 3)
        self.assertTrue(all(re.fullmatch(r"[0-9a-f]{40}", ref) for ref in action_refs))
        self.assertIn("actions/attest-build-provenance@", workflow)
        self.assertIn("scripts/audit-release-artifact", workflow)
        self.assertIn("scripts/test-package-lifecycle", workflow)
        self.assertIn("scripts/lint-shell", workflow)
        self.assertIn("shellcheck", workflow)
        self.assertLess(workflow.index("scripts/lint-shell"), workflow.index("scripts/build-release-artifact"))
        self.assertLess(workflow.index("scripts/audit-release-artifact"), workflow.index("actions/attest-build-provenance@"))
        self.assertLess(workflow.index("scripts/test-package-lifecycle"), workflow.index("actions/attest-build-provenance@"))
        self.assertIn('[[ "$GITHUB_REF_NAME" == "v$version" ]]', workflow)
        self.assertIn('[[ "$(cat dist/SOURCE-COMMIT.txt)" == "$GITHUB_SHA" ]]', workflow)
        for dependency in ("ffmpeg", "iproute2", "iw", "libpulse", "networkmanager", "polkit", "systemd", "wpa_supplicant"):
            self.assertIn(dependency, workflow)

    def test_shell_lint_covers_production_surfaces_without_rewriting_lab_evidence(self) -> None:
        lint = (ROOT / "scripts" / "lint-shell").read_text(encoding="utf-8")
        for path in ("omarchy-cast-guard", "omarchy-cast-guard-recover", "audit-release-artifact", "bootstrap-fluxcast", "build-release-artifact", "test-package-lifecycle", "validate-plugin", "PKGBUILD"):
            self.assertIn(path, lint)
        self.assertNotIn("scripts/lab/", lint)
