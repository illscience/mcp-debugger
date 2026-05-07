from __future__ import annotations

import json
import os
import select
import socket
import subprocess
import sys
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
            [sys.executable, "-m", "codex_debugger.mcp_server"],
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


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


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
        assert init["serverInfo"]["name"] == "codex-debugger"
        client.notify("notifications/initialized")

        tools = client.request("tools/list")
        tool_names = {tool["name"] for tool in tools["tools"]}
        required = {
            "debug_guidance",
            "debug_python_repro",
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
                        "debug_continue to exit",
                    ],
                    "bugEvidence": {
                        "runtimeBuggyExpression": buggy_value["result"],
                        "runtimeExpectedExpression": correct_value["result"],
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
