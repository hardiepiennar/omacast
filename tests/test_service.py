from __future__ import annotations

import tempfile
from pathlib import Path
import unittest

from omarchy_cast.command import CommandResult
from omarchy_cast.service import INHIBIT_REASON, ServiceError, UNIT_NAME, session_service_command, start_session_service, stop_pending_session_service


class ServiceTest(unittest.TestCase):
    def test_session_runs_in_one_collectable_user_service(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            launcher = Path(temp) / "omacast"
            launcher.write_text("#!/bin/sh\n", encoding="utf-8")
            command = session_service_command(
                executable=str(launcher),
                peer="AA:BB:CC:DD:EE:FF",
                mode="mirror",
                profile="safe",
                duration=300,
            )
        self.assertEqual(command[:5], ("systemd-run", "--user", "--quiet", "--collect", f"--unit={UNIT_NAME}"))
        self.assertIn("--property=KillMode=mixed", command)
        inhibitor = command.index("systemd-inhibit")
        self.assertEqual(command[inhibitor:inhibitor + 5], (
            "systemd-inhibit", "--what=idle:sleep", "--who=Omacast",
            f"--why={INHIBIT_REASON}", "--mode=block",
        ))
        self.assertEqual(command[-9:], ("connect", "--peer", "AA:BB:CC:DD:EE:FF", "--mode", "mirror", "--profile", "safe", "--duration", "300"))
        self.assertNotIn("sh", command)

    def test_start_reports_only_after_systemd_accepts_the_unit(self) -> None:
        calls: list[tuple[str, ...]] = []

        def runner(command: tuple[str, ...], **kwargs: object) -> CommandResult:
            calls.append(command)
            self.assertEqual(kwargs["timeout"], 10)
            self.assertEqual(set(kwargs), {"timeout"})
            return CommandResult(command, 0, "", "")

        with tempfile.TemporaryDirectory() as temp:
            launcher = Path(temp) / "omacast"
            launcher.write_text("#!/bin/sh\n", encoding="utf-8")
            payload = start_session_service(
                executable=str(launcher),
                peer="tv-01",
                mode="mirror",
                profile="safe",
                duration=60,
                runner=runner,
            )
        self.assertEqual(len(calls), 1)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["unit"], UNIT_NAME)

    def test_start_surfaces_a_bounded_systemd_failure(self) -> None:
        def runner(command: tuple[str, ...], **kwargs: object) -> CommandResult:
            return CommandResult(command, 1, "", "unit already exists")

        with tempfile.TemporaryDirectory() as temp:
            launcher = Path(temp) / "omacast"
            launcher.write_text("#!/bin/sh\n", encoding="utf-8")
            with self.assertRaisesRegex(ServiceError, "unit already exists"):
                start_session_service(
                    executable=str(launcher),
                    peer="tv-01",
                    mode="mirror",
                    profile="safe",
                    duration=60,
                    runner=runner,
                )

    def test_pending_launch_can_be_cancelled_before_session_state_exists(self) -> None:
        calls: list[tuple[str, ...]] = []

        def runner(command: tuple[str, ...], **kwargs: object) -> CommandResult:
            calls.append(command)
            self.assertEqual(kwargs["timeout"], 25)
            self.assertEqual(set(kwargs), {"timeout"})
            return CommandResult(command, 0, "", "")

        payload = stop_pending_session_service(runner=runner)
        self.assertEqual(calls, [("systemctl", "--user", "stop", UNIT_NAME)])
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["reason"], "launch-cancelled")

    def test_cancel_is_idempotent_after_transient_unit_is_collected(self) -> None:
        def runner(command: tuple[str, ...], **kwargs: object) -> CommandResult:
            return CommandResult(command, 5, "", f"Unit {UNIT_NAME} not loaded.")

        self.assertTrue(stop_pending_session_service(runner=runner)["ok"])


if __name__ == "__main__":
    unittest.main()
