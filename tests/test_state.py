from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from omarchy_cast.state import SessionLock, StateError, idle_state, read_state, session_lock_is_held, transition, write_state


class StateTest(unittest.TestCase):
    def test_idle_to_discovery_requires_a_session_id(self) -> None:
        with self.assertRaises(StateError):
            transition(idle_state(), "discovering")
        state = transition(idle_state(), "discovering", sessionId="session-1")
        self.assertEqual(state["phase"], "discovering")

    def test_rejects_illegal_transition(self) -> None:
        with self.assertRaises(StateError):
            transition(idle_state(), "streaming", sessionId="session-1")

    def test_checking_can_stop_before_discovery(self) -> None:
        checking = transition(idle_state(), "checking", sessionId="session-1")
        self.assertEqual(transition(checking, "stopping")["phase"], "stopping")

    def test_idle_transition_discards_prior_session_metadata(self) -> None:
        active = transition(idle_state(), "checking", sessionId="session-1", request={"peer": "receiver"})
        self.assertIsInstance(active["startedAt"], str)
        stopped = transition(active, "idle")
        self.assertEqual(set(stopped), {"schemaVersion", "phase", "sessionId", "updatedAt"})

    def test_session_start_time_survives_active_transitions(self) -> None:
        checking = transition(idle_state(), "checking", sessionId="session-1")
        discovering = transition(checking, "discovering")
        self.assertEqual(discovering["startedAt"], checking["startedAt"])

    def test_writes_private_atomic_json_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            environment = {"XDG_RUNTIME_DIR": temp}
            state = transition(idle_state(), "checking", sessionId="session-1")
            path = write_state(state, environment)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(read_state(environment)["sessionId"], "session-1")
            self.assertEqual(json.loads(path.read_text())["phase"], "checking")

    def test_no_runtime_state_is_idle(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            self.assertEqual(read_state({"XDG_RUNTIME_DIR": temp}), idle_state())

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
