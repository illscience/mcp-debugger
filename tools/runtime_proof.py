from __future__ import annotations

import json
import os
import select
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "examples" / "buggy_discount.py"


class MCPClient:
    def __init__(self) -> None:
        env = os.environ.copy()
        env["PYTHONPATH"] = str(ROOT / "src") + os.pathsep + env.get("PYTHONPATH", "")
        self.process = subprocess.Popen(
            [sys.executable, "-m", "vibe_debug.mcp_server"],
            cwd=ROOT,
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        self._next_id = 1

    def close(self) -> None:
        if self.process.poll() is None:
            try:
                self.notify("exit", {})
            except Exception:
                pass
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)

    def request(self, method: str, params: dict[str, Any] | None = None, timeout: float = 30.0) -> dict[str, Any]:
        message_id = self._next_id
        self._next_id += 1
        self._send({"jsonrpc": "2.0", "id": message_id, "method": method, "params": params or {}})

        deadline = time.monotonic() + timeout
        assert self.process.stdout is not None
        while time.monotonic() < deadline:
            remaining = max(0.0, deadline - time.monotonic())
            readable, _, _ = select.select([self.process.stdout], [], [], remaining)
            if not readable:
                break
            line = self.process.stdout.readline()
            if not line:
                stderr = ""
                if self.process.stderr is not None:
                    readable_stderr, _, _ = select.select([self.process.stderr], [], [], 0)
                    if readable_stderr:
                        stderr = self.process.stderr.read()
                raise RuntimeError(f"MCP server exited while waiting for {method}: {stderr}")
            response = json.loads(line)
            if response.get("id") == message_id:
                if "error" in response:
                    raise RuntimeError(response["error"])
                return response["result"]
        raise TimeoutError(f"timed out waiting for MCP method {method}")

    def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        self._send({"jsonrpc": "2.0", "method": method, "params": params or {}})

    def call_tool(self, name: str, arguments: dict[str, Any], timeout: float = 30.0) -> dict[str, Any]:
        result = self.request("tools/call", {"name": name, "arguments": arguments}, timeout=timeout)
        if result.get("isError"):
            stderr = ""
            if self.process.stderr is not None:
                readable_stderr, _, _ = select.select([self.process.stderr], [], [], 0)
                if readable_stderr:
                    stderr = self.process.stderr.read()
            raise RuntimeError(f"{name} failed: {result['content'][0]['text']}\n{stderr}")
        structured = result.get("structuredContent")
        if structured is not None:
            return structured
        return json.loads(result["content"][0]["text"])

    def _send(self, message: dict[str, Any]) -> None:
        assert self.process.stdin is not None
        self.process.stdin.write(json.dumps(message, separators=(",", ":")) + "\n")
        self.process.stdin.flush()


def line_with(marker: str) -> int:
    for index, line in enumerate(TARGET.read_text().splitlines(), start=1):
        if marker in line:
            return index
    raise AssertionError(f"marker not found: {marker}")


def line_with_file(path: Path, marker: str) -> int:
    for index, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if marker in line:
            return index
    raise AssertionError(f"marker not found in {path}: {marker}")


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def run_cli(args: list[str], timeout: float = 60.0) -> dict[str, Any]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src") + os.pathsep + env.get("PYTHONPATH", "")
    process = subprocess.run(
        [sys.executable, "-m", "vibe_debug.cli", *args],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
    )
    if process.returncode != 0:
        raise RuntimeError(
            f"CLI failed ({process.returncode}) for {args!r}\n"
            f"stdout:\n{process.stdout}\n"
            f"stderr:\n{process.stderr}"
        )
    return json.loads(process.stdout)


def node_supports_type_stripping() -> bool:
    node = shutil.which("node")
    if not node:
        return False
    with tempfile.TemporaryDirectory() as directory:
        script = Path(directory) / "probe.ts"
        script.write_text("const value: number = 41;\nconsole.log(value + 1);\n", encoding="utf-8")
        result = subprocess.run([node, str(script)], capture_output=True, text=True, timeout=10, check=False)
    return result.returncode == 0 and "42" in result.stdout


def top_frame(client: MCPClient, session_id: str) -> dict[str, Any]:
    stack = client.call_tool("debug_stack", {"sessionId": session_id})
    frames = stack["frames"]
    assert frames, "expected at least one stack frame"
    return frames[0]


