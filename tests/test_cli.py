from __future__ import annotations

import json
import socket
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

from vibe_debug.cli import _format_claude_stream, main


def call_cli(args: list[str]) -> tuple[int, str]:
    stdout = StringIO()
    with redirect_stdout(stdout):
        code = main(args)
    return code, stdout.getvalue()


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def line_with(path: Path, marker: str) -> int:
    for index, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if marker in line:
            return index
    raise AssertionError(f"marker not found: {marker}")


class CLITests(unittest.TestCase):
    def test_doctor_defaults_to_human_output(self) -> None:
        code, output = call_cli(["doctor"])

        self.assertEqual(code, 0)
        self.assertIn("vibe-debug", output)
        self.assertIn("debugpy import: ok", output)
        self.assertIn("MCP initialize: ok", output)
        self.assertNotIn('"checks"', output)

    def test_doctor_json_keeps_machine_readable_report(self) -> None:
        code, output = call_cli(["doctor", "--json"])

        self.assertEqual(code, 0)
        payload = json.loads(output)
        self.assertEqual(payload["name"], "vibe-debug")
        self.assertTrue(payload["ok"])
        self.assertIsInstance(payload["checks"], list)

    def test_doctor_quiet_prints_one_status_line(self) -> None:
        code, output = call_cli(["doctor", "--quiet"])

        self.assertEqual(code, 0)
        self.assertEqual(output, "vibe-debug: ok\n")

    def test_demo_project_writes_claude_memory_and_bug(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            code, output = call_cli(["demo-project", directory, "--target", "claude"])

            self.assertEqual(code, 0)
            root = Path(directory)
            self.assertTrue((root / "CLAUDE.md").exists())
            self.assertTrue((root / ".claude" / "skills" / "vibe-debug" / "SKILL.md").exists())
            self.assertTrue((root / "buggy_invoice.py").exists())
            self.assertIn("vibe-debug", (root / "CLAUDE.md").read_text())
            self.assertIn("debug-python <script.py>", (root / ".claude" / "skills" / "vibe-debug" / "SKILL.md").read_text())
            self.assertIn("actual_total", (root / "buggy_invoice.py").read_text())
            payload = json.loads(output)
            self.assertEqual(payload["created"], [".claude/skills/vibe-debug/SKILL.md", "CLAUDE.md", "buggy_invoice.py"])
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

    def test_init_cli_skill_writes_explicit_trigger_description_and_cli_docs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            code, output = call_cli(["init-cli-skill", "--directory", directory, "--target", "claude"])

            self.assertEqual(code, 0)
            payload = json.loads(output)
            path = Path(payload["written"][0])
            self.assertEqual(path.name, "SKILL.md")
            self.assertTrue(path.exists())
            contents = path.read_text(encoding="utf-8")
            self.assertIn("description:", contents)
            self.assertIn("reproducible Python bug", contents)
            self.assertIn("failing Python test", contents)
            self.assertIn("verify, validate, or implement Python behavior", contents)
            self.assertIn("code-writing tasks", contents)
            self.assertIn("Do not use for non-Python bugs", contents)
            self.assertIn("Always tell the user when and how you are using the debugger", contents)
            self.assertIn("stopped file, line, function", contents)
            self.assertIn("debug-python <script.py>", contents)
            self.assertIn("--break <file.py>:<line>", contents)
            self.assertIn("--json", contents)

    def test_claude_install_snippet_uses_user_scope(self) -> None:
        code, output = call_cli(["install-snippet", "claude"])

        self.assertEqual(code, 0)
        self.assertIn("claude mcp add -s user vibe-debug", output)
        self.assertIn("npx -y github:illscience/vibe-debug", output)

    def test_codex_install_snippet_uses_npx(self) -> None:
        code, output = call_cli(["install-snippet", "codex"])

        self.assertEqual(code, 0)
        self.assertIn("codex mcp add vibe_debug", output)
        self.assertIn("npx -y github:illscience/vibe-debug", output)

    def test_debug_python_stops_and_prints_locals(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            script = Path(directory) / "sample.py"
            script.write_text(
                "\n".join(
                    [
                        "def main():",
                        "    x = 41",
                        "    y = x + 1",
                        "    print(y)",
                        "",
                        "if __name__ == '__main__':",
                        "    main()",
                        "",
                    ]
                ),
                encoding="utf-8",
            )

            code, output = call_cli(
                [
                    "debug-python",
                    str(script),
                    "--break",
                    f"{script}:4",
                    "--eval",
                    "y",
                ]
            )

        self.assertEqual(code, 0)
        self.assertIn("Stopped: sample.py:4 in main", output)
        self.assertIn("x = 41", output)
        self.assertIn("y = 42", output)
        self.assertIn("y -> 42", output)

    def test_debug_request_starts_server_triggers_url_and_prints_request_locals(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            port = free_port()
            script = Path(directory) / "web_sample.py"
            script.write_text(
                "\n".join(
                    [
                        "from urllib.parse import parse_qs",
                        "from wsgiref.simple_server import make_server",
                        "",
                        "",
                        "def app(environ, start_response):",
                        "    path = environ['PATH_INFO']",
                        "    raw_query = environ.get('QUERY_STRING', '')",
                        "    params = parse_qs(raw_query)",
                        "    per_page = min(max(int(params.get('per_page', ['20'])[0]), 1), 50)",
                        "    status = '200 OK'",
                        "    start_response(status, [('Content-Type', 'text/plain')])  # BREAK_HANDLER",
                        "    return [f'{path} {per_page}'.encode()]",
                        "",
                        "",
                        "if __name__ == '__main__':",
                        f"    server = make_server('127.0.0.1', {port}, app)",
                        "    server.serve_forever()",
                        "",
                    ]
                ),
                encoding="utf-8",
            )
            breakpoint_line = line_with(script, "BREAK_HANDLER")

            code, output = call_cli(
                [
                    "debug-request",
                    str(script),
                    "--url",
                    f"http://127.0.0.1:{port}/wines?per_page=999",
                    "--break",
                    f"{script}:{breakpoint_line}",
                    "--eval",
                    "per_page",
                    "--eval",
                    "path",
                    "--json",
                    "--timeout",
                    "20",
                ]
            )

        self.assertEqual(code, 0, output)
        payload = json.loads(output)
        self.assertTrue(payload["ok"], payload)
        self.assertEqual(payload["mode"], "debug-request")
        self.assertEqual(payload["stopped"]["function"], "app")
        self.assertEqual(payload["stopped"]["line"], breakpoint_line)
        locals_by_name = {item["name"]: item["value"] for item in payload["locals"]}
        self.assertEqual(locals_by_name["path"], "'/wines'")
        self.assertEqual(locals_by_name["per_page"], "50")
        evaluations = {item["expression"]: item["result"] for item in payload["evaluations"]}
        self.assertEqual(evaluations["per_page"], "50")
        self.assertEqual(evaluations["path"], "'/wines'")

    def test_attach_python_attaches_to_debugpy_listener_and_prints_locals(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            port = free_port()
            script = Path(directory) / "attach_sample.py"
            script.write_text(
                "\n".join(
                    [
                        "def main():",
                        "    value = 10",
                        "    doubled = value * 2",
                        "    print(doubled)  # BREAK_PRINT",
                        "",
                        "",
                        "if __name__ == '__main__':",
                        "    main()",
                        "",
                    ]
                ),
                encoding="utf-8",
            )
            breakpoint_line = line_with(script, "BREAK_PRINT")
            target = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "debugpy",
                    "--listen",
                    f"127.0.0.1:{port}",
                    "--wait-for-client",
                    str(script),
                ],
                cwd=directory,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            try:
                code, output = call_cli(
                    [
                        "attach-python",
                        "--host",
                        "127.0.0.1",
                        "--port",
                        str(port),
                        "--break",
                        f"{script}:{breakpoint_line}",
                        "--eval",
                        "doubled",
                        "--json",
                        "--timeout",
                        "20",
                    ]
                )
            finally:
                if target.poll() is None:
                    target.terminate()
                    try:
                        target.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        target.kill()
                        target.wait(timeout=5)

        self.assertEqual(code, 0, output)
        payload = json.loads(output)
        self.assertTrue(payload["ok"], payload)
        self.assertEqual(payload["mode"], "attach-python")
        self.assertEqual(payload["stopped"]["function"], "main")
        self.assertEqual(payload["stopped"]["line"], breakpoint_line)
        locals_by_name = {item["name"]: item["value"] for item in payload["locals"]}
        self.assertEqual(locals_by_name["value"], "10")
        self.assertEqual(locals_by_name["doubled"], "20")
        evaluations = {item["expression"]: item["result"] for item in payload["evaluations"]}
        self.assertEqual(evaluations["doubled"], "20")

    def test_claude_progress_formats_debugger_events(self) -> None:
        events = [
            {
                "type": "system",
                "subtype": "init",
                "cwd": "/tmp/demo",
                "mcp_servers": [{"name": "vibe-debug", "status": "connected"}],
            },
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "tool-1",
                            "name": "mcp__vibe-debug__debug_python_repro",
                            "input": {"program": "/tmp/demo/buggy_invoice.py"},
                        }
                    ]
                },
            },
            {
                "type": "user",
                "message": {
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "tool-1",
                            "content": json.dumps(
                                {
                                    "stopped": {
                                        "location": {
                                            "name": "invoice_total",
                                            "line": 13,
                                            "source": {"path": "/tmp/demo/buggy_invoice.py"},
                                        }
                                    },
                                    "snapshot": {
                                        "locals": [
                                            {"name": "subtotal", "value": "120.0"},
                                            {"name": "rate", "value": "0.15"},
                                            {"name": "total", "value": "119.85"},
                                        ]
                                    },
                                }
                            ),
                        }
                    ]
                },
            },
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "tool-2",
                            "name": "mcp__vibe-debug__debug_evaluate",
                            "input": {"expression": "subtotal * (1 - rate)"},
                        }
                    ]
                },
            },
            {
                "type": "user",
                "message": {
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "tool-2",
                            "content": json.dumps(
                                {
                                    "expression": "subtotal * (1 - rate)",
                                    "result": "102.0",
                                }
                            ),
                        }
                    ]
                },
            },
        ]
        input_stream = StringIO("\n".join(json.dumps(event) for event in events))
        output_stream = StringIO()

        self.assertEqual(_format_claude_stream(input_stream, output_stream), 0)
        output = output_stream.getvalue()
        self.assertIn("MCP: vibe-debug connected", output)
        self.assertIn("Tool: vibe-debug.debug_python_repro (buggy_invoice.py)", output)
        self.assertIn("Stopped: buggy_invoice.py:13 in invoice_total", output)
        self.assertIn("Locals: subtotal=120.0 rate=0.15 total=119.85", output)
        self.assertIn("Eval: subtotal * (1 - rate) -> 102.0", output)

    def test_claude_progress_formats_vibe_debug_cli_json_from_bash(self) -> None:
        events = [
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "tool-1",
                            "name": "Bash",
                            "input": {
                                "command": "npx -y github:illscience/vibe-debug debug-python buggy_invoice.py --break buggy_invoice.py:13 --json",
                                "description": "Run debugger",
                            },
                        }
                    ]
                },
            },
            {
                "type": "user",
                "message": {
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "tool-1",
                            "content": json.dumps(
                                {
                                    "stopped": {
                                        "file": "/tmp/demo/buggy_invoice.py",
                                        "line": 13,
                                        "function": "invoice_total",
                                    },
                                    "locals": [
                                        {"name": "subtotal", "value": "120.0"},
                                        {"name": "rate", "value": "0.15"},
                                        {"name": "total", "value": "119.85"},
                                    ],
                                    "evaluations": [
                                        {"expression": "subtotal * (1 - rate)", "result": "102.0"}
                                    ],
                                }
                            ),
                        }
                    ]
                },
            },
        ]
        input_stream = StringIO("\n".join(json.dumps(event) for event in events))
        output_stream = StringIO()

        self.assertEqual(_format_claude_stream(input_stream, output_stream), 0)
        output = output_stream.getvalue()
        self.assertIn("Tool: Bash (vibe-debug debug-python)", output)
        self.assertIn("Stopped: buggy_invoice.py:13 in invoice_total", output)
        self.assertIn("Locals: subtotal=120.0 rate=0.15 total=119.85", output)
        self.assertIn("Eval: subtotal * (1 - rate) -> 102.0", output)

    def test_claude_progress_treats_pending_mcp_as_starting_until_tool_use(self) -> None:
        events = [
            {
                "type": "system",
                "subtype": "init",
                "cwd": "/tmp/demo",
                "mcp_servers": [{"name": "vibe-debug", "status": "pending"}],
            },
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "tool-1",
                            "name": "mcp__vibe-debug__debug_python_repro",
                            "input": {"program": "/tmp/demo/buggy_invoice.py"},
                        }
                    ]
                },
            },
        ]
        input_stream = StringIO("\n".join(json.dumps(event) for event in events))
        output_stream = StringIO()

        self.assertEqual(_format_claude_stream(input_stream, output_stream), 0)
        output = output_stream.getvalue()
        self.assertIn("MCP: vibe-debug starting", output)
        self.assertIn("MCP: vibe-debug active", output)
        self.assertNotIn("MCP: vibe-debug pending", output)

    def test_claude_progress_suppresses_transient_retried_tool_error(self) -> None:
        events = [
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "tool-1",
                            "name": "mcp__vibe-debug__debug_evaluate",
                            "input": {"expression": "subtotal * (1 - rate)"},
                        }
                    ]
                },
            },
            {
                "type": "user",
                "message": {
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "tool-1",
                            "is_error": True,
                            "content": json.dumps({"error": "NameError: name 'subtotal' is not defined"}),
                        }
                    ]
                },
            },
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "tool-2",
                            "name": "mcp__vibe-debug__debug_evaluate",
                            "input": {"expression": "subtotal * (1 - rate)", "frameId": 2},
                        }
                    ]
                },
            },
            {
                "type": "user",
                "message": {
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "tool-2",
                            "content": json.dumps({"expression": "subtotal * (1 - rate)", "result": "102.0"}),
                        }
                    ]
                },
            },
        ]
        input_stream = StringIO("\n".join(json.dumps(event) for event in events))
        output_stream = StringIO()

        self.assertEqual(_format_claude_stream(input_stream, output_stream), 0)
        output = output_stream.getvalue()
        self.assertNotIn("Tool error", output)
        self.assertIn("Eval: subtotal * (1 - rate) -> 102.0", output)


if __name__ == "__main__":
    unittest.main()
