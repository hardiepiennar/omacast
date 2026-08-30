from __future__ import annotations

import json
import unittest

from omarchy_cast.command import CommandResult
from omarchy_cast.guard import GuardError, GuardRequest, HELPER_PATH, orphan_parent_interfaces, prepare_command, reclaim_command, reclaim_orphan_interfaces, validate_helper_result, validate_reclaim_result


class GuardContractTest(unittest.TestCase):
    def request(self, **changes: object) -> GuardRequest:
        values: dict[str, object] = {"schema_version": 1, "session_id": "a" * 32, "uid": 1000, "interface": "wlan42", "peer": "00:11:22:33:44:55", "frequency_mhz": 2437, "duration_seconds": 300}
        values.update(changes)
        return GuardRequest(**values)  # type: ignore[arg-type]

    def test_prepare_command_is_fixed_and_shell_free(self) -> None:
        command = prepare_command(self.request())
        self.assertEqual(command[:2], (HELPER_PATH, "prepare"))
        self.assertEqual(command[command.index("--peer") + 1], "00:11:22:33:44:55")
        self.assertEqual(command[command.index("--frequency") + 1], "2437")
        self.assertNotIn(";", " ".join(command))

    def test_rejects_untrusted_arguments_or_helper_path(self) -> None:
        for request in (self.request(schema_version=True), self.request(session_id="receiver-name"), self.request(interface="wlan0;id"), self.request(peer="receiver"), self.request(frequency_mhz=9999), self.request(duration_seconds=30), self.request(uid=0)):
            with self.assertRaises(GuardError):
                prepare_command(request)
        with self.assertRaises(GuardError):
            prepare_command(self.request(), helper_path="/tmp/helper")

    def test_reclaim_command_is_fixed_and_validated(self) -> None:
        self.assertEqual(reclaim_command(uid=1000, interface="wlan42"), (
            HELPER_PATH, "reclaim", "--schema-version", "1",
            "--uid", "1000", "--interface", "wlan42",
        ))
        for values in ({"uid": 0, "interface": "wlan42"}, {"uid": 1000, "interface": "wlan0;id"}):
            with self.assertRaises(GuardError):
                reclaim_command(**values)  # type: ignore[arg-type]

    def test_orphan_probe_and_reclaim_status_are_bounded(self) -> None:
        calls: list[tuple[str, ...]] = []

        def iw_runner(args, *, timeout=5.0):
            calls.append(tuple(args))
            return CommandResult(
                tuple(args), 0,
                "phy#0\n  Interface wlan42\n  Interface p2p-wlan43-0\n  Interface p2p-wlan42-0\n",
                "",
            )

        self.assertEqual(
            orphan_parent_interfaces(("wlan42", "wlan43", "wlan44", "wlan43"), runner=iw_runner),
            ("wlan42", "wlan43"),
        )
        self.assertEqual(calls, [("iw", "dev")])
        self.assertEqual(orphan_parent_interfaces((), runner=iw_runner), ())
        self.assertEqual(calls, [("iw", "dev")])
        for interfaces in (("wlan0;id",), tuple(f"wlan{index}" for index in range(33))):
            with self.subTest(interfaces=interfaces):
                with self.assertRaises(GuardError):
                    orphan_parent_interfaces(interfaces, runner=iw_runner)

        def reclaim_runner(args, *, timeout=5.0):
            self.assertEqual(args[:3], ("pkexec", HELPER_PATH, "reclaim"))
            return CommandResult(tuple(args), 0, json.dumps({
                "schemaVersion": 1, "kind": "omarchy-cast-guard-reclaim-status",
                "ok": True, "reclaimed": 1,
            }), "")

        self.assertEqual(reclaim_orphan_interfaces("wlan42", uid=1000, runner=reclaim_runner)["reclaimed"], 1)
        with self.assertRaises(GuardError):
            reclaim_orphan_interfaces(
                "wlan42", uid=1000,
                runner=lambda args, timeout=5.0: CommandResult(tuple(args), 0, "[" * 2_000 + "0" + "]" * 2_000, ""),
            )
        for payload in ({}, {"schemaVersion": True, "kind": "omarchy-cast-guard-reclaim-status", "ok": True, "reclaimed": 1}, {"schemaVersion": 1, "kind": "omarchy-cast-guard-reclaim-status", "ok": True, "reclaimed": 33}):
            with self.assertRaises(GuardError):
                validate_reclaim_result(payload)

    def test_status_contract_rejects_unexpected_shapes(self) -> None:
        session_id = "b" * 32
        ready = {
            "schemaVersion": 1, "kind": "omarchy-cast-guard-status", "ok": True,
            "phase": "ready", "sessionId": session_id, "error": None,
            "triggerPath": f"/run/omarchy-cast/{session_id}/user/trigger",
            "brokerPath": f"/run/omarchy-cast/{session_id}/supplicant.sock",
        }
        result = validate_helper_result(ready)
        self.assertTrue(result["ok"])
        for payload in (
            {},
            {**ready, "schemaVersion": True},
            {**ready, "kind": "other"},
            {**ready, "ok": "yes"},
            {key: value for key, value in ready.items() if key != "brokerPath"},
            {**ready, "unexpected": []},
            {**ready, "ok": False},
            {**ready, "error": "failure"},
            {"schemaVersion": 1, "kind": "omarchy-cast-guard-status", "ok": True, "phase": "active", "sessionId": session_id, "error": None, "triggerPath": ready["triggerPath"]},
            {"schemaVersion": 1, "kind": "omarchy-cast-guard-status", "ok": True, "phase": "error", "sessionId": session_id, "error": "failure"},
        ):
            with self.assertRaises(GuardError):
                validate_helper_result(payload)
        error = validate_helper_result({
            "schemaVersion": 1, "kind": "omarchy-cast-guard-status", "ok": False,
            "phase": "error", "sessionId": session_id, "error": "cleanup incomplete",
        })
        self.assertFalse(error["ok"])

    def test_status_rejects_non_runtime_trigger_path(self) -> None:
        session_id = "b" * 32
        status = {"schemaVersion": 1, "kind": "omarchy-cast-guard-status", "ok": True, "phase": "ready", "sessionId": session_id, "error": None, "triggerPath": f"/run/omarchy-cast/{session_id}/user/trigger", "brokerPath": f"/run/omarchy-cast/{session_id}/supplicant.sock"}
        result = validate_helper_result(status)
        self.assertEqual(result["triggerPath"], f"/run/omarchy-cast/{session_id}/user/trigger")
        with self.assertRaises(GuardError):
            validate_helper_result({**status, "triggerPath": "/tmp/trigger"})

    def test_status_rejects_non_session_broker_path(self) -> None:
        session_id = "b" * 32
        broker = f"/run/omarchy-cast/{session_id}/supplicant.sock"
        status = {"schemaVersion": 1, "kind": "omarchy-cast-guard-status", "ok": True, "phase": "ready", "sessionId": session_id, "error": None, "triggerPath": f"/run/omarchy-cast/{session_id}/user/trigger", "brokerPath": broker}
        result = validate_helper_result(status)
        self.assertEqual(result["brokerPath"], broker)
        with self.assertRaises(GuardError):
            validate_helper_result({**status, "brokerPath": "/tmp/broker"})
