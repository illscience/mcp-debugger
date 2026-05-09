from __future__ import annotations

import base64
import json
import os
import signal
import socket
import struct
import subprocess
import time
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


class NodeDebugSessionError(RuntimeError):
    """Raised when a Node inspector operation fails."""


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _normalize_path(path: str, cwd: str | None = None) -> str:
    base = Path(cwd or os.getcwd())
    value = Path(path)
    if not value.is_absolute():
        value = base / value
    return str(value.resolve())


def _path_to_file_url(path: str) -> str:
    return str(Path(path).resolve())


def _file_url_to_path(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "file":
        return url
    return urllib.request.url2pathname(parsed.path)


def _remote_object_value(value: dict[str, Any]) -> str:
    if "unserializableValue" in value:
        return str(value["unserializableValue"])
    if "value" in value:
        raw = value["value"]
        if isinstance(raw, str):
            return repr(raw)
        if raw is True:
            return "true"
        if raw is False:
            return "false"
        if raw is None:
            return "null"
        return str(raw)
    if value.get("type") == "undefined":
        return "undefined"
    description = value.get("description")
    if isinstance(description, str):
        return description
    return str(value.get("type") or "unknown")


class _WebSocket:
    def __init__(self, sock: socket.socket):
        self._socket = sock

    @classmethod
    def connect(cls, url: str, timeout: float) -> "_WebSocket":
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme != "ws":
            raise NodeDebugSessionError(f"unsupported inspector websocket URL: {url}")
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or 80
        path = parsed.path or "/"
        if parsed.query:
            path += f"?{parsed.query}"

        sock = socket.create_connection((host, port), timeout=timeout)
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        request = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {host}:{port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n"
            "\r\n"
        )
        sock.sendall(request.encode("ascii"))
        response = b""
        while b"\r\n\r\n" not in response:
            chunk = sock.recv(4096)
            if not chunk:
                raise NodeDebugSessionError("inspector websocket closed during handshake")
            response += chunk
        status_line = response.split(b"\r\n", 1)[0]
        if b" 101 " not in status_line:
            raise NodeDebugSessionError(f"inspector websocket upgrade failed: {status_line!r}")
        return cls(sock)

    def send_text(self, text: str) -> None:
        payload = text.encode("utf-8")
        header = bytearray([0x81])
        length = len(payload)
        if length < 126:
            header.append(0x80 | length)
        elif length <= 0xFFFF:
            header.append(0x80 | 126)
            header.extend(struct.pack("!H", length))
        else:
            header.append(0x80 | 127)
            header.extend(struct.pack("!Q", length))
        mask = os.urandom(4)
        masked = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
        self._socket.sendall(bytes(header) + mask + masked)

    def recv_text(self, timeout: float) -> str:
        self._socket.settimeout(timeout)
        while True:
            first = self._read_exact(2)
            opcode = first[0] & 0x0F
            masked = bool(first[1] & 0x80)
            length = first[1] & 0x7F
            if length == 126:
                length = struct.unpack("!H", self._read_exact(2))[0]
            elif length == 127:
                length = struct.unpack("!Q", self._read_exact(8))[0]
            mask = self._read_exact(4) if masked else b""
            payload = self._read_exact(length)
            if masked:
                payload = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))

            if opcode == 0x8:
                raise EOFError("inspector websocket closed")
            if opcode == 0x9:
                self._send_control(0xA, payload)
                continue
            if opcode in (0x1, 0x0):
                return payload.decode("utf-8")

    def close(self) -> None:
        try:
            self._send_control(0x8, b"")
        except OSError:
            pass
        try:
            self._socket.close()
        except OSError:
            pass

    def _send_control(self, opcode: int, payload: bytes) -> None:
        mask = os.urandom(4)
        header = bytes([0x80 | opcode, 0x80 | len(payload)])
        masked = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
        self._socket.sendall(header + mask + masked)

    def _read_exact(self, length: int) -> bytes:
        chunks: list[bytes] = []
        remaining = length
        while remaining:
            chunk = self._socket.recv(remaining)
            if not chunk:
                raise EOFError("inspector websocket closed")
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)