def local_variables(client: MCPClient, session_id: str, frame_id: int) -> dict[str, dict[str, Any]]:
    scopes = client.call_tool("debug_scopes", {"sessionId": session_id, "frameId": frame_id})
    locals_scope = next(scope for scope in scopes["scopes"] if scope["name"].lower() == "locals")
    variables = client.call_tool(
        "debug_variables",
        {"sessionId": session_id, "variablesReference": locals_scope["variablesReference"]},
    )
    return {variable["name"]: variable for variable in variables["variables"]}


def main() -> int:
    client = MCPClient()
    session_id: str | None = None
    try:
        init = client.request("initialize", {"protocolVersion": "2025-06-18"})
        assert init["serverInfo"]["name"] == "vibe-debug"
        client.notify("notifications/initialized")

        tools = client.request("tools/list")
        tool_names = {tool["name"] for tool in tools["tools"]}
        required = {
            "debug_guidance",
            "debug_python_repro",
            "debug_typescript_repro",
            "debug_launch",
            "debug_attach",
            "debug_set_breakpoints",
            "debug_continue",
            "debug_step",
            "debug_stack",
            "debug_scopes",
            "debug_variables",
            "debug_evaluate",
            "debug_stop",
        }
        assert required.issubset(tool_names), sorted(required - tool_names)

        guidance = client.call_tool("debug_guidance", {})
        assert guidance["recommendedFirstTool"] == "debug_python_repro", guidance
        assert "debug_typescript_repro" in guidance["recommendedFirstTools"], guidance

        main_call_line = line_with("BREAK_MAIN_CALL")
        repro = client.call_tool(
            "debug_python_repro",
            {
                "program": str(TARGET),
                "cwd": str(ROOT),
                "python": sys.executable,
                "breakpoints": [{"file": str(TARGET), "line": main_call_line}],
                "keep_session": False,
                "timeout": 20,
            },
            timeout=40,
        )
        assert repro["stopped"]["state"] == "stopped", repro
        assert repro["stopped"]["location"]["name"] == "main", repro
        assert any(variable["name"] == "price" for variable in repro["snapshot"]["locals"]), repro

        launch = client.call_tool(
            "debug_launch",
            {
                "program": str(TARGET),
                "cwd": str(ROOT),
                "python": sys.executable,
                "timeout": 20,
            },
            timeout=40,
        )
        session_id = launch["sessionId"]
        assert launch["state"] == "configuring", launch

        breakpoint_result = client.call_tool(
            "debug_set_breakpoints",
            {"sessionId": session_id, "file": str(TARGET), "lines": [main_call_line]},
        )
        assert breakpoint_result["breakpoints"][0]["verified"] is True, breakpoint_result

        stopped = client.call_tool("debug_continue", {"sessionId": session_id, "timeout": 20}, timeout=40)
        assert stopped["state"] == "stopped", stopped
        assert stopped["stoppedReason"] == "breakpoint", stopped
        assert stopped["location"]["name"] == "main", stopped

        stepped_in = client.call_tool("debug_step", {"sessionId": session_id, "kind": "into", "timeout": 20}, timeout=40)
        assert stepped_in["state"] == "stopped", stepped_in
        frame = top_frame(client, session_id)
        assert frame["name"] == "apply_discount", frame

        variables = local_variables(client, session_id, int(frame["id"]))
        assert variables["price"]["value"] == "120.0", variables
        assert variables["loyalty_level"]["value"] == "'gold'", variables

        stepped_into_lookup = client.call_tool(
            "debug_step",
            {"sessionId": session_id, "kind": "into", "timeout": 20},
            timeout=40,
        )
        assert stepped_into_lookup["state"] == "stopped", stepped_into_lookup
        lookup_frame = top_frame(client, session_id)
        assert lookup_frame["name"] == "lookup_rate", lookup_frame

        stepped_out = client.call_tool("debug_step", {"sessionId": session_id, "kind": "out", "timeout": 20}, timeout=40)
        assert stepped_out["state"] == "stopped", stepped_out
        after_out_frame = top_frame(client, session_id)
        assert after_out_frame["name"] == "apply_discount", after_out_frame

        stepped_over = client.call_tool("debug_step", {"sessionId": session_id, "kind": "over", "timeout": 20}, timeout=40)
        assert stepped_over["state"] == "stopped", stepped_over
        bug_frame = top_frame(client, session_id)
        assert bug_frame["name"] == "apply_discount", bug_frame

        buggy_value = client.call_tool(
            "debug_evaluate",
            {
                "sessionId": session_id,
                "frameId": int(bug_frame["id"]),
                "expression": "round(price - rate, 2)",
            },
        )
        correct_value = client.call_tool(
            "debug_evaluate",
            {
                "sessionId": session_id,
                "frameId": int(bug_frame["id"]),
                "expression": "round(price * (1 - rate), 2)",
            },
        )
        assert buggy_value["result"] == "119.85", buggy_value
        assert correct_value["result"] == "102.0", correct_value

        default_frame_value = client.call_tool(
            "debug_evaluate",
            {
                "sessionId": session_id,
                "expression": "round(price * (1 - rate), 2)",
            },
        )
        assert default_frame_value["result"] == "102.0", default_frame_value

        finished = client.call_tool("debug_continue", {"sessionId": session_id, "timeout": 20}, timeout=40)
        assert finished["state"] in {"exited", "terminated"}, finished
        client.call_tool("debug_stop", {"sessionId": session_id})
        session_id = None

        attach_port = free_port()
        attach_target = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "debugpy",
                "--listen",
                f"127.0.0.1:{attach_port}",
                "--wait-for-client",
                str(TARGET),
            ],
            cwd=ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        attach_session_id: str | None = None
        try:
            attach = client.call_tool(
                "debug_attach",
                {"host": "127.0.0.1", "port": attach_port, "timeout": 20},
                timeout=40,
            )
            attach_session_id = attach["sessionId"]
            assert attach["state"] == "configuring", attach
            attach_breakpoint = client.call_tool(
                "debug_set_breakpoints",
                {"sessionId": attach_session_id, "file": str(TARGET), "lines": [main_call_line]},
            )
            assert attach_breakpoint["breakpoints"][0]["verified"] is True, attach_breakpoint
            attach_stopped = client.call_tool(
                "debug_continue",
                {"sessionId": attach_session_id, "timeout": 20},
                timeout=40,
            )
            assert attach_stopped["state"] == "stopped", attach_stopped
            assert attach_stopped["location"]["name"] == "main", attach_stopped
            client.call_tool("debug_stop", {"sessionId": attach_session_id})
            attach_session_id = None
        finally:
            if attach_session_id:
                try:
                    client.call_tool("debug_stop", {"sessionId": attach_session_id})
                except Exception:
                    pass
            if attach_target.poll() is None:
                attach_target.terminate()
                try:
                    attach_target.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    attach_target.kill()
                    attach_target.wait(timeout=5)

        with tempfile.TemporaryDirectory() as directory:
            proof_root = Path(directory)
            typescript_proved: list[str] = []
            typescript_evidence: dict[str, str] = {}

            web_port = free_port()
            web_target = proof_root / "web_probe.py"
            web_target.write_text(
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
                        f"    server = make_server('127.0.0.1', {web_port}, app)",
                        "    server.serve_forever()",
                        "",
                    ]
                ),
                encoding="utf-8",
            )
            web_line = line_with_file(web_target, "BREAK_HANDLER")
            debug_request = run_cli(
                [
                    "debug-request",
                    str(web_target),
                    "--url",
                    f"http://127.0.0.1:{web_port}/wines?per_page=999",
                    "--break",
                    f"{web_target}:{web_line}",
                    "--eval",
                    "per_page",
                    "--eval",
                    "path",
                    "--json",
                    "--timeout",
                    "20",
                ]
            )
            assert debug_request["stopped"]["state"] == "stopped", debug_request
            assert debug_request["stopped"]["function"] == "app", debug_request
            debug_request_evals = {item["expression"]: item["result"] for item in debug_request["evaluations"]}
            assert debug_request_evals["per_page"] == "50", debug_request
            assert debug_request_evals["path"] == "'/wines'", debug_request

            attach_cli_port = free_port()
            attach_cli_target = proof_root / "attach_cli_probe.py"
            attach_cli_target.write_text(
                "\n".join(
                    [
                        "def main():",
                        "    value = 10",
                        "    doubled = value * 2",
                        "    print(doubled)  # BREAK_ATTACH_CLI",
                        "",
                        "",
                        "if __name__ == '__main__':",
                        "    main()",
                        "",
                    ]
                ),
                encoding="utf-8",
            )
            attach_cli_line = line_with_file(attach_cli_target, "BREAK_ATTACH_CLI")
            attach_cli_process = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "debugpy",
                    "--listen",
                    f"127.0.0.1:{attach_cli_port}",
                    "--wait-for-client",
                    str(attach_cli_target),
                ],
                cwd=proof_root,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            try:
                attach_cli = run_cli(
                    [
                        "attach-python",
                        "--host",
                        "127.0.0.1",
                        "--port",
                        str(attach_cli_port),
                        "--break",
                        f"{attach_cli_target}:{attach_cli_line}",
                        "--eval",
                        "doubled",
                        "--json",
                        "--timeout",
                        "20",
                    ]
                )
                assert attach_cli["stopped"]["state"] == "stopped", attach_cli
                assert attach_cli["stopped"]["function"] == "main", attach_cli
                attach_cli_evals = {item["expression"]: item["result"] for item in attach_cli["evaluations"]}
                assert attach_cli_evals["doubled"] == "20", attach_cli
            finally:
                if attach_cli_process.poll() is None:
                    attach_cli_process.terminate()
                    try:
                        attach_cli_process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        attach_cli_process.kill()
                        attach_cli_process.wait(timeout=5)

            if node_supports_type_stripping():
                ts_target = proof_root / "pricing.ts"
                ts_target.write_text(
                    "\n".join(
                        [
                            "function calculateTotal(price: number, rate: number): number {",
                            "    const discount = price * rate;",
                            "    const finalTotal = price - discount;",
                            "    console.log(finalTotal); // BREAK_TS",
                            "    return finalTotal;",
                            "}",
                            "",
                            "calculateTotal(120, 0.15);",
                            "",
                        ]
                    ),
                    encoding="utf-8",
                )
                ts_line = line_with_file(ts_target, "BREAK_TS")

                ts_cli = run_cli(
                    [
                        "debug-typescript",
                        str(ts_target),
                        "--break",
                        f"{ts_target}:{ts_line}",
                        "--eval",
                        "finalTotal",
                        "--json",
                        "--timeout",
                        "20",
                    ]
                )
                assert ts_cli["stopped"]["state"] == "stopped", ts_cli
                assert ts_cli["stopped"]["function"] == "calculateTotal", ts_cli
                ts_cli_evals = {item["expression"]: item["result"] for item in ts_cli["evaluations"]}
                assert ts_cli_evals["finalTotal"] == "102", ts_cli
                typescript_proved.append("CLI debug-typescript")
                typescript_evidence["debugTypescriptFinalTotal"] = ts_cli_evals["finalTotal"]

                ts_mcp = client.call_tool(
                    "debug_typescript_repro",
                    {
                        "program": str(ts_target),
                        "cwd": str(proof_root),
                        "breakpoints": [{"file": str(ts_target), "line": ts_line}],
                        "evaluations": ["finalTotal"],
                        "keep_session": False,
                        "timeout": 20,
                    },
                    timeout=40,
                )
                assert ts_mcp["stopped"]["state"] == "stopped", ts_mcp
                assert ts_mcp["stopped"]["location"]["name"] == "calculateTotal", ts_mcp
                ts_mcp_evals = {item["expression"]: item["result"] for item in ts_mcp["evaluations"]}
                assert ts_mcp_evals["finalTotal"] == "102", ts_mcp
                typescript_proved.append("MCP debug_typescript_repro")
                typescript_evidence["debugTypescriptMcpFinalTotal"] = ts_mcp_evals["finalTotal"]
            else:
                typescript_proved.append("TypeScript skipped: local Node cannot execute .ts directly")

        print(
            json.dumps(
                {
                    "ok": True,
                    "proved": [
                        "MCP initialize/tools/list",
                        "debug_guidance",
                        "debug_python_repro",
                        "debug_launch",
                        "debug_attach",
                        "debug_set_breakpoints",
                        "debug_continue to breakpoint",
                        "debug_step into",
                        "debug_scopes/debug_variables locals",
                        "debug_step out",
                        "debug_step over",
                        "debug_evaluate",
                        "debug_evaluate default top frame",
                        "debug_continue to exit",
                        "CLI debug-request",
                        "CLI attach-python",
                        *typescript_proved,
                    ],
                    "bugEvidence": {
                        "runtimeBuggyExpression": buggy_value["result"],
                        "runtimeExpectedExpression": correct_value["result"],
                    },
                    "cliEvidence": {
                        "debugRequestPerPage": debug_request_evals["per_page"],
                        "debugRequestPath": debug_request_evals["path"],
                        "attachPythonDoubled": attach_cli_evals["doubled"],
                        **typescript_evidence,
                    },
                },
                indent=2,
            )
        )
        return 0
    finally:
        if session_id:
            try:
                client.call_tool("debug_stop", {"sessionId": session_id})
            except Exception:
                pass
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
