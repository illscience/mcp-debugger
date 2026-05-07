from __future__ import annotations

import argparse
import json
import sys
import traceback
from typing import Any, Callable

from . import __version__
from .agent_guidance import AGENT_USAGE_GUIDANCE
from .session import DebugSessionManager


ToolHandler = Callable[[dict[str, Any]], dict[str, Any]]


def _schema(properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": required or [],
        "additionalProperties": False,
    }


def _tool_definitions() -> list[dict[str, Any]]:
    return [
        {
            "name": "debug_guidance",
            "description": "Return concise guidance for coding agents on when and how to use the debugger MCP tools.",
            "inputSchema": _schema({}),
        },
        {
            "name": "debug_python_repro",
            "description": (
                "Use this first for a reproducible Python bug. Launch a Python script under debugpy, "
                "set breakpoints, continue to the first stop, and return stack plus top-frame locals."
            ),
            "inputSchema": _schema(
                {
                    "program": {"type": "string", "description": "Path to the Python script that reproduces the bug."},
                    "args": {"type": "array", "items": {"type": "string"}, "default": []},
                    "cwd": {"type": "string", "description": "Working directory for the target program."},
                    "python": {"type": "string", "description": "Python executable to use for debugpy and the target."},
                    "env": {
                        "type": "object",
                        "additionalProperties": {"type": "string"},
                        "description": "Environment variables to add to the target process.",
                    },
                    "breakpoints": {
                        "type": "array",
                        "description": "Breakpoints to set before the script starts running.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "file": {"type": "string"},
                                "line": {"type": "integer"},
                            },
                            "required": ["file", "line"],
                            "additionalProperties": False,
                        },
                    },
                    "stop_on_entry": {"type": "boolean", "default": False},
                    "timeout": {"type": "number", "default": 20},
                    "locals_limit": {"type": "integer", "default": 40},
                    "keep_session": {
                        "type": "boolean",
                        "default": True,
                        "description": "Keep the session open after the first stop so the agent can step or inspect more.",
                    },
                },
                ["program"],
            ),
        },
        {
            "name": "debug_launch",
            "description": "Launch a Python program under debugpy and pause before user code until breakpoints are configured.",
            "inputSchema": _schema(
                {
                    "program": {"type": "string", "description": "Path to the Python file to launch."},
                    "args": {"type": "array", "items": {"type": "string"}, "default": []},
                    "cwd": {"type": "string", "description": "Working directory for the target program."},
                    "python": {"type": "string", "description": "Python executable to use for debugpy and the target."},
                    "env": {
                        "type": "object",
                        "additionalProperties": {"type": "string"},
                        "description": "Environment variables to add to the target process.",
                    },
                    "stop_on_entry": {"type": "boolean", "default": False},
                    "timeout": {"type": "number", "default": 15},
                },
                ["program"],
            ),
        },
        {
            "name": "debug_attach",
            "description": "Attach to an already-listening debugpy adapter on localhost or another explicit host.",
            "inputSchema": _schema(
                {
                    "host": {"type": "string", "default": "127.0.0.1"},
                    "port": {"type": "integer"},
                    "timeout": {"type": "number", "default": 15},
                    "path_mappings": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "localRoot": {"type": "string"},
                                "remoteRoot": {"type": "string"},
                            },
                            "required": ["localRoot", "remoteRoot"],
                            "additionalProperties": False,
                        },
                    },
                },
                ["port"],
            ),
        },
        {
            "name": "debug_set_breakpoints",
            "description": "Set one or more line breakpoints in a Python source file.",
            "inputSchema": _schema(
                {
                    "sessionId": {"type": "string"},
                    "file": {"type": "string"},
                    "lines": {"type": "array", "items": {"type": "integer"}},
                    "cwd": {"type": "string"},
                },
                ["sessionId", "file", "lines"],
            ),
        },
        {
            "name": "debug_continue",
            "description": "Continue execution until the next breakpoint, exception, process exit, or timeout.",
            "inputSchema": _schema(
                {
                    "sessionId": {"type": "string"},
                    "timeout": {"type": "number", "default": 15},
                },
                ["sessionId"],
            ),
        },
        {
            "name": "debug_step",
            "description": "Step the stopped thread over the next line, into a function, or out of the current function.",
            "inputSchema": _schema(
                {
                    "sessionId": {"type": "string"},
                    "kind": {"type": "string", "enum": ["over", "into", "out"]},
                    "timeout": {"type": "number", "default": 15},
                },
                ["sessionId", "kind"],
            ),
        },
        {
            "name": "debug_stack",
            "description": "Return stack frames for the current stopped thread.",
            "inputSchema": _schema(
                {
                    "sessionId": {"type": "string"},
                    "threadId": {"type": "integer"},
                    "levels": {"type": "integer", "default": 20},
                },
                ["sessionId"],
            ),
        },
        {
            "name": "debug_scopes",
            "description": "Return debugger scopes for a stack frame, including variablesReference handles.",
            "inputSchema": _schema(
                {
                    "sessionId": {"type": "string"},
                    "frameId": {"type": "integer"},
                },
                ["sessionId", "frameId"],
            ),
        },
        {
            "name": "debug_variables",
            "description": "Expand a debugger variablesReference to inspect locals, globals, objects, lists, or dicts.",
            "inputSchema": _schema(
                {
                    "sessionId": {"type": "string"},
                    "variablesReference": {"type": "integer"},
                    "start": {"type": "integer"},
                    "count": {"type": "integer"},
                },
                ["sessionId", "variablesReference"],
            ),
        },
        {
            "name": "debug_evaluate",
            "description": "Evaluate an expression in a paused frame. This can execute code in the target process.",
            "inputSchema": _schema(
                {
                    "sessionId": {"type": "string"},
                    "expression": {"type": "string"},
                    "frameId": {"type": "integer"},
                    "context": {"type": "string", "default": "repl"},
                },
                ["sessionId", "expression"],
            ),
        },
        {
            "name": "debug_stop",
            "description": "Disconnect from a debug session and terminate the launched debuggee by default.",
            "inputSchema": _schema(
                {
                    "sessionId": {"type": "string"},
                    "terminate_debuggee": {"type": "boolean", "default": True},
                },
                ["sessionId"],
            ),
        },
    ]