class InspectorClient:
    def __init__(self, websocket_url: str, timeout: float = 10.0):
        self.websocket_url = websocket_url
        self.timeout = timeout
        self._websocket = _WebSocket.connect(websocket_url, timeout=timeout)
        self._seq = 1
        self._responses: dict[int, dict[str, Any]] = {}
        self._events: list[dict[str, Any]] = []

    def request(self, method: str, params: dict[str, Any] | None = None, timeout: float | None = None) -> dict[str, Any]:
        request_id = self.send_request(method, params)
        return self.wait_response(request_id, timeout=timeout)

    def send_request(self, method: str, params: dict[str, Any] | None = None) -> int:
        request_id = self._seq
        self._seq += 1
        self._websocket.send_text(json.dumps({"id": request_id, "method": method, "params": params or {}}))
        return request_id

    def wait_response(self, request_id: int, timeout: float | None = None) -> dict[str, Any]:
        deadline = time.monotonic() + (timeout if timeout is not None else self.timeout)
        while request_id not in self._responses:
            self._read_one(deadline)
        response = self._responses.pop(request_id)
        if "error" in response:
            raise NodeDebugSessionError(f"inspector request failed: {response['error']}")
        result = response.get("result")
        return result if isinstance(result, dict) else {}

    def wait_event(
        self,
        method: str,
        timeout: float | None = None,
        after: int = 0,
        process: subprocess.Popen | None = None,
    ) -> dict[str, Any]:
        deadline = time.monotonic() + (timeout if timeout is not None else self.timeout)
        start = after
        while True:
            for index in range(start, len(self._events)):
                event = self._events[index]
                if event.get("method") == method:
                    return event
            start = len(self._events)
            if process is not None and process.poll() is not None:
                raise NodeDebugSessionError(f"target process exited with code {process.returncode}")
            self._read_one(deadline)

    def event_count(self) -> int:
        return len(self._events)

    def close(self) -> None:
        self._websocket.close()

    def _read_one(self, deadline: float) -> None:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("timed out waiting for inspector message")
        message = json.loads(self._websocket.recv_text(timeout=remaining))
        if "id" in message:
            self._responses[int(message["id"])] = message
        elif "method" in message:
            self._events.append(message)


def _inspector_url(host: str, port: int, process: subprocess.Popen | None, timeout: float) -> str:
    deadline = time.monotonic() + timeout
    last_error: BaseException | None = None
    url = f"http://{host}:{port}/json/list"
    while time.monotonic() < deadline:
        if process is not None and process.poll() is not None:
            raise NodeDebugSessionError(f"target process exited with code {process.returncode}")
        try:
            with urllib.request.urlopen(url, timeout=0.2) as response:
                targets = json.loads(response.read().decode("utf-8"))
            if isinstance(targets, list) and targets:
                websocket_url = targets[0].get("webSocketDebuggerUrl")
                if isinstance(websocket_url, str):
                    return websocket_url
        except BaseException as exc:
            last_error = exc
        time.sleep(0.05)
    raise TimeoutError(f"node inspector did not accept {host}:{port}") from last_error


