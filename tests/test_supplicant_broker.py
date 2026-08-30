from __future__ import annotations

from importlib.machinery import SourceFileLoader
from importlib.util import module_from_spec, spec_from_loader
import json
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


BROKER_PATH = Path(__file__).resolve().parents[1] / "packaging" / "arch" / "omarchy-cast-supplicant-broker"
LOADER = SourceFileLoader("omacast_supplicant_broker", str(BROKER_PATH))
SPEC = spec_from_loader(LOADER.name, LOADER)
assert SPEC is not None
broker_module = module_from_spec(SPEC)
LOADER.exec_module(broker_module)


class SupplicantBrokerProtocolTest(unittest.TestCase):
    def broker(self):
        return broker_module.SupplicantBroker(
            session="a" * 32,
            uid=1000,
            interface="wlan42",
            peer="00:11:22:33:44:55",
            frequency=2437,
        )

    def test_source_advertises_only_defined_wfd_device_information(self) -> None:
        self.assertEqual(
            broker_module.source_wfd_ies(), bytes.fromhex("00000600101c4400c8")
        )

    def test_privileged_command_output_is_bounded_before_retention(self) -> None:
        command = [
            sys.executable, "-c",
            f"import os; os.write(1, b'x' * ({broker_module.MAX_COMMAND_OUTPUT} + 1))",
        ]
        with self.assertRaisesRegex(broker_module.BrokerError, "exceeded its limit"):
            broker_module.run_bounded(command, timeout=2.0)

    def test_privileged_command_drains_stdout_and_stderr_concurrently(self) -> None:
        command = [
            sys.executable, "-c",
            "import os; os.write(1, b'o' * 60000); os.write(2, b'e' * 60000)",
        ]
        returncode, stdout, stderr = broker_module.run_bounded(command, timeout=2.0)
        self.assertEqual(returncode, 0)
        self.assertEqual((len(stdout), len(stderr)), (60_000, 60_000))

    def test_privileged_command_timeout_kills_the_child(self) -> None:
        command = [sys.executable, "-c", "import time; time.sleep(10)"]
        with self.assertRaises(subprocess.TimeoutExpired):
            broker_module.run_bounded(command, timeout=0.05)

    @staticmethod
    def exchange(broker, payload: object) -> dict[str, object]:
        server, client = socket.socketpair()
        try:
            client.sendall(json.dumps(payload).encode("utf-8") + b"\n")
            broker.handle(server)
            return json.loads(client.recv(4096))
        finally:
            server.close()
            client.close()

    def test_protocol_accepts_only_closed_connect_and_cleanup_operations(self) -> None:
        broker = self.broker()
        with mock.patch.object(broker, "connect", return_value={"group": {"interface": "p2p-wlan42-0", "role": "client"}}):
            response = self.exchange(broker, {"schemaVersion": 1, "op": "connect"})
        self.assertTrue(response["ok"])

        for payload in (
            {"schemaVersion": 1, "op": "disconnect-other"},
            {"schemaVersion": 1, "op": "connect", "path": "/foreign"},
            {"schemaVersion": True, "op": "connect"},
            {"schemaVersion": 2, "op": "connect"},
            ["connect"],
        ):
            with self.subTest(payload=payload):
                server, client = socket.socketpair()
                try:
                    client.sendall(json.dumps(payload).encode("utf-8") + b"\n")
                    with self.assertRaises(broker_module.BrokerError):
                        broker.handle(server)
                finally:
                    server.close()
                    client.close()

    def test_protocol_rejects_oversized_and_incomplete_input(self) -> None:
        for request in (b"x" * (broker_module.MAX_REQUEST + 1), b'{"schemaVersion":1,"op":"connect"}'):
            server, client = socket.socketpair()
            try:
                client.sendall(request)
                client.shutdown(socket.SHUT_WR)
                with self.assertRaisesRegex(broker_module.BrokerError, "oversized or incomplete"):
                    self.broker().handle(server)
            finally:
                server.close()
                client.close()

    def test_connect_is_pinned_to_selected_adapter_peer_and_frequency(self) -> None:
        broker = self.broker()
        with tempfile.TemporaryDirectory() as directory:
            broker.wfd_marker = Path(directory) / "owned"
            with mock.patch.object(broker, "network_armed", return_value=True), mock.patch.object(broker, "resolve_control", return_value="/selected-control"), mock.patch.object(
                broker, "resolve_peer", return_value="/selected-peer"
            ), mock.patch.object(
                broker, "peer_groups", side_effect=(frozenset({"/old"}), frozenset({"/old", "/new"}))
            ), mock.patch.object(
                broker, "group_candidate", return_value={"interface": "p2p-wlan42-0", "role": "client"}
            ), mock.patch.object(
                broker_module, "get_property", return_value="(<@ay []>,)"
            ), mock.patch.object(
                broker_module, "set_property"
            ) as set_property, mock.patch.object(
                broker_module, "call", return_value="()"
            ) as call:
                result = broker.connect()

        self.assertEqual(result, {"group": {"interface": "p2p-wlan42-0", "role": "client"}})
        set_property.assert_called_once_with(
            broker_module.WPA_ROOT,
            broker_module.WPA_DEST,
            "WFDIEs",
            mock.ANY,
        )
        connect = call.call_args.args[0]
        self.assertIn("/selected-control", connect)
        self.assertIn(f"{broker_module.WPA_P2P}.Connect", connect)
        options = connect[-1]
        self.assertIn("objectpath '/selected-peer'", options)
        self.assertIn("'frequency': <int32 2437>", options)
        self.assertIn("'go_intent': <int32 0>", options)
        self.assertNotIn("wlan43", options)

    def test_connect_refuses_preexisting_global_wfd_owner(self) -> None:
        broker = self.broker()
        with mock.patch.object(broker, "network_armed", return_value=True), mock.patch.object(broker, "resolve_control", return_value="/selected-control"), mock.patch.object(
            broker, "resolve_peer", return_value="/selected-peer"
        ), mock.patch.object(
            broker, "peer_groups", return_value=frozenset()
        ), mock.patch.object(
            broker_module, "get_property", return_value="(<[byte 0x01]>,)"
        ), mock.patch.object(broker_module, "set_property") as set_property:
            with self.assertRaisesRegex(broker_module.BrokerError, "another Wi-Fi Display owner"):
                broker.connect()
        set_property.assert_not_called()

    def test_connect_refuses_to_run_before_root_guard_arms_networking(self) -> None:
        broker = self.broker()
        with mock.patch.object(broker, "wait_for_network_arm", return_value=False), mock.patch.object(
            broker, "resolve_control"
        ) as resolve:
            with self.assertRaisesRegex(broker_module.BrokerError, "networking is not active"):
                broker.connect()
        resolve.assert_not_called()

    def test_connect_waits_for_the_root_owned_network_marker(self) -> None:
        broker = self.broker()
        with mock.patch.object(
            broker, "network_armed", side_effect=(False, False, True)
        ) as armed, mock.patch.object(broker.stop, "wait", return_value=False) as wait:
            self.assertTrue(broker.wait_for_network_arm(timeout=10))
        self.assertEqual(armed.call_count, 3)
        self.assertEqual(wait.call_count, 2)

    def test_network_marker_wait_is_bounded_and_stoppable(self) -> None:
        broker = self.broker()
        with mock.patch.object(broker, "network_armed", return_value=False), mock.patch.object(
            broker.stop, "wait", return_value=True
        ) as wait:
            self.assertFalse(broker.wait_for_network_arm(timeout=10))
        wait.assert_called_once()

    def test_resolution_never_falls_back_to_another_adapter_or_peer(self) -> None:
        broker = self.broker()

        def properties(path: str, interface: str, name: str) -> str:
            if path == broker_module.WPA_ROOT and name == "Interfaces":
                return "([objectpath '/wlan1', objectpath '/p2p42', objectpath '/wlan42'],)"
            if name == "Ifname":
                return {"/wlan1": "(<'wlan1'>,)", "/p2p42": "(<'p2p-dev-wlan42'>,)", "/wlan42": "(<'wlan42'>,)"}[path]
            if path == "/p2p42" and name == "Peers":
                return "([objectpath '/foreign-peer', objectpath '/selected-peer'],)"
            if name == "DeviceAddress":
                return {
                    "/foreign-peer": "(<[byte 0xaa, byte 0xbb, byte 0xcc, byte 0xdd, byte 0xee, byte 0xff]>,)",
                    "/selected-peer": "(<[byte 0x00, byte 0x11, byte 0x22, byte 0x33, byte 0x44, byte 0x55]>,)",
                }[path]
            raise AssertionError((path, interface, name))

        with mock.patch.object(broker_module, "get_property", side_effect=properties):
            control = broker.resolve_control()
            peer = broker.resolve_peer(control)
        self.assertEqual(control, "/p2p42")
        self.assertEqual(peer, "/selected-peer")

    def test_group_attribution_rejects_foreign_adapter_and_invalid_role(self) -> None:
        broker = self.broker()

        def properties(path: str, interface: str, name: str) -> str:
            if path == broker_module.WPA_ROOT and name == "Interfaces":
                return "([objectpath '/foreign-iface'],)"
            if path == "/foreign-iface" and name == "Group":
                return "(<objectpath '/group'>,)"
            if path == "/foreign-iface" and name == "Ifname":
                return "(<'p2p-wlan99-0'>,)"
            if path == "/group" and name == "Role":
                return "(<'client'>,)"
            raise AssertionError((path, interface, name))

        with mock.patch.object(broker_module, "get_property", side_effect=properties):
            self.assertIsNone(broker.group_candidate("/group"))

    def test_cleanup_uses_only_recorded_control_and_owned_wfd_marker(self) -> None:
        broker = self.broker()
        broker.control_path = "/selected-control"
        broker.wfd_owned = True
        broker.p2p_mutation_attempted = True
        with tempfile.TemporaryDirectory() as directory:
            broker.wfd_marker = Path(directory) / "owned"
            broker.wfd_marker.touch(mode=0o600)
            with mock.patch.object(broker_module, "call", return_value="()") as call, mock.patch.object(
                broker_module, "get_property", return_value=broker_module.byte_variant(broker_module.source_wfd_ies())
            ), mock.patch.object(
                broker_module, "set_property"
            ) as set_property:
                self.assertTrue(broker.cleanup())

        control_calls = [entry.args[0] for entry in call.call_args_list]
        self.assertEqual(len(control_calls), 2)
        self.assertTrue(all("/selected-control" in arguments for arguments in control_calls))
        set_property.assert_called_once_with(
            broker_module.WPA_ROOT,
            broker_module.WPA_DEST,
            "WFDIEs",
            "<@ay []>",
        )
        self.assertFalse(broker.wfd_owned)

    def test_cleanup_does_not_mutate_p2p_before_connect_or_clear_changed_wfd_state(self) -> None:
        broker = self.broker()
        broker.control_path = "/selected-control"
        broker.wfd_owned = True
        with tempfile.TemporaryDirectory() as directory:
            broker.wfd_marker = Path(directory) / "owned"
            broker.wfd_marker.touch(mode=0o600)
            with mock.patch.object(broker_module, "call") as call, mock.patch.object(
                broker_module, "get_property", return_value="(<[byte 0xff]>,)"
            ), mock.patch.object(broker_module, "set_property") as set_property:
                self.assertFalse(broker.cleanup())

            self.assertTrue(broker.wfd_marker.exists())
        call.assert_not_called()
        set_property.assert_not_called()
        self.assertTrue(broker.wfd_owned)

    def test_one_session_cannot_consume_connect_twice(self) -> None:
        broker = self.broker()
        broker.connect_attempted = True
        with self.assertRaisesRegex(broker_module.BrokerError, "already consumed"):
            broker.connect()


if __name__ == "__main__":
    unittest.main()
