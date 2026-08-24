from __future__ import annotations

import sys
import unittest

from omarchy_cast.bounds import MAX_COMMAND_OUTPUT_BYTES
from omarchy_cast.command import run_command


class CommandTest(unittest.TestCase):
    def test_captures_normal_stdout_and_stderr(self) -> None:
        result = run_command((sys.executable, "-c", "import sys; print('ready'); print('note', file=sys.stderr)"))
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "ready\n")
        self.assertEqual(result.stderr, "note\n")

    def test_rejects_oversized_stdout_without_retaining_it(self) -> None:
        result = run_command((sys.executable, "-c", f"import sys; sys.stdout.write('x' * {MAX_COMMAND_OUTPUT_BYTES + 1})"))
        self.assertEqual(result.returncode, 125)
        self.assertEqual(result.stdout, "")
        self.assertIn("output exceeded", result.stderr)

    def test_rejects_oversized_stderr_without_deadlocking(self) -> None:
        result = run_command((sys.executable, "-c", f"import sys; sys.stderr.write('x' * {MAX_COMMAND_OUTPUT_BYTES + 1})"))
        self.assertEqual(result.returncode, 125)
        self.assertEqual(result.stdout, "")
        self.assertIn("output exceeded", result.stderr)

    def test_timeout_discards_partial_output(self) -> None:
        result = run_command((sys.executable, "-c", "import time; print('partial', flush=True); time.sleep(2)"), timeout=0.05)
        self.assertEqual(result.returncode, 124)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "command timed out")