class MCPDebuggerServer:
    def __init__(self) -> None:
        self.manager = DebugSessionManager()
        self.handlers: dict[str, ToolHandler] = {
            "debug_guidance": self._debug_guidance,
            "debug_python_repro": self._debug_python_repro,
            "debug_launch": self._debug_launch,
            "debug_attach": self._debug_attach,
            "debug_set_breakpoints": self._debug_set_breakpoints,
            "debug_continue": self._debug_continue,
            "debug_step": self._debug_step,
            "debug_stack": self._debug_stack,
            "debug_scopes": self._debug_scopes,
            "debug_variables": self._debug_variables,
            "debug_evaluate": self._debug_evaluate,
            "debug_stop": self._debug_stop,
        }

    def _debug_guidance(self, args: dict[str, Any]) -> dict[str, Any]:
        return {
            "guidance": AGENT_USAGE_GUIDANCE,
            "recommendedFirstTool": "debug_python_repro",
            "primitiveTools": [
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
            ],
        }

    def serve(self) -> None:
        try:
            for line in sys.stdin:
                if not line.strip():
                    continue
                try:
                    message = json.loads(line)
                    response = self.handle_message(message)
                    if response is not None:
                        self._write(response)
                except Exception as exc:
                    self._write(
                        {
                            "jsonrpc": "2.0",
                            "id": None,
                            "error": {"code": -32603, "message": str(exc)},
                        }
                    )
        finally:
            self.manager.stop_all()

    def handle_message(self, message: dict[str, Any]) -> dict[str, Any] | None:
        method = message.get("method")
        message_id = message.get("id")

        if method is None:
            return None

        if method == "initialize":
            requested_version = (message.get("params") or {}).get("protocolVersion")
            return self._result(
                message_id,
                {
                    "protocolVersion": requested_version or "2025-06-18",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "vibe-debug", "version": __version__},
                },
            )

        if method == "notifications/initialized":
            return None

        if method == "ping":
            return self._result(message_id, {})

        if method == "tools/list":
            return self._result(message_id, {"tools": _tool_definitions()})

        if method == "tools/call":
            params = message.get("params") or {}
            name = params.get("name")
            arguments = params.get("arguments") or {}
            return self._result(message_id, self.call_tool(name, arguments))

        if method == "shutdown":
            self.manager.stop_all()
            return self._result(message_id, {})

        if method == "exit":
            self.manager.stop_all()
            raise SystemExit(0)

        return {
            "jsonrpc": "2.0",
            "id": message_id,
            "error": {"code": -32601, "message": f"method not found: {method}"},
        }

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        handler = self.handlers.get(name)
        if handler is None:
            return _tool_error(f"unknown tool: {name}")
        try:
            result = handler(arguments)
            return _tool_result(result)
        except Exception as exc:
            traceback.print_exc(file=sys.stderr)
            return _tool_error(str(exc), {"exceptionType": type(exc).__name__})

    def _debug_launch(self, args: dict[str, Any]) -> dict[str, Any]:
        return self.manager.launch(
            program=args["program"],
            args=args.get("args") or [],
            cwd=args.get("cwd"),
            python=args.get("python"),
            env=args.get("env"),
            stop_on_entry=bool(args.get("stop_on_entry", False)),
            timeout=float(args.get("timeout", 15)),
        )

    def _debug_python_repro(self, args: dict[str, Any]) -> dict[str, Any]:
        timeout = float(args.get("timeout", 20))
        launch = self.manager.launch(
            program=args["program"],
            args=args.get("args") or [],
            cwd=args.get("cwd"),
            python=args.get("python"),
            env=args.get("env"),
            stop_on_entry=bool(args.get("stop_on_entry", False)),
            timeout=timeout,
        )
        session_id = launch["sessionId"]
        session = self.manager.get(session_id)
        breakpoint_results: list[dict[str, Any]] = []

        for item in args.get("breakpoints") or []:
            breakpoint_results.append(
                session.set_breakpoints(
                    file=item["file"],
                    lines=[int(item["line"])],
                    cwd=args.get("cwd"),
                )
            )

        stopped = session.continue_execution(timeout=timeout)
        snapshot: dict[str, Any] = {}
        if stopped.get("state") == "stopped":
            snapshot = session.top_frame_locals(limit=int(args.get("locals_limit", 40)))

        if not bool(args.get("keep_session", True)):
            self.manager.stop(session_id)

        return {
            "sessionId": session_id,
            "launch": launch,
            "breakpoints": breakpoint_results,
            "stopped": stopped,
            "snapshot": snapshot,
            "nextActions": [
                "Use debug_step to move over/into/out from the current line.",
                "Use debug_variables to expand object variablesReference values.",
                "Use debug_stop when finished with this session.",
            ],
        }

    def _debug_attach(self, args: dict[str, Any]) -> dict[str, Any]:
        return self.manager.attach(
            host=args.get("host") or "127.0.0.1",
            port=int(args["port"]),
            timeout=float(args.get("timeout", 15)),
            path_mappings=args.get("path_mappings"),
        )

    def _debug_set_breakpoints(self, args: dict[str, Any]) -> dict[str, Any]:
        return self.manager.get(args["sessionId"]).set_breakpoints(
            file=args["file"],
            lines=[int(line) for line in args["lines"]],
            cwd=args.get("cwd"),
        )

    def _debug_continue(self, args: dict[str, Any]) -> dict[str, Any]:
        return self.manager.get(args["sessionId"]).continue_execution(timeout=float(args.get("timeout", 15)))

    def _debug_step(self, args: dict[str, Any]) -> dict[str, Any]:
        return self.manager.get(args["sessionId"]).step(
            kind=args["kind"],
            timeout=float(args.get("timeout", 15)),
        )

    def _debug_stack(self, args: dict[str, Any]) -> dict[str, Any]:
        return self.manager.get(args["sessionId"]).stack(
            thread_id=args.get("threadId"),
            levels=int(args.get("levels", 20)),
        )

    def _debug_scopes(self, args: dict[str, Any]) -> dict[str, Any]:
        return self.manager.get(args["sessionId"]).scopes(frame_id=int(args["frameId"]))

    def _debug_variables(self, args: dict[str, Any]) -> dict[str, Any]:
        return self.manager.get(args["sessionId"]).variables(
            variables_reference=int(args["variablesReference"]),
            start=args.get("start"),
            count=args.get("count"),
        )

    def _debug_evaluate(self, args: dict[str, Any]) -> dict[str, Any]:
        return self.manager.get(args["sessionId"]).evaluate(
            expression=args["expression"],
            frame_id=args.get("frameId"),
            context=args.get("context", "repl"),
        )

    def _debug_stop(self, args: dict[str, Any]) -> dict[str, Any]:
        return self.manager.stop(
            session_id=args["sessionId"],
            terminate_debuggee=bool(args.get("terminate_debuggee", True)),
        )

    @staticmethod
    def _result(message_id: Any, result: dict[str, Any]) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": message_id, "result": result}

    @staticmethod
    def _write(message: dict[str, Any]) -> None:
        sys.stdout.write(json.dumps(message, separators=(",", ":")) + "\n")
        sys.stdout.flush()


def _tool_result(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "content": [{"type": "text", "text": json.dumps(result, indent=2, sort_keys=True)}],
        "structuredContent": result,
        "isError": False,
    }


def _tool_error(message: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = {"error": message}
    if data:
        payload.update(data)
    return {
        "content": [{"type": "text", "text": json.dumps(payload, indent=2, sort_keys=True)}],
        "structuredContent": payload,
        "isError": True,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the vibe-debug MCP server.")
    parser.parse_args(argv)
    MCPDebuggerServer().serve()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
