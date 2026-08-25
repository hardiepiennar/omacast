from __future__ import annotations

from pathlib import Path
import json
import re
import subprocess
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
        self.assertEqual(json.loads(version.stdout), {"schemaVersion": 1, "kind": "omarchy-cast-guard-version", "apiRevision": 5})
        source = guard.read_text(encoding="utf-8")
        self.assertIn('"$action" == prepare || "$action" == stop', source)
        self.assertIn('if [[ "$action" == prepare ]]; then prepare; else stop; fi', source)
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
        self.assertIn('lease_fresh', source)
        self.assertNotIn('active_deadline', source)
        self.assertIn('prepare_network_runtime', source)
        self.assertIn('networkd_state_file="$session_root/networkd-units"', source)
        self.assertIn("systemd network runtime directory is unsafe", source)
        self.assertIn('restore_networkd_state', source)
        self.assertIn('runtime_dirs_created=false', source)
        self.assertIn("api_revision=5", source)
        self.assertIn('[[ "${PKEXEC_UID:-}" == "$uid" ]]', source)
        self.assertNotIn("systemd-networkd is already active", source)
        self.assertIn("systemctl reload systemd-networkd.service", source)
        self.assertIn("if systemctl is-active --quiet systemd-networkd.service", source)
        self.assertIn('"$1" == --version', source)
        prepare_body = source.split('prepare() {', 1)[1].split('}', 1)[0]
        self.assertLess(prepare_body.index("trap 'cleanup' EXIT"), prepare_body.index('write_runtime_files'))
        write_body = source.split('write_runtime_files() {', 1)[1].split('\n}', 1)[0]
        self.assertLess(write_body.index('[[ ! -e "$session_root"'), write_body.index('install -d -m700 "$session_root"'))
        recovery_source = recovery.read_text(encoding="utf-8")
        self.assertIn('lease_seconds="$2"', recovery_source)
        self.assertIn('heartbeat_file="$user_root/heartbeat"', recovery_source)
        self.assertIn('networkd_state_file="$root/networkd-units"', recovery_source)
        self.assertIn('telemetry_root="/run/user/$uid/omarchy-cast/telemetry/$session"', recovery_source)
        for live_name in ("current.json", "ffmpeg.progress", "mux-packets.csv", "engine.jsonl", "engine.log"):
            self.assertIn(f'"$telemetry_root/{live_name}"', recovery_source)
        for removed_surface in ("qos.pid", "qos_file", "apply_media_qos", "renice", "/proc/$root_pid"):
            self.assertNotIn(removed_surface, source)
            self.assertNotIn(removed_surface, recovery_source)
        self.assertIn('rmdir --ignore-fail-on-non-empty "$telemetry_root"', recovery_source)
        self.assertIn("systemctl reload systemd-networkd.service", recovery_source)
        self.assertNotIn('systemctl stop systemd-networkd.service systemd-networkd.socket', recovery_source)

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
        self.assertEqual(len(series), 20)
        expected_numbers = [f"{number:04d}" for number in range(1, 7)] + [f"{number:04d}" for number in range(9, 23)]
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
