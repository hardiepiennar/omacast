from __future__ import annotations

from importlib.machinery import SourceFileLoader
from importlib.util import module_from_spec, spec_from_loader
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
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

    def test_privileged_command_timeout_kills_descendants_holding_pipes(self) -> None:
        command = [
            sys.executable, "-c",
            "import subprocess,sys; subprocess.Popen((sys.executable,'-c','import time; time.sleep(30)'))",
        ]
        with self.assertRaises(subprocess.TimeoutExpired):
            broker_module.run_bounded(command, timeout=0.05)

    def test_negotiation_failure_signal_is_exactly_attributed_and_bounded(self) -> None:
        control = "/fi/w1/wpa_supplicant1/Interfaces/0"
        peer = "/fi/w1/wpa_supplicant1/Interfaces/0/Peers/selected"
        signal_line = (
            f"{control}: {broker_module.WPA_P2P}.GONegotiationFailure "
            f"({{'peer_object': <objectpath '{peer}'>, 'status': <10>}},)"
        )
        self.assertEqual(
            broker_module.go_negotiation_failure(
                signal_line, control_path=control, peer_path=peer
            ),
            10,
        )
        self.assertIsNone(
            broker_module.go_negotiation_failure(
                signal_line.replace("/selected'", "/foreign'"),
                control_path=control,
                peer_path=peer,
            )
        )
        with self.assertRaisesRegex(broker_module.BrokerError, "invalid negotiation-failure"):
            broker_module.go_negotiation_failure(
                f"{control}: {broker_module.WPA_P2P}.GONegotiationFailure ({{'status': <10>}},)",
                control_path=control,
                peer_path=peer,
            )
        with self.assertRaisesRegex(broker_module.BrokerError, "invalid negotiation-failure status"):
            broker_module.go_negotiation_failure(
                signal_line.replace("<10>", "<0>"),
                control_path=control,
                peer_path=peer,
            )

    def test_wps_failure_signal_is_control_scoped_and_bounded(self) -> None:
        control = "/fi/w1/wpa_supplicant1/Interfaces/0"
        signal_line = (
            f"{control}: {broker_module.WPA_P2P}.WpsFailed "
            "('fail', {'msg': <int32 18>, 'config_error': <int16 18>})"
        )
        self.assertEqual(
            broker_module.wps_failure(signal_line, control_path=control), (18, 18)
        )
        self.assertIsNone(broker_module.wps_failure("unrelated", control_path=control))
        with self.assertRaisesRegex(broker_module.BrokerError, "invalid WPS-failure"):
            broker_module.wps_failure(signal_line.replace(control, "/foreign"), control_path=control)

    def test_pairing_pin_validation_is_exact_and_checksum_aware(self) -> None:
        self.assertEqual(broker_module.validate_pairing_pin("12345670"), "12345670")
        for value in ("12345671", "1234567", "1234abcd", True, 12345670):
            with self.subTest(value=value), self.assertRaises(broker_module.BrokerError):
                broker_module.validate_pairing_pin(value)

    def test_pin_connect_uses_in_process_dbus_and_never_a_subprocess_argument(self) -> None:
        captured: dict[str, object] = {}

        class Interface:
            def Connect(self, options, *, timeout):
                captured["options"] = options
                captured["timeout"] = timeout

        class Bus:
            def get_object(self, destination, path):
                captured["object"] = (destination, path)
                return object()

        fake_dbus = SimpleNamespace(
            Dictionary=lambda values, signature: dict(values),
            ObjectPath=lambda value: value,
            Boolean=lambda value: value,
            Int32=lambda value: value,
            String=lambda value: value,
            Interface=lambda _object, dbus_interface: Interface(),
            SystemBus=lambda: Bus(),
        )
        with mock.patch.dict(sys.modules, {"dbus": fake_dbus}), mock.patch.object(
            broker_module, "run_bounded"
        ) as subprocess_call:
            broker_module.connect_with_pin("/selected-control", "/selected-peer", "12345670", 2437)
        subprocess_call.assert_not_called()
        self.assertEqual(captured["object"], (broker_module.WPA_DEST, "/selected-control"))
        self.assertEqual(captured["timeout"], 15.0)
        self.assertEqual(captured["options"], {
            "peer": "/selected-peer", "persistent": True, "join": False,
            "authorize_only": False, "go_intent": 0, "wps_method": "keypad",
            "pin": "12345670", "frequency": 2437,
        })

    def test_pin_connect_sanitizes_dbus_failures(self) -> None:
        class Interface:
            def Connect(self, _options, *, timeout):
                raise RuntimeError("secret was 12345670")

        fake_dbus = SimpleNamespace(
            Dictionary=lambda values, signature: dict(values),
            ObjectPath=lambda value: value, Boolean=lambda value: value,
            Int32=lambda value: value, String=lambda value: value,
            Interface=lambda _object, dbus_interface: Interface(),
            SystemBus=lambda: SimpleNamespace(get_object=lambda *_args: object()),
        )
        with mock.patch.dict(sys.modules, {"dbus": fake_dbus}), self.assertRaises(
            broker_module.BrokerError
        ) as caught:
            broker_module.connect_with_pin(
                "/selected-control", "/selected-peer", "12345670", 0
            )
        self.assertNotIn("12345670", str(caught.exception))

    def test_long_lived_monitor_drains_pressure_and_stops_a_child_that_never_exits(self) -> None:
        control = "/fi/w1/wpa_supplicant1/Interfaces/0"
        peer = "/fi/w1/wpa_supplicant1/Interfaces/0/Peers/selected"
        ready = f"Monitoring signals on object {control}\n"
        failure = (
            f"{control}: {broker_module.WPA_P2P}.GONegotiationFailure "
            f"({{'peer_object': <objectpath '{peer}'>, 'status': <10>}},)\n"
        )
        script = (
            "import os,threading,time;"
            f"os.write(1,{ready!r}.encode());"
            "a=threading.Thread(target=os.write,args=(1,(b'x'*79+b'\\n')*900));"
            "b=threading.Thread(target=os.write,args=(2,b'e'*72000));"
            "a.start();b.start();a.join();b.join();"
            f"os.write(1,{failure!r}.encode());"
            "time.sleep(30)"
        )
        process = subprocess.Popen(
            [sys.executable, "-c", script],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        monitor = broker_module.SupplicantSignalMonitor(
            control_path=control, peer_path=peer
        )
        with mock.patch.object(broker_module.subprocess, "Popen", return_value=process):
            monitor.start()
        deadline = time.monotonic() + 2
        while monitor.failure_status() is None and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertEqual(monitor.failure_status(), 10)
        self.assertEqual(len(monitor.stderr), broker_module.MAX_MONITOR_STDERR)
        monitor.close()
        self.assertIsNotNone(process.poll())
        self.assertTrue(all(not thread.is_alive() for thread in monitor.threads))

    def test_monitor_failure_is_closed_when_the_child_exits_after_readiness(self) -> None:
        control = "/fi/w1/wpa_supplicant1/Interfaces/0"
        peer = "/fi/w1/wpa_supplicant1/Interfaces/0/Peers/selected"
        process = subprocess.Popen(
            [
                sys.executable,
                "-c",
                f"print({'Monitoring signals on object ' + control!r},flush=True)",
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        monitor = broker_module.SupplicantSignalMonitor(
            control_path=control, peer_path=peer
        )
        with mock.patch.object(broker_module.subprocess, "Popen", return_value=process):
            monitor.start()
        process.wait(timeout=2)
        with self.assertRaisesRegex(broker_module.BrokerError, "stopped unexpectedly"):
            monitor.failure_status()
        monitor.close()

    def test_supplicant_collections_have_independent_count_limits(self) -> None:
        with self.assertRaisesRegex(broker_module.BrokerError, "too many object paths"):
            broker_module.object_paths(" ".join(f"objectpath '/{index}'" for index in range(broker_module.MAX_OBJECT_PATHS + 1)))
        with self.assertRaisesRegex(broker_module.BrokerError, "too many byte values"):
            broker_module.byte_values(" ".join("0xff" for _ in range(broker_module.MAX_VARIANT_BYTES + 1)))

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

        broker = self.broker()
        with mock.patch.object(broker, "connect", return_value={"group": {"interface": "p2p-wlan42-0", "role": "client"}}) as connect:
            response = self.exchange(
                broker, {"schemaVersion": 1, "op": "connect", "pin": "12345670"}
            )
        self.assertTrue(response["ok"])
        connect.assert_called_once_with("12345670")

        for payload in (
            {"schemaVersion": 1, "op": "disconnect-other"},
            {"schemaVersion": 1, "op": "connect", "path": "/foreign"},
            {"schemaVersion": 1, "op": "cleanup", "pin": "12345670"},
            {"schemaVersion": 1, "op": "connect", "pin": "12345671"},
            {"schemaVersion": 1, "op": "connect", "pin": True},
            {"schemaVersion": True, "op": "connect"},
            {"schemaVersion": 2, "op": "connect"},
            ["connect"],
            {"schemaVersion": 1, "op": [True]},
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

    def test_protocol_rejects_recursively_deep_json(self) -> None:
        payload: object = "connect"
        for _ in range(20):
            payload = [payload]
        server, client = socket.socketpair()
        try:
            client.sendall(json.dumps({"schemaVersion": 1, "op": payload}).encode("utf-8") + b"\n")
            with self.assertRaisesRegex(broker_module.BrokerError, "shape limit"):
                self.broker().handle(server)
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
            ) as call, mock.patch.object(
                broker_module, "SupplicantSignalMonitor"
            ) as monitor:
                monitor.return_value.failure_status.return_value = None
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
        self.assertIn("'persistent': <true>", options)
        self.assertNotIn("'persistent': <false>", options)
        self.assertIn("'frequency': <int32 2437>", options)
        self.assertIn("'go_intent': <int32 0>", options)
        self.assertNotIn("wlan43", options)
        monitor.return_value.start.assert_called_once_with()
        monitor.return_value.close.assert_called_once_with()

    def test_connect_surfaces_selected_receiver_provisioning_rejection(self) -> None:
        broker = self.broker()
        with tempfile.TemporaryDirectory() as directory:
            broker.wfd_marker = Path(directory) / "owned"
            with mock.patch.object(broker, "network_armed", return_value=True), mock.patch.object(
                broker, "resolve_control", return_value="/selected-control"
            ), mock.patch.object(
                broker, "resolve_peer", return_value="/selected-peer"
            ), mock.patch.object(
                broker, "peer_groups", return_value=frozenset()
            ), mock.patch.object(
                broker_module, "get_property", return_value="(<@ay []>,)"
            ), mock.patch.object(
                broker_module, "set_property"
            ), mock.patch.object(
                broker_module, "call", return_value="()"
            ), mock.patch.object(
                broker_module, "SupplicantSignalMonitor"
            ) as monitor:
                monitor.return_value.failure_status.return_value = 10
                with self.assertRaisesRegex(
                    broker_module.BrokerError, "incompatible provisioning method"
                ):
                    broker.connect()
        monitor.return_value.close.assert_called_once_with()

    def test_connect_uses_keypad_only_after_an_explicit_valid_pin(self) -> None:
        broker = self.broker()
        with tempfile.TemporaryDirectory() as directory:
            broker.wfd_marker = Path(directory) / "owned"
            with mock.patch.object(broker, "network_armed", return_value=True), mock.patch.object(
                broker, "resolve_control", return_value="/selected-control"
            ), mock.patch.object(
                broker, "resolve_peer", return_value="/selected-peer"
            ), mock.patch.object(
                broker, "peer_groups", side_effect=(frozenset(), frozenset({"/new"}))
            ), mock.patch.object(
                broker, "group_candidate", return_value={"interface": "p2p-wlan42-0", "role": "client"}
            ), mock.patch.object(
                broker_module, "get_property", return_value="(<@ay []>,)"
            ), mock.patch.object(
                broker_module, "set_property"
            ), mock.patch.object(
                broker_module, "call"
            ) as shell_call, mock.patch.object(
                broker_module, "connect_with_pin"
            ) as pin_call, mock.patch.object(
                broker_module, "SupplicantSignalMonitor"
            ) as monitor:
                monitor.return_value.failure_status.return_value = None
                monitor.return_value.pairing_failure.return_value = None
                result = broker.connect("12345670")
        self.assertEqual(result["group"]["role"], "client")
        pin_call.assert_called_once_with(
            "/selected-control", "/selected-peer", "12345670", 2437
        )
        shell_call.assert_not_called()
        monitor.assert_called_once_with(
            control_path="/selected-control", peer_path="/selected-peer",
            watch_wps=True,
        )

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
        self.assertEqual(
            [arguments[-1] for arguments in control_calls],
            [f"{broker_module.WPA_P2P}.Cancel", f"{broker_module.WPA_P2P}.Disconnect"],
        )
        self.assertTrue(all("PersistentGroup" not in " ".join(arguments) for arguments in control_calls))
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

    def networkmanager_broker(self):
        return broker_module.SupplicantBroker(
            session="a" * 32, uid=1000, interface="wlan42",
            peer="00:11:22:33:44:55", frequency=0,
            backend="networkmanager",
        )

    def test_networkmanager_connect_records_intent_before_mutation(self) -> None:
        broker = self.networkmanager_broker()
        device = "/org/freedesktop/NetworkManager/Devices/42"
        peer = "/org/freedesktop/NetworkManager/WifiP2PPeer/7"
        connection = "/org/freedesktop/NetworkManager/Settings/9"
        active = "/org/freedesktop/NetworkManager/ActiveConnection/11"

        def property_value(_dest: str, _path: str, _interface: str, name: str) -> str:
            return {
                "State": "(<uint32 2>,)",
                "IpInterface": "(<'p2p-wlan42-0'>,)",
                "Ip4Config": "(<objectpath '/org/freedesktop/NetworkManager/IP4Config/3'>,)",
                "AddressData": "(<[{'address': <'192.168.49.1'>, 'prefix': <uint32 24>}]>,)",
            }[name]

        writes: list[tuple[str, str, str, str]] = []
        with mock.patch.object(broker, "wait_for_network_arm", return_value=True), mock.patch.object(
            broker, "resolve_nm_device", return_value=device
        ), mock.patch.object(
            broker, "resolve_nm_peer", return_value=peer
        ), mock.patch.object(
            broker, "_write_nm_record", side_effect=lambda *values: writes.append(values)
        ), mock.patch.object(
            broker, "_nm_owned", return_value=True
        ), mock.patch.object(
            broker_module, "bus_property", side_effect=property_value
        ), mock.patch.object(
            broker_module, "call",
            return_value=f"(objectpath '{connection}', objectpath '{active}', @a{{sv}} {{}})",
        ) as call:
            result = broker.connect()

        self.assertEqual(result, {"group": {"interface": "p2p-wlan42-0", "role": "GO"}})
        self.assertEqual(writes, [
            ("/", "/", device, peer),
            (active, connection, device, peer),
        ])
        mutation = call.call_args.args[0]
        self.assertIn(f"{broker_module.NM_IFACE}.AddAndActivateConnection2", mutation)
        self.assertIn("'method': <'shared'>", mutation[-4])
        self.assertIn("'address': <'192.168.49.1'>", mutation[-4])
        self.assertIn("'persist': <'volatile'>", mutation[-1])

    def test_networkmanager_failed_mutation_retains_recovery_intent(self) -> None:
        broker = self.networkmanager_broker()
        device = "/org/freedesktop/NetworkManager/Devices/42"
        peer = "/org/freedesktop/NetworkManager/WifiP2PPeer/7"
        writes: list[tuple[str, str, str, str]] = []
        with mock.patch.object(broker, "wait_for_network_arm", return_value=True), mock.patch.object(
            broker, "resolve_nm_device", return_value=device
        ), mock.patch.object(
            broker, "resolve_nm_peer", return_value=peer
        ), mock.patch.object(
            broker, "_write_nm_record", side_effect=lambda *values: writes.append(values)
        ), mock.patch.object(
            broker_module, "call", side_effect=broker_module.BrokerError("activation failed")
        ), self.assertRaisesRegex(broker_module.BrokerError, "activation failed"):
            broker.connect()
        self.assertEqual(writes, [("/", "/", device, peer)])

    def test_networkmanager_recovery_resolves_intent_and_deactivates_only_owned_path(self) -> None:
        broker = self.networkmanager_broker()
        device = "/org/freedesktop/NetworkManager/Devices/42"
        peer = "/org/freedesktop/NetworkManager/WifiP2PPeer/7"
        connection = "/org/freedesktop/NetworkManager/Settings/9"
        active = "/org/freedesktop/NetworkManager/ActiveConnection/11"
        with mock.patch.object(
            broker, "_read_nm_record", return_value=("/", "/", device, peer)
        ), mock.patch.object(
            broker, "_find_nm_owned_active", return_value=(active, connection)
        ), mock.patch.object(
            broker, "_write_nm_record"
        ) as write_record, mock.patch.object(
            broker, "_nm_owned", side_effect=(True, False)
        ), mock.patch.object(
            broker, "_remove_nm_record"
        ) as remove_record, mock.patch.object(
            broker_module, "call", return_value="()"
        ) as call:
            self.assertTrue(broker.cleanup_networkmanager())
        write_record.assert_called_once_with(active, connection, device, peer)
        self.assertIn(f"{broker_module.NM_IFACE}.DeactivateConnection", call.call_args.args[0])
        self.assertIn(active, call.call_args.args[0])
        remove_record.assert_called_once_with()

    def test_networkmanager_cleanup_converges_with_independent_recovery(self) -> None:
        broker = self.networkmanager_broker()
        device = "/org/freedesktop/NetworkManager/Devices/42"
        peer = "/org/freedesktop/NetworkManager/WifiP2PPeer/7"
        connection = "/org/freedesktop/NetworkManager/Settings/9"
        active = "/org/freedesktop/NetworkManager/ActiveConnection/11"
        with mock.patch.object(
            broker, "_read_nm_record", return_value=(active, connection, device, peer)
        ), mock.patch.object(
            broker, "_nm_owned", side_effect=(True, False, False)
        ), mock.patch.object(
            broker, "_remove_nm_record"
        ) as remove_record, mock.patch.object(
            broker_module, "call", side_effect=broker_module.BrokerError("already deactivated")
        ):
            self.assertTrue(broker.cleanup_networkmanager())
        remove_record.assert_called_once_with()

    def test_networkmanager_intent_resolution_rejects_ambiguous_or_changed_identity(self) -> None:
        broker = self.networkmanager_broker()
        device = "/org/freedesktop/NetworkManager/Devices/42"
        peer = "/org/freedesktop/NetworkManager/WifiP2PPeer/7"
        actives = (
            "/org/freedesktop/NetworkManager/ActiveConnection/11",
            "/org/freedesktop/NetworkManager/ActiveConnection/12",
        )

        def property_value(_dest: str, path: str, _interface: str, name: str) -> str:
            if name == "ActiveConnections":
                return "([" + ", ".join(f"objectpath '{item}'" for item in actives) + "],)"
            if name == "Id":
                return f"(<'Omacast {broker.session}'>,)"
            if name == "Connection":
                return f"(<objectpath '/org/freedesktop/NetworkManager/Settings/{11 if path.endswith('11') else 12}'>,)"
            if name == "Devices":
                return f"([objectpath '{device}'],)"
            if name == "SpecificObject":
                return f"(<objectpath '{peer}'>,)"
            raise AssertionError((path, name))

        with mock.patch.object(
            broker_module, "bus_property", side_effect=property_value
        ), self.assertRaisesRegex(broker_module.BrokerError, "ambiguous"):
            broker._find_nm_owned_active(device, peer)

        def changed_property(_dest: str, path: str, _interface: str, name: str) -> str:
            if name == "ActiveConnections":
                return f"([objectpath '{actives[0]}'],)"
            if name == "Id":
                return f"(<'Omacast {broker.session}'>,)"
            if name == "Connection":
                return "(<objectpath '/org/freedesktop/NetworkManager/Settings/11'>,)"
            if name == "Devices":
                return f"([objectpath '{device}'],)"
            if name == "SpecificObject":
                return "(<objectpath '/org/freedesktop/NetworkManager/WifiP2PPeer/99'>,)"
            raise AssertionError((path, name))

        with mock.patch.object(
            broker_module, "bus_property", side_effect=changed_property
        ), self.assertRaisesRegex(broker_module.BrokerError, "peer ownership changed"):
            broker._find_nm_owned_active(device, peer)

    def test_networkmanager_record_paths_are_closed_and_special_records_fail_without_blocking(self) -> None:
        broker = self.networkmanager_broker()
        valid = (
            "/", "/", "/org/freedesktop/NetworkManager/Devices/42",
            "/org/freedesktop/NetworkManager/WifiP2PPeer/7",
        )
        self.assertIn(b"active=/\n", broker._nm_record_payload(*valid))
        for values in (
            ("relative", *valid[1:]),
            (valid[0], "/tmp/foreign", *valid[2:]),
            (*valid[:2], "/foreign/device", valid[3]),
            (*valid[:3], "/foreign/peer"),
        ):
            with self.subTest(values=values), self.assertRaises(broker_module.BrokerError):
                broker._nm_record_payload(*values)

        with tempfile.TemporaryDirectory() as directory:
            record = Path(directory) / "networkmanager-active"
            os.mkfifo(record, mode=0o600)
            descriptor = os.open(directory, os.O_PATH | os.O_DIRECTORY)
            with mock.patch.object(broker, "_session_descriptor", return_value=descriptor):
                started = time.monotonic()
                with self.assertRaises(broker_module.BrokerError):
                    broker._read_nm_record()
                self.assertLess(time.monotonic() - started, 1)

    def test_networkmanager_mode_refuses_pin_pairing(self) -> None:
        with self.assertRaisesRegex(broker_module.BrokerError, "not supported"):
            self.networkmanager_broker().connect("12345670")


if __name__ == "__main__":
    unittest.main()
