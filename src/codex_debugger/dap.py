from __future__ import annotations

import json
import socket
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable


class DAPError(RuntimeError):
    """Raised when a Debug Adapter Protocol operation fails."""


def encode_dap_message(payload: dict[str, Any]) -> bytes:
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    header = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
    return header + body


def read_dap_message(stream) -> dict[str, Any]:
    headers: dict[str, str] = {}

    while True:
        line = stream.readline()
        if line == b"":
            raise EOFError("debug adapter closed the connection")
        if line in (b"\r\n", b"\n"):
            break
        name, _, value = line.decode("ascii").partition(":")
        headers[name.strip().lower()] = value.strip()

    content_length = headers.get("content-length")
    if content_length is None:
        raise DAPError(f"DAP message missing Content-Length header: {headers!r}")

    body = stream.read(int(content_length))
    if len(body) != int(content_length):
        raise EOFError("debug adapter closed while sending a message body")
    return json.loads(body.decode("utf-8"))


@dataclass(frozen=True)
class DAPEvent:
    event: str
    body: dict[str, Any]
    raw: dict[str, Any]


class DAPClient:
    def __init__(self, host: str, port: int, timeout: float = 10.0):
        self.host = host
        self.port = port
        self.timeout = timeout
        self._socket = socket.create_connection((host, port), timeout=timeout)
        self._stream = self._socket.makefile("rb")
        self._seq = 1
        self._condition = threading.Condition()
        self._responses: dict[int, dict[str, Any]] = {}
        self._events: list[DAPEvent] = []
        self._closed_error: BaseException | None = None
        self._reader = threading.Thread(target=self._read_loop, name="dap-reader", daemon=True)
        self._reader.start()

    def close(self) -> None:
        try:
            self._socket.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            self._socket.close()
        except OSError:
            pass

    def send_request(self, command: str, arguments: dict[str, Any] | None = None) -> int:
        with self._condition:
            request_seq = self._seq
            self._seq += 1

        payload = {
            "seq": request_seq,
            "type": "request",
            "command": command,
            "arguments": arguments or {},
        }
        self._socket.sendall(encode_dap_message(payload))
        return request_seq

    def request(
        self,
        command: str,
        arguments: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        request_seq = self.send_request(command, arguments)
        return self.wait_response(request_seq, timeout=timeout)

    def wait_response(self, request_seq: int, timeout: float | None = None) -> dict[str, Any]:
        deadline = time.monotonic() + (timeout if timeout is not None else self.timeout)
        with self._condition:
            while request_seq not in self._responses:
                self._raise_if_closed()
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(f"timed out waiting for DAP response {request_seq}")
                self._condition.wait(remaining)

            response = self._responses.pop(request_seq)

        if not response.get("success", False):
            message = response.get("message") or response.get("body", {}).get("error", {}).get("format")
            raise DAPError(f"DAP request failed: {message or response!r}")

        body = response.get("body")
        return body if isinstance(body, dict) else {}

    def wait_for_event(
        self,
        event_name: str | tuple[str, ...],
        predicate: Callable[[DAPEvent], bool] | None = None,
        timeout: float | None = None,
        after: int = 0,
    ) -> DAPEvent:
        names = (event_name,) if isinstance(event_name, str) else event_name
        deadline = time.monotonic() + (timeout if timeout is not None else self.timeout)

        with self._condition:
            start_index = after
            while True:
                for index in range(start_index, len(self._events)):
                    event = self._events[index]
                    if event.event in names and (predicate is None or predicate(event)):
                        return event
                start_index = len(self._events)

                self._raise_if_closed()
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    expected = ", ".join(names)
                    raise TimeoutError(f"timed out waiting for DAP event: {expected}")
                self._condition.wait(remaining)

    @property
    def events(self) -> list[DAPEvent]:
        with self._condition:
            return list(self._events)

    def event_count(self) -> int:
        with self._condition:
            return len(self._events)

    def _read_loop(self) -> None:
        try:
            while True:
                message = read_dap_message(self._stream)
                message_type = message.get("type")
                with self._condition:
                    if message_type == "response":
                        request_seq = int(message["request_seq"])
                        self._responses[request_seq] = message
                    elif message_type == "event":
                        body = message.get("body")
                        self._events.append(
                            DAPEvent(
                                event=str(message.get("event")),
                                body=body if isinstance(body, dict) else {},
                                raw=message,
                            )
                        )
                    self._condition.notify_all()
        except BaseException as exc:
            with self._condition:
                self._closed_error = exc
                self._condition.notify_all()

    def _raise_if_closed(self) -> None:
        if self._closed_error is not None:
            raise DAPError(f"DAP connection closed: {self._closed_error}") from self._closed_error