@dataclass
class NodeDebugSession:
    session_id: str
    client: InspectorClient
    process: subprocess.Popen | None = None
    state: str = "initializing"
    stopped_call_frames: list[dict[str, Any]] = field(default_factory=list)
    breakpoints: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def launch(
        cls,
        program: str,
        args: list[str] | None = None,
        cwd: str | None = None,
        node: str | None = None,
        node_args: list[str] | None = None,
        env: dict[str, str] | None = None,
        timeout: float = 15.0,
    ) -> "NodeDebugSession":
        node_executable = node or "node"
        working_directory = _normalize_path(cwd or os.getcwd())
        program_path = _normalize_path(program, working_directory)
        host = "127.0.0.1"
        port = _free_port()
        command = [
            node_executable,
            f"--inspect-brk={host}:{port}",
            *(node_args or []),
            program_path,
            *(args or []),
        ]
        process_env = os.environ.copy()
        process_env.update(env or {})
        process = subprocess.Popen(
            command,
            cwd=working_directory,
            env=process_env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=True,
            start_new_session=True,
        )
        websocket_url = _inspector_url(host, port, process=process, timeout=timeout)
        client = InspectorClient(websocket_url, timeout=timeout)
        session = cls(
            session_id=str(uuid.uuid4()),
            client=client,
            process=process,
            metadata={
                "mode": "launch",
                "runtime": "node",
                "program": program_path,
                "cwd": working_directory,
                "adapterHost": host,
                "adapterPort": port,
                "inspectorUrl": websocket_url,
            },
        )
        session._initialize(timeout=timeout)
        session.state = "configuring"
        return session

    @classmethod
    def attach(cls, host: str, port: int, timeout: float = 15.0) -> "NodeDebugSession":
        websocket_url = _inspector_url(host, port, process=None, timeout=timeout)
        client = InspectorClient(websocket_url, timeout=timeout)
        session = cls(
            session_id=str(uuid.uuid4()),
            client=client,
            metadata={
                "mode": "attach",
                "runtime": "node",
                "adapterHost": host,
                "adapterPort": port,
                "inspectorUrl": websocket_url,
            },
        )
        session._initialize(timeout=timeout)
        session.state = "configuring"
        return session

    def set_breakpoints(self, file: str, lines: list[int], cwd: str | None = None) -> dict[str, Any]:
        path = _normalize_path(file, cwd)
        url = _path_to_file_url(path)
        results: list[dict[str, Any]] = []
        for line in lines:
            body = self.client.request(
                "Debugger.setBreakpointByUrl",
                {
                    "url": url,
                    "lineNumber": int(line) - 1,
                    "columnNumber": 0,
                },
            )
            summary = {
                "line": int(line),
                "verified": bool(body.get("breakpointId")),
                "breakpointId": body.get("breakpointId"),
                "locations": body.get("locations", []),
            }
            results.append(summary)
            self.breakpoints.append({"file": path, "url": url, "line": int(line), **summary})
        return {
            "sessionId": self.session_id,
            "state": self.state,
            "file": path,
            "breakpoints": results,
        }

    def continue_execution(self, timeout: float = 15.0, stop_on_entry: bool = False) -> dict[str, Any]:
        if self.state == "configuring":
            start = self.client.event_count()
            self.client.request("Runtime.runIfWaitingForDebugger", timeout=timeout)
            return self._wait_for_relevant_pause(timeout=timeout, after=start, stop_on_entry=stop_on_entry)

        if self.state != "stopped":
            raise NodeDebugSessionError(f"cannot continue while session is {self.state!r}")

        start = self.client.event_count()
        self.client.request("Debugger.resume", timeout=timeout)
        return self._wait_for_relevant_pause(timeout=timeout, after=start, stop_on_entry=False)

    def step(self, kind: str, timeout: float = 15.0) -> dict[str, Any]:
        if self.state != "stopped":
            raise NodeDebugSessionError(f"cannot step while session is {self.state!r}")
        command_by_kind = {
            "over": "Debugger.stepOver",
            "into": "Debugger.stepInto",
            "out": "Debugger.stepOut",
        }
        command = command_by_kind.get(kind)
        if command is None:
            raise NodeDebugSessionError("step kind must be one of: over, into, out")
        start = self.client.event_count()
        self.client.request(command, timeout=timeout)
        result = self._event_result(self.client.wait_event("Debugger.paused", timeout=timeout, after=start, process=self.process))
        result["step"] = kind
        return result

    def stack(self, thread_id: int | None = None, levels: int = 20) -> dict[str, Any]:
        frames = [self._frame_summary(frame) for frame in self.stopped_call_frames[:levels]]
        return {
            "sessionId": self.session_id,
            "state": self.state,
            "threadId": thread_id,
            "frames": frames,
            "totalFrames": len(self.stopped_call_frames),
        }

    def evaluate(self, expression: str, frame_id: str | int | None = None, context: str = "repl") -> dict[str, Any]:
        if self.state != "stopped" or not self.stopped_call_frames:
            raise NodeDebugSessionError("no stopped call frame is available")
        call_frame_id = str(frame_id) if frame_id is not None else str(self.stopped_call_frames[0]["callFrameId"])
        body = self.client.request(
            "Debugger.evaluateOnCallFrame",
            {
                "callFrameId": call_frame_id,
                "expression": expression,
                "returnByValue": False,
                "silent": True,
            },
        )
        if "exceptionDetails" in body:
            raise NodeDebugSessionError(f"evaluation failed: {body['exceptionDetails']}")
        result = body.get("result")
        value = _remote_object_value(result) if isinstance(result, dict) else None
        return {
            "sessionId": self.session_id,
            "state": self.state,
            "expression": expression,
            "result": value,
            "type": result.get("type") if isinstance(result, dict) else None,
            "variablesReference": result.get("objectId") if isinstance(result, dict) else None,
        }

    def top_frame_locals(self, limit: int = 40) -> dict[str, Any]:
        stack = self.stack(levels=1)
        if not self.stopped_call_frames:
            return {"stack": stack, "locals": []}
        frame = self.stopped_call_frames[0]
        locals_: list[dict[str, Any]] = []
        seen: set[str] = set()
        for scope in frame.get("scopeChain", []):
            if scope.get("type") not in {"local", "block", "closure"}:
                continue
            scope_object = scope.get("object")
            object_id = scope_object.get("objectId") if isinstance(scope_object, dict) else None
            if not isinstance(object_id, str):
                continue
            body = self.client.request(
                "Runtime.getProperties",
                {"objectId": object_id, "ownProperties": True, "accessorPropertiesOnly": False},
            )
            for item in body.get("result", []):
                name = item.get("name")
                value = item.get("value")
                if not isinstance(name, str) or name in seen or not isinstance(value, dict):
                    continue
                seen.add(name)
                locals_.append(
                    {
                        "name": name,
                        "value": _remote_object_value(value),
                        "type": value.get("type"),
                    }
                )
                if len(locals_) >= limit:
                    return {"stack": stack, "frame": self._frame_summary(frame), "locals": locals_}
        return {"stack": stack, "frame": self._frame_summary(frame), "locals": locals_}

    def stop(self, terminate_debuggee: bool = True) -> dict[str, Any]:
        try:
            self.client.close()
        except Exception:
            pass
        if terminate_debuggee and self.process is not None and self.process.poll() is None:
            try:
                os.killpg(os.getpgid(self.process.pid), signal.SIGTERM)
            except (AttributeError, OSError):
                self.process.terminate()
            try:
                self.process.wait(timeout=5.0)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(os.getpgid(self.process.pid), signal.SIGKILL)
                except (AttributeError, OSError):
                    self.process.kill()
                self.process.wait(timeout=5.0)
        self.state = "terminated"
        return {"sessionId": self.session_id, "state": self.state}

    def _initialize(self, timeout: float) -> None:
        self.client.request("Runtime.enable", timeout=timeout)
        self.client.request("Debugger.enable", timeout=timeout)

    def _wait_for_relevant_pause(self, timeout: float, after: int, stop_on_entry: bool) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        cursor = after
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("timed out waiting for breakpoint")
            event = self.client.wait_event("Debugger.paused", timeout=remaining, after=cursor, process=self.process)
            cursor = self.client.event_count()
            if stop_on_entry or self._paused_at_breakpoint(event):
                return self._event_result(event)
            self._event_result(event)
            self.client.request("Debugger.resume", timeout=remaining)

    def _paused_at_breakpoint(self, event: dict[str, Any]) -> bool:
        params = event.get("params") if isinstance(event.get("params"), dict) else {}
        hit_breakpoints = params.get("hitBreakpoints")
        if isinstance(hit_breakpoints, list) and hit_breakpoints:
            return True
        call_frames = params.get("callFrames")
        if not isinstance(call_frames, list) or not call_frames:
            return False
        top = call_frames[0]
        if not isinstance(top, dict):
            return False
        location = top.get("location") if isinstance(top.get("location"), dict) else {}
        line = location.get("lineNumber")
        url = top.get("url")
        if not isinstance(url, str) or not url:
            script_id = location.get("scriptId")
            url = self._script_url(script_id) if isinstance(script_id, str) else ""
        for breakpoint in self.breakpoints:
            if breakpoint.get("url") == url and breakpoint.get("line") == int(line or -1) + 1:
                return True
        return False

    def _event_result(self, event: dict[str, Any]) -> dict[str, Any]:
        params = event.get("params") if isinstance(event.get("params"), dict) else {}
        call_frames = params.get("callFrames") if isinstance(params.get("callFrames"), list) else []
        self.stopped_call_frames = [frame for frame in call_frames if isinstance(frame, dict)]
        self.state = "stopped"
        result = {
            "sessionId": self.session_id,
            "state": self.state,
            "event": "stopped",
            "stoppedReason": params.get("reason"),
            "threadId": None,
        }
        if self.stopped_call_frames:
            result["location"] = self._frame_summary(self.stopped_call_frames[0])
        return result

    def _frame_summary(self, frame: dict[str, Any]) -> dict[str, Any]:
        location = frame.get("location") if isinstance(frame.get("location"), dict) else {}
        url = frame.get("url") if isinstance(frame.get("url"), str) else ""
        if not url:
            script_id = location.get("scriptId")
            if isinstance(script_id, str):
                url = self._script_url(script_id)
        function_name = frame.get("functionName") if isinstance(frame.get("functionName"), str) else ""
        return {
            "id": frame.get("callFrameId"),
            "name": function_name or "(anonymous)",
            "line": int(location.get("lineNumber", -1)) + 1,
            "column": int(location.get("columnNumber", -1)) + 1,
            "source": {
                "name": Path(_file_url_to_path(url)).name if url else None,
                "path": _file_url_to_path(url) if url else None,
            },
        }

    def _script_url(self, script_id: str) -> str:
        for event in reversed(self.client._events):
            if event.get("method") != "Debugger.scriptParsed":
                continue
            params = event.get("params") if isinstance(event.get("params"), dict) else {}
            if params.get("scriptId") == script_id and isinstance(params.get("url"), str):
                return params["url"]
        return ""


class NodeDebugSessionManager:
    def __init__(self) -> None:
        self._sessions: dict[str, NodeDebugSession] = {}

    def launch(self, **kwargs: Any) -> dict[str, Any]:
        session = NodeDebugSession.launch(**kwargs)
        self._sessions[session.session_id] = session
        return {
            "sessionId": session.session_id,
            "state": session.state,
            **session.metadata,
        }

    def attach(self, **kwargs: Any) -> dict[str, Any]:
        session = NodeDebugSession.attach(**kwargs)
        self._sessions[session.session_id] = session
        return {
            "sessionId": session.session_id,
            "state": session.state,
            **session.metadata,
        }

    def get(self, session_id: str) -> NodeDebugSession:
        session = self._sessions.get(session_id)
        if session is None:
            raise NodeDebugSessionError(f"unknown node debug session: {session_id}")
        return session

    def has(self, session_id: str) -> bool:
        return session_id in self._sessions

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
