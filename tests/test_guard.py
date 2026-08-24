from __future__ import annotations

import unittest

from omarchy_cast.guard import GuardError, GuardRequest, HELPER_PATH, prepare_command, stop_command, validate_helper_result


class GuardContractTest(unittest.TestCase):
    def request(self, **changes: object) -> GuardRequest:
        values: dict[str, object] = {"schema_version": 1, "session_id": "a" * 32, "uid": 1000, "interface": "wlan42", "duration_seconds": 300}
        values.update(changes)
        return GuardRequest(**values)  # type: ignore[arg-type]

    def test_prepare_command_is_fixed_and_shell_free(self) -> None:
        command = prepare_command(self.request())
        self.assertEqual(command[:2], (HELPER_PATH, "prepare"))
        self.assertNotIn(";", " ".join(command))
        self.assertEqual(stop_command(self.request())[1], "stop")

    def test_rejects_untrusted_arguments_or_helper_path(self) -> None:
        for request in (self.request(session_id="receiver-name"), self.request(interface="wlan0;id"), self.request(duration_seconds=30), self.request(uid=0)):
            with self.assertRaises(GuardError):
                prepare_command(request)
        with self.assertRaises(GuardError):
            prepare_command(self.request(), helper_path="/tmp/helper")

    def test_status_contract_rejects_unexpected_shapes(self) -> None:
        result = validate_helper_result({"schemaVersion": 1, "kind": "omarchy-cast-guard-status", "ok": True, "phase": "ready", "sessionId": "b" * 32})
        self.assertTrue(result["ok"])
        for payload in ({}, {"schemaVersion": 1, "kind": "other", "ok": True, "phase": "ready"}, {"schemaVersion": 1, "kind": "omarchy-cast-guard-status", "ok": "yes", "phase": "ready"}):
            with self.assertRaises(GuardError):
                validate_helper_result(payload)

    def test_status_rejects_non_runtime_trigger_path(self) -> None:
        with self.assertRaises(GuardError):
            validate_helper_result({"schemaVersion": 1, "kind": "omarchy-cast-guard-status", "ok": True, "phase": "ready", "sessionId": "b" * 32, "triggerPath": "/tmp/trigger"})
