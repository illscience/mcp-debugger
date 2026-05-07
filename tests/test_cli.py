from __future__ import annotations

import json
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

from mcp_debugger.cli import main


def call_cli(args: list[str]) -> tuple[int, str]:
    stdout = StringIO()
    with redirect_stdout(stdout):
        code = main(args)
    return code, stdout.getvalue()


class CLITests(unittest.TestCase):
    def test_demo_project_writes_claude_memory_and_bug(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            code, output = call_cli(["demo-project", directory, "--target", "claude"])

            self.assertEqual(code, 0)
            root = Path(directory)
            self.assertTrue((root / "CLAUDE.md").exists())
            self.assertTrue((root / "buggy_invoice.py").exists())
            self.assertIn("debug_python_repro", (root / "CLAUDE.md").read_text())
            self.assertIn("actual_total", (root / "buggy_invoice.py").read_text())
            payload = json.loads(output)
            self.assertEqual(payload["created"], ["CLAUDE.md", "buggy_invoice.py"])
            self.assertIn("claude -p", payload["next"][0])

    def test_init_agent_files_can_target_both_agents(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            code, output = call_cli(["init-agent-files", "--directory", directory, "--target", "both"])

            self.assertEqual(code, 0)
            root = Path(directory)
            self.assertTrue((root / "CLAUDE.md").exists())
            self.assertTrue((root / "AGENTS.md").exists())
            payload = json.loads(output)
            self.assertEqual(len(payload["written"]), 2)

    def test_claude_install_snippet_uses_user_scope(self) -> None:
        code, output = call_cli(["install-snippet", "claude"])

        self.assertEqual(code, 0)
        self.assertIn("claude mcp add -s user mcp-debugger", output)


if __name__ == "__main__":
    unittest.main()
