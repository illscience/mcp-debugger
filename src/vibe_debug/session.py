from __future__ import annotations

import os
import signal
import socket
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .dap import DAPClient, DAPEvent


class DebugSessionError(RuntimeError):
    """Raised when the user requests an invalid debugger operation."""


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _connect_dap_with_retry(
    host: str,
    port: int,
    adapter_process: subprocess.Popen | None = None,
    timeout: float = 10.0,
) -> DAPClient:
    deadline = time.monotonic() + timeout
    last_error: OSError | None = None
    while time.monotonic() < deadline:
        if adapter_process is not None and adapter_process.poll() is not None:
            raise RuntimeError(f"debug adapter exited with code {adapter_process.returncode}")
        try:
            return DAPClient(host, port, timeout=timeout)
        except OSError as exc:
            last_error = exc
            time.sleep(0.05)
    raise TimeoutError(f"debug adapter did not accept {host}:{port}") from last_error


def _normalize_path(path: str, cwd: str | None = None) -> str:
    base = Path(cwd or os.getcwd())
    value = Path(path)
    if not value.is_absolute():
        value = base / value
    return str(value.resolve())


@dataclass
class DebugSession:
    session_id: str
    client: DAPClient
    adapter_process: subprocess.Popen | None = None
    state: str = "initializing"
    stopped_thread_id: int | None = None
    launch_request_seq: int | None = None
    event_cursor: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def launch(
        cls,
        program: str,
        args: list[str] | None = None,
        cwd: str | None = None,
        python: str | None = None,
        env: dict[str, str] | None = None,
        stop_on_entry: bool = False,
        timeout: float = 15.0,
    ) -> "DebugSession":
        python_executable = python or sys.executable
        working_directory = _normalize_path(cwd or os.getcwd())
        program_path = _normalize_path(program, working_directory)
        host = "127.0.0.1"
        port = _free_port()

        adapter_process = subprocess.Popen(
            [python_executable, "-m", "debugpy.adapter", "--host", host, "--port", str(port)],
            cwd=working_directory,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=True,
            start_new_session=True,
        )
        client = _connect_dap_with_retry(host, port, adapter_process=adapter_process, timeout=timeout)
        session = cls(
            session_id=str(uuid.uuid4()),
            client=client,
            adapter_process=adapter_process,
            metadata={
                "mode": "launch",
                "program": program_path,
                "cwd": working_directory,
                "adapterHost": host,
                "adapterPort": port,
            },
        )
        session._initialize()

        launch_args: dict[str, Any] = {
            "name": "vibe-debug",
            "type": "python",
            "request": "launch",
            "program": program_path,
            "cwd": working_directory,
            "args": args or [],
            "env": env or {},
            "console": "internalConsole",
            "justMyCode": False,
            "stopOnEntry": stop_on_entry,
            "python": [python_executable],
        }
        session.launch_request_seq = client.send_request("launch", launch_args)
        session.event_cursor = client.event_count()
        initialized = client.wait_for_event("initialized", timeout=timeout, after=0)
        session.event_cursor = max(session.event_cursor, client.events.index(initialized) + 1)
        session.state = "configuring"
        return session

    @classmethod
    def attach(
        cls,
        host: str,
        port: int,
        timeout: float = 15.0,
        path_mappings: list[dict[str, str]] | None = None,
    ) -> "DebugSession":
        client = _connect_dap_with_retry(host, port, timeout=timeout)
        session = cls(
            session_id=str(uuid.uuid4()),
            client=client,
            metadata={"mode": "attach", "adapterHost": host, "adapterPort": port},
        )
        session._initialize()
        attach_args: dict[str, Any] = {
            "name": "vibe-debug",
            "type": "python",
            "request": "attach",
            "justMyCode": False,
        }
        if path_mappings:
            attach_args["pathMappings"] = path_mappings
        session.launch_request_seq = client.send_request("attach", attach_args)
        initialized = client.wait_for_event("initialized", timeout=timeout, after=0)
        session.event_cursor = client.events.index(initialized) + 1
        session.state = "configuring"
        return session

    def set_breakpoints(self, file: str, lines: list[int], cwd: str | None = None) -> dict[str, Any]:
        path = _normalize_path(file, cwd)
        body = self.client.request(
            "setBreakpoints",
            {
                "source": {"path": path},
                "breakpoints": [{"line": int(line)} for line in lines],
                "sourceModified": False,
            },
        )
        return {
            "sessionId": self.session_id,
            "state": self.state,
            "file": path,
            "breakpoints": body.get("breakpoints", []),
        }

    def continue_execution(self, timeout: float = 15.0) -> dict[str, Any]:
        if self.state == "configuring":
            start = self.client.event_count()
            self.client.request("configurationDone", timeout=timeout)
            if self.launch_request_seq is not None:
                try:
                    self.client.wait_response(self.launch_request_seq, timeout=timeout)
                except TimeoutError:
                    pass
            event = self._wait_for_pause_or_exit(timeout=timeout, after=start)
            return self._event_result(event)

        if self.state != "stopped" or self.stopped_thread_id is None:
            raise DebugSessionError(f"cannot continue while session is {self.state!r}")

        start = self.client.event_count()
        self.client.request("continue", {"threadId": self.stopped_thread_id}, timeout=timeout)
        event = self._wait_for_pause_or_exit(timeout=timeout, after=start)
        return self._event_result(event)

    def step(self, kind: str, timeout: float = 15.0) -> dict[str, Any]:
        if self.state != "stopped" or self.stopped_thread_id is None:
            raise DebugSessionError(f"cannot step while session is {self.state!r}")

        command_by_kind = {
            "over": "next",
            "into": "stepIn",
            "out": "stepOut",
        }
        command = command_by_kind.get(kind)
        if command is None:
            raise DebugSessionError("step kind must be one of: over, into, out")

        start = self.client.event_count()
        self.client.request(command, {"threadId": self.stopped_thread_id}, timeout=timeout)
        event = self._wait_for_pause_or_exit(timeout=timeout, after=start)
        result = self._event_result(event)
        result["step"] = kind
        return result

    def stack(self, thread_id: int | None = None, levels: int = 20) -> dict[str, Any]:
        selected_thread = thread_id or self.stopped_thread_id
        if selected_thread is None:
            raise DebugSessionError("no stopped thread is available")

        body = self.client.request(
            "stackTrace",
            {"threadId": selected_thread, "startFrame": 0, "levels": levels},
        )
        frames = [self._frame_summary(frame) for frame in body.get("stackFrames", [])]
        return {
            "sessionId": self.session_id,
            "state": self.state,
            "threadId": selected_thread,
            "frames": frames,
            "totalFrames": body.get("totalFrames"),
        }

    def scopes(self, frame_id: int) -> dict[str, Any]:
        body = self.client.request("scopes", {"frameId": int(frame_id)})
        return {
            "sessionId": self.session_id,
            "state": self.state,
            "frameId": int(frame_id),
            "scopes": body.get("scopes", []),
        }

    def variables(
        self,
        variables_reference: int,
        start: int | None = None,
        count: int | None = None,
    ) -> dict[str, Any]:
        args: dict[str, Any] = {"variablesReference": int(variables_reference)}
        if start is not None:
            args["start"] = int(start)
        if count is not None:
            args["count"] = int(count)

        body = self.client.request("variables", args)
        return {
            "sessionId": self.session_id,
            "state": self.state,
            "variablesReference": int(variables_reference),
            "variables": body.get("variables", []),
        }

    def evaluate(self, expression: str, frame_id: int | None = None, context: str = "repl") -> dict[str, Any]:
        args: dict[str, Any] = {"expression": expression, "context": context}
        if frame_id is not None:
            args["frameId"] = int(frame_id)
        elif self.state == "stopped":
            stack = self.stack(levels=1)
            if stack["frames"]:
                args["frameId"] = int(stack["frames"][0]["id"])
        body = self.client.request("evaluate", args)
        return {
            "sessionId": self.session_id,
            "state": self.state,
            "expression": expression,
            "result": body.get("result"),
            "type": body.get("type"),
            "variablesReference": body.get("variablesReference"),
        }

    def top_frame_locals(self, limit: int = 40) -> dict[str, Any]:
        stack = self.stack(levels=1)
        if not stack["frames"]:
            return {"stack": stack, "locals": []}

        frame = stack["frames"][0]
        scopes = self.scopes(frame_id=int(frame["id"]))
        local_scopes = [scope for scope in scopes["scopes"] if scope.get("name", "").lower() == "locals"]
        if not local_scopes:
            return {"stack": stack, "frame": frame, "scopes": scopes["scopes"], "locals": []}

        variables_reference = int(local_scopes[0]["variablesReference"])
        variables = self.variables(variables_reference=variables_reference, count=limit)
        return {
            "stack": stack,
            "frame": frame,
            "scopes": scopes["scopes"],
            "locals": variables["variables"],
        }

    def stop(self, terminate_debuggee: bool = True) -> dict[str, Any]:
        try:
            self.client.request("disconnect", {"terminateDebuggee": terminate_debuggee}, timeout=5.0)
        except Exception:
            pass
        self.client.close()
        if terminate_debuggee:
            self._signal_debuggee_processes(signal.SIGTERM)
            self._wait_for_debuggee_exit(timeout=2.0)
        if self.adapter_process is not None and self.adapter_process.poll() is None:
            self._terminate_adapter_process_group()
            try:
                self.adapter_process.wait(timeout=5.0)
            except subprocess.TimeoutExpired:
                if terminate_debuggee:
                    self._signal_debuggee_processes(signal.SIGKILL)
                self._kill_adapter_process_group()
                self.adapter_process.wait(timeout=5.0)
        self.state = "terminated"
        return {"sessionId": self.session_id, "state": self.state}

    def _debuggee_process_ids(self) -> set[int]:
        process_ids: set[int] = set()
        for event in self.client.events:
            if event.event != "process":
                continue
            process_id = event.body.get("systemProcessId")
            if isinstance(process_id, int):
                process_ids.add(process_id)
        return process_ids

    def _signal_debuggee_processes(self, sig: int) -> None:
        for process_id in self._debuggee_process_ids():
            if self.adapter_process is not None:
                try:
                    process_group_id = os.getpgid(process_id)
                    if process_group_id != os.getpgrp():
                        os.killpg(process_group_id, sig)
                        continue
                except OSError:
                    pass
            try:
                os.kill(process_id, sig)
            except OSError:
                pass

    def _wait_for_debuggee_exit(self, timeout: float) -> None:
        deadline = time.monotonic() + timeout
        process_ids = self._debuggee_process_ids()
        while process_ids and time.monotonic() < deadline:
            alive: set[int] = set()
            for process_id in process_ids:
                try:
                    os.kill(process_id, 0)
                    alive.add(process_id)
                except OSError:
                    pass
            process_ids = alive
            if process_ids:
                time.sleep(0.05)

    def _terminate_adapter_process_group(self) -> None:
        if self.adapter_process is None:
            return
        try:
            os.killpg(os.getpgid(self.adapter_process.pid), signal.SIGTERM)
        except (AttributeError, OSError):
            self.adapter_process.terminate()

    def _kill_adapter_process_group(self) -> None:
        if self.adapter_process is None:
            return
        try:
            os.killpg(os.getpgid(self.adapter_process.pid), signal.SIGKILL)
        except (AttributeError, OSError):
            self.adapter_process.kill()

    def _initialize(self) -> None:
        self.client.request(
            "initialize",
            {
                "clientID": "vibe-debug",
                "clientName": "Vibe Debug MCP",
                "adapterID": "python",
                "pathFormat": "path",
                "linesStartAt1": True,
                "columnsStartAt1": True,
                "supportsVariableType": True,
                "supportsVariablePaging": True,
                "supportsRunInTerminalRequest": False,
            },
        )

    def _wait_for_pause_or_exit(self, timeout: float, after: int) -> DAPEvent:
        event = self.client.wait_for_event(("stopped", "terminated", "exited"), timeout=timeout, after=after)
        self.event_cursor = self.client.event_count()
        return event

    def _event_result(self, event: DAPEvent) -> dict[str, Any]:
        if event.event == "stopped":
            self.state = "stopped"
            thread_id = event.body.get("threadId")
            self.stopped_thread_id = int(thread_id) if thread_id is not None else self.stopped_thread_id
            result = {
                "sessionId": self.session_id,
                "state": self.state,
                "event": event.event,
                "stoppedReason": event.body.get("reason"),
                "threadId": self.stopped_thread_id,
            }
            try:
                stack = self.stack(thread_id=self.stopped_thread_id, levels=1)
                if stack["frames"]:
                    result["location"] = stack["frames"][0]
            except Exception as exc:
                result["locationError"] = str(exc)
            return result

        self.state = "terminated" if event.event == "terminated" else "exited"
        self.stopped_thread_id = None
        return {
            "sessionId": self.session_id,
            "state": self.state,
            "event": event.event,
            "body": event.body,
        }

    @staticmethod
    def _frame_summary(frame: dict[str, Any]) -> dict[str, Any]:
        source = frame.get("source") if isinstance(frame.get("source"), dict) else {}
        return {
            "id": frame.get("id"),
            "name": frame.get("name"),
            "line": frame.get("line"),
            "column": frame.get("column"),
            "source": {
                "name": source.get("name"),
                "path": source.get("path"),
            },
        }


class DebugSessionManager:
    def __init__(self) -> None:
        self._sessions: dict[str, DebugSession] = {}

    def launch(self, **kwargs: Any) -> dict[str, Any]:
        session = DebugSession.launch(**kwargs)
        self._sessions[session.session_id] = session
        return {
            "sessionId": session.session_id,
            "state": session.state,
            **session.metadata,
        }

    def attach(self, **kwargs: Any) -> dict[str, Any]:
        session = DebugSession.attach(**kwargs)
        self._sessions[session.session_id] = session
        return {
            "sessionId": session.session_id,
            "state": session.state,
            **session.metadata,
        }

    def get(self, session_id: str) -> DebugSession:
        session = self._sessions.get(session_id)
        if session is None:
            raise DebugSessionError(f"unknown debug session: {session_id}")
        return session

    def stop(self, session_id: str, terminate_debuggee: bool = True) -> dict[str, Any]:
        session = self.get(session_id)
        result = session.stop(terminate_debuggee=terminate_debuggee)
        self._sessions.pop(session_id, None)
        return result

    def stop_all(self) -> None:
        for session_id in list(self._sessions):
            try:
                self.stop(session_id)
            except Exception:
                pass
