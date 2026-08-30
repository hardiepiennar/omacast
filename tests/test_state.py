from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from omarchy_cast.bounds import MAX_STATE_BYTES
from omarchy_cast.state import SessionLock, StateError, idle_state, read_state, session_lock_is_held, state_path, transition, write_state


SESSION_ID = "a" * 32


class StateTest(unittest.TestCase):
    def test_idle_to_discovery_requires_a_session_id(self) -> None:
        with self.assertRaises(StateError):
            transition(idle_state(), "discovering")
        state = transition(idle_state(), "discovering", sessionId=SESSION_ID)
        self.assertEqual(state["phase"], "discovering")

    def test_active_state_requires_controller_issued_session_id(self) -> None:
        for session_id in ("", "session-1", "../outside", "A" * 32, "a" * 31, "a" * 33):
            with self.subTest(session_id=session_id), self.assertRaisesRegex(StateError, "controller-issued"):
                transition(idle_state(), "checking", sessionId=session_id)

    def test_rejects_illegal_transition(self) -> None:
        with self.assertRaises(StateError):
            transition(idle_state(), "streaming", sessionId=SESSION_ID)

    def test_rejects_boolean_schema_revision(self) -> None:
        state = idle_state()
        state["schemaVersion"] = True
        with self.assertRaisesRegex(StateError, "schema"):
            transition(state, "checking", sessionId=SESSION_ID)

    def test_checking_can_stop_before_discovery(self) -> None:
        checking = transition(idle_state(), "checking", sessionId=SESSION_ID)
        self.assertEqual(transition(checking, "stopping")["phase"], "stopping")

    def test_idle_transition_discards_prior_session_metadata(self) -> None:
        active = transition(idle_state(), "checking", sessionId=SESSION_ID, request={"peer": "receiver"})
        self.assertIsInstance(active["startedAt"], str)
        stopped = transition(active, "idle")
        self.assertEqual(set(stopped), {"schemaVersion", "phase", "sessionId", "updatedAt"})

    def test_session_start_time_survives_active_transitions(self) -> None:
        checking = transition(idle_state(), "checking", sessionId=SESSION_ID)
        discovering = transition(checking, "discovering")
        self.assertEqual(discovering["startedAt"], checking["startedAt"])

    def test_writes_private_atomic_json_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            environment = {"XDG_RUNTIME_DIR": temp}
            state = transition(idle_state(), "checking", sessionId=SESSION_ID)
            path = write_state(state, environment)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(read_state(environment)["sessionId"], SESSION_ID)
            self.assertEqual(json.loads(path.read_text())["phase"], "checking")

    def test_state_write_rejects_a_symlinked_runtime_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            environment = {"XDG_RUNTIME_DIR": temp}
            target = Path(temp) / "unrelated"
            target.mkdir(mode=0o700)
            preserved = target / "state.json"
            preserved.write_text("preserve", encoding="utf-8")
            (Path(temp) / "omarchy-cast").symlink_to(target, target_is_directory=True)
            state = transition(idle_state(), "checking", sessionId=SESSION_ID)

            with self.assertRaisesRegex(StateError, "unavailable or unsafe"):
                write_state(state, environment)

            self.assertEqual(preserved.read_text(encoding="utf-8"), "preserve")

    def test_state_write_replaces_links_without_changing_their_targets(self) -> None:
        for link_kind in ("symlink", "hardlink"):
            with self.subTest(link_kind=link_kind), tempfile.TemporaryDirectory() as temp:
                environment = {"XDG_RUNTIME_DIR": temp}
                directory = Path(temp) / "omarchy-cast"
                directory.mkdir(mode=0o700)
                target = Path(temp) / "unrelated-state"
                target.write_text("preserve", encoding="utf-8")
                target.chmod(0o600)
                state_file = directory / "state.json"
                if link_kind == "symlink":
                    state_file.symlink_to(target)
                else:
                    os.link(target, state_file)

                write_state(transition(idle_state(), "checking", sessionId=SESSION_ID), environment)

                self.assertEqual(target.read_text(encoding="utf-8"), "preserve")
                self.assertEqual(read_state(environment)["sessionId"], SESSION_ID)

    def test_state_write_stays_on_the_pinned_directory_after_path_replacement(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            environment = {"XDG_RUNTIME_DIR": temp}
            original = Path(temp) / "omarchy-cast"
            original.mkdir(mode=0o700)
            detached = Path(temp) / "detached-runtime"
            replacement = Path(temp) / "replacement"
            replacement.mkdir(mode=0o700)

            class ReplacingToken:
                @property
                def hex(self) -> str:
                    original.rename(detached)
                    original.symlink_to(replacement, target_is_directory=True)
                    return "b" * 32

            state = transition(idle_state(), "checking", sessionId=SESSION_ID)
            with patch("omarchy_cast.state.uuid4", return_value=ReplacingToken()):
                write_state(state, environment)

            self.assertFalse((replacement / "state.json").exists())
            self.assertEqual(json.loads((detached / "state.json").read_text())["sessionId"], SESSION_ID)

    def test_no_runtime_state_is_idle(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            self.assertEqual(read_state({"XDG_RUNTIME_DIR": temp}), idle_state())

    def test_oversized_runtime_state_is_rejected_before_json_parsing(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            environment = {"XDG_RUNTIME_DIR": temp}
            path = state_path(environment)
            path.parent.mkdir(mode=0o700)
            path.write_bytes(b"{" + b" " * MAX_STATE_BYTES + b"}")
            path.chmod(0o600)
            with self.assertRaisesRegex(StateError, "exceeds"):
                read_state(environment)

    def test_runtime_state_rejects_non_controller_session_id(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            environment = {"XDG_RUNTIME_DIR": temp}
            path = state_path(environment)
            path.parent.mkdir(mode=0o700)
            path.write_text(
                json.dumps({
                    "schemaVersion": 1,
                    "phase": "error",
                    "sessionId": "../../outside",
                    "updatedAt": None,
                }),
                encoding="utf-8",
            )
            path.chmod(0o600)
            with self.assertRaisesRegex(StateError, "controller-issued"):
                read_state(environment)

    def test_runtime_state_refuses_fifo_without_blocking_on_open(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            environment = {"XDG_RUNTIME_DIR": temp}
            path = state_path(environment)
            path.parent.mkdir(mode=0o700)
            os.mkfifo(path, mode=0o600)
            probe = (
                "import sys\n"
                "from omarchy_cast.state import StateError, read_state\n"
                "try:\n"
                "    read_state({'XDG_RUNTIME_DIR': sys.argv[1]})\n"
                "except StateError as error:\n"
                "    print(error)\n"
                "else:\n"
                "    raise SystemExit('FIFO was accepted as runtime state')\n"
            )
            result = subprocess.run(
                (sys.executable, "-c", probe, temp),
                check=False,
                capture_output=True,
                text=True,
                timeout=2,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("not a regular file", result.stdout)

    def test_runtime_state_refuses_symlinks_and_excessive_shape(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            environment = {"XDG_RUNTIME_DIR": temp}
            path = state_path(environment)
            path.parent.mkdir(mode=0o700)
            target = Path(temp) / "target.json"
            target.write_text(json.dumps(idle_state()), encoding="utf-8")
            path.symlink_to(target)
            with self.assertRaisesRegex(StateError, "cannot read"):
                read_state(environment)
        nested: object = "value"
        for _ in range(14):
            nested = {"next": nested}
        with self.assertRaisesRegex(StateError, "nested too deeply"):
            transition(idle_state(), "checking", sessionId=SESSION_ID, request=nested)

    def test_runtime_state_rejects_recursively_deep_json(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            environment = {"XDG_RUNTIME_DIR": temp}
            path = state_path(environment)
            path.parent.mkdir(mode=0o700)
            path.write_text("[" * 2_000 + "0" + "]" * 2_000, encoding="ascii")
            path.chmod(0o600)
            with self.assertRaises(StateError):
                read_state(environment)

    def test_session_lock_allows_exactly_one_owner(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            environment = {"XDG_RUNTIME_DIR": temp}
            first = SessionLock(environment)
            second = SessionLock(environment)
            first.acquire()
            self.assertTrue(first.acquired)
            self.assertTrue(session_lock_is_held(environment))
            with self.assertRaises(StateError):
                second.acquire()
            first.release()
            self.assertFalse(session_lock_is_held(environment))
            second.acquire()
            self.assertTrue(second.acquired)
            second.release()

    def test_session_lock_rejects_symlinks_without_changing_the_target(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            environment = {"XDG_RUNTIME_DIR": temp}
            directory = Path(temp) / "omarchy-cast"
            directory.mkdir(mode=0o700)
            target = Path(temp) / "unrelated-user-file"
            target.write_text("preserve", encoding="utf-8")
            target.chmod(0o644)
            (directory / "session.lock").symlink_to(target)

            with self.assertRaisesRegex(StateError, "unavailable or unsafe"):
                SessionLock(environment).acquire()

            self.assertEqual(target.read_text(encoding="utf-8"), "preserve")
            self.assertEqual(target.stat().st_mode & 0o777, 0o644)
            self.assertFalse(session_lock_is_held(environment))

    def test_session_lock_rejects_a_symlinked_runtime_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            environment = {"XDG_RUNTIME_DIR": temp}
            target_directory = Path(temp) / "unrelated-directory"
            target_directory.mkdir(mode=0o700)
            target = target_directory / "session.lock"
            target.write_text("preserve", encoding="utf-8")
            target.chmod(0o644)
            (Path(temp) / "omarchy-cast").symlink_to(target_directory, target_is_directory=True)

            with self.assertRaisesRegex(StateError, "directory is unavailable or unsafe"):
                SessionLock(environment).acquire()

            self.assertEqual(target.read_text(encoding="utf-8"), "preserve")
            self.assertEqual(target.stat().st_mode & 0o777, 0o644)
            self.assertFalse(session_lock_is_held(environment))

    def test_session_lock_rejects_fifo_without_blocking(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp) / "omarchy-cast"
            directory.mkdir(mode=0o700)
            os.mkfifo(directory / "session.lock", mode=0o600)
            probe = (
                "import sys\n"
                "from omarchy_cast.state import SessionLock, StateError, session_lock_is_held\n"
                "environment = {'XDG_RUNTIME_DIR': sys.argv[1]}\n"
                "try:\n"
                "    SessionLock(environment).acquire()\n"
                "except StateError as error:\n"
                "    print(error)\n"
                "else:\n"
                "    raise SystemExit('FIFO was accepted as the session lock')\n"
                "assert not session_lock_is_held(environment)\n"
            )
            result = subprocess.run(
                (sys.executable, "-c", probe, temp),
                check=False,
                capture_output=True,
                text=True,
                timeout=2,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("not a regular file", result.stdout)

    def test_session_lock_rejects_hardlinks_without_changing_the_target(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            environment = {"XDG_RUNTIME_DIR": temp}
            directory = Path(temp) / "omarchy-cast"
            directory.mkdir(mode=0o700)
            target = Path(temp) / "unrelated-user-file"
            target.write_text("preserve", encoding="utf-8")
            target.chmod(0o644)
            os.link(target, directory / "session.lock")

            with self.assertRaisesRegex(StateError, "unsafe link count"):
                SessionLock(environment).acquire()

            self.assertEqual(target.read_text(encoding="utf-8"), "preserve")
            self.assertEqual(target.stat().st_mode & 0o777, 0o644)
            self.assertFalse(session_lock_is_held(environment))

    def test_session_lock_rejects_public_mode_without_repairing_it(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            environment = {"XDG_RUNTIME_DIR": temp}
            directory = Path(temp) / "omarchy-cast"
            directory.mkdir(mode=0o700)
            lock = directory / "session.lock"
            lock.write_text("preserve", encoding="utf-8")
            lock.chmod(0o644)

            with self.assertRaisesRegex(StateError, "permissions are unsafe"):
                SessionLock(environment).acquire()

            self.assertEqual(lock.read_text(encoding="utf-8"), "preserve")
            self.assertEqual(lock.stat().st_mode & 0o777, 0o644)
            self.assertFalse(session_lock_is_held(environment))
