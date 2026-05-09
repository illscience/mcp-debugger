from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from . import __version__
from .agent_guidance import AGENT_USAGE_GUIDANCE, guidance_for_target
from .session import DebugSession


NPX_PACKAGE_SPEC = "github:illscience/vibe-debug"
MCP_SERVER_NAME = "vibe-debug"


DEMO_BUG = """def lookup_discount_rate(customer_tier):
    rates = {
        "standard": 0.0,
        "silver": 0.10,
        "gold": 0.15,
    }
    return rates[customer_tier]


def invoice_total(subtotal, customer_tier):
    rate = lookup_discount_rate(customer_tier)
    total = subtotal - rate
    return round(total, 2)


def main():
    subtotal = 120.0
    customer_tier = "gold"
    expected_total = 102.0
    actual_total = invoice_total(subtotal, customer_tier)
    print(f"{customer_tier=} {subtotal=} {expected_total=} {actual_total=}")


if __name__ == "__main__":
    main()
"""


CLI_DISCOVERY_SKILL = """---
name: vibe-debug
description: "Use when the user asks to debug, fix, explain, verify, validate, or implement Python behavior where live runtime state could help. Trigger for reproducible Python bugs, failing Python tests/scripts, wrong output, unexpected exceptions, local web request behavior, or code-writing tasks where breakpoint locals, stack location, or expression evaluation would confirm what the code actually does. Use when a runnable Python command, script, test, or request exists or can be created. Do not use for non-Python bugs, pure style refactors, documentation-only tasks, or cases with no executable behavior to observe."
---

# Vibe Debug CLI

Use the `vibe-debug` CLI to observe live Python runtime state while debugging, verifying behavior, or writing code.

## Primary Command

```bash
npx -y github:illscience/vibe-debug debug-python <script.py> --break <file.py>:<line> --json
```

For web apps with a local request that reproduces the behavior, prefer:

```bash
npx -y github:illscience/vibe-debug debug-request <server.py> --url http://127.0.0.1:5000/path --break <file.py>:<line> --json
```

For an already-running process that was started with `python -m debugpy --listen 127.0.0.1:<port> --wait-for-client ...`, use:

```bash
npx -y github:illscience/vibe-debug attach-python --port <port> --break <file.py>:<line> --json
```

Pick a breakpoint at the suspicious calculation, branch, return, assertion, or exception site. If no breakpoint is known yet, run with `--stop-on-entry --json`, inspect the code, then run again with a more useful breakpoint.

## Useful Options

- `--break <file.py>:<line>`: set a line breakpoint before running. Repeat for multiple breakpoints.
- `--eval "<expr>"`: evaluate a side-effect-free expression in the paused frame. Repeat for multiple expressions.
- `--arg "<value>"`: pass one argument to the target program. Repeat for multiple program arguments.
- `--cwd <dir>`: run the target program from a specific working directory.
- `--url "<local-url>"`: with `debug-request`, send a local HTTP request after the server starts.
- `--trigger-url "<local-url>"`: with `attach-python`, send a local HTTP request after breakpoints are set.
- `--locals-limit <n>`: cap the number of locals returned.
- `--json`: return compact machine-readable output.

## Workflow

1. Find or create the smallest Python command, script, test, or request that exercises the behavior.
2. Use `debug-python` for scripts/tests, `debug-request` for local web requests, or `attach-python` for an existing debugpy listener.
3. Choose a breakpoint near the runtime behavior being diagnosed, verified, or implemented.
4. Run the selected command with `--json`.
5. Inspect `stopped`, `locals`, and `evaluations`.
6. Use observed runtime state to explain the bug, confirm the behavior, or guide the code change.

## Communication

Always tell the user when and how you are using the debugger. Before running it, state the debugger mode, the target script/test/request or attach port, and the breakpoint you chose. After it stops, state the stopped file, line, function, and the observed locals or evaluations that matter. If you decide not to use the debugger for a runnable Python behavior task, briefly state why.

## Example

```bash
npx -y github:illscience/vibe-debug debug-python buggy_invoice.py --break buggy_invoice.py:13 --eval "subtotal * (1 - rate)" --json
```

Use the result to explain what the program actually did, not only what the source appears to say.
"""


def _mcp_command() -> list[str]:
    command_json = os.environ.get("VIBE_DEBUG_SERVER_COMMAND_JSON")
    if command_json:
        try:
            command = json.loads(command_json)
        except json.JSONDecodeError as exc:
            raise SystemExit(f"Invalid VIBE_DEBUG_SERVER_COMMAND_JSON: {exc}") from exc
        if not isinstance(command, list) or not all(isinstance(item, str) for item in command):
            raise SystemExit("VIBE_DEBUG_SERVER_COMMAND_JSON must be a JSON array of strings.")
        return command

    command = shutil.which("vibe-debug-server")
    if command:
        return [command]
    for directory in (Path(sys.prefix) / "bin", Path(sys.executable).parent):
        sibling = directory / "vibe-debug-server"
        if sibling.exists():
            return [str(sibling)]
    return ["vibe-debug-server"]


def _doctor_report() -> dict[str, object]:
    checks: list[dict[str, object]] = []

    try:
        import debugpy  # noqa: F401

        checks.append({"name": "debugpy import", "ok": True})
    except Exception as exc:
        checks.append({"name": "debugpy import", "ok": False, "error": str(exc)})

    command = _mcp_command()
    try:
        process = subprocess.run(
            command,
            input='\n'.join(
                [
                    '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18"}}',
                    '{"jsonrpc":"2.0","method":"exit","params":{}}',
                    "",
                ]
            ),
            text=True,
            capture_output=True,
            timeout=10,
            check=False,
        )
        ok = process.returncode == 0 and '"name":"vibe-debug"' in process.stdout
        checks.append(
            {
                "name": "MCP initialize",
                "ok": ok,
                "command": command,
                "stdout": process.stdout.strip(),
                "stderr": process.stderr.strip(),
            }
        )
    except Exception as exc:
        checks.append({"name": "MCP initialize", "ok": False, "command": command, "error": str(exc)})

    report = {
        "name": "vibe-debug",
        "version": __version__,
        "python": sys.executable,
        "checks": checks,
        "ok": all(bool(check["ok"]) for check in checks),
    }
    return report


def _print_doctor_human(report: dict[str, object], quiet: bool) -> None:
    checks = report.get("checks")
    ok = bool(report.get("ok"))

    if quiet:
        status = "ok" if ok else "failed"
        print(f"{report.get('name', 'vibe-debug')}: {status}")
        return

    print(f"{report.get('name', 'vibe-debug')} {report.get('version', 'unknown')}")
    print(f"Python: {report.get('python', 'unknown')}")

    if not isinstance(checks, list):
        print("Checks: failed")
        return

    for check in checks:
        if not isinstance(check, dict):
            continue
        name = check.get("name", "check")
        if check.get("ok"):
            print(f"{name}: ok")
            continue

        detail = check.get("error") or check.get("stderr") or check.get("stdout")
        if isinstance(detail, str) and detail:
            print(f"{name}: failed - {detail}")
        else:
            print(f"{name}: failed")


def _doctor(json_output: bool = False, quiet: bool = False) -> int:
    report = _doctor_report()
    if json_output:
        print(json.dumps(report, indent=2))
    else:
        _print_doctor_human(report, quiet=quiet)
    return 0 if report["ok"] else 1


def _print_install(target: str) -> int:
    if target == "codex":
        print(f"codex mcp add vibe_debug -- npx -y {NPX_PACKAGE_SPEC}")
    elif target == "claude":
        print(f"claude mcp add -s user vibe-debug -- npx -y {NPX_PACKAGE_SPEC}")
    else:
        print(
            json.dumps(
                {
                    "mcpServers": {
                        "vibe-debug": {
                            "command": "npx",
                            "args": ["-y", NPX_PACKAGE_SPEC],
                            "env": {},
                        }
                    }
                },
                indent=2,
            )
        )
    return 0


def _targets(value: str) -> list[str]:
    if value == "both":
        return ["claude", "codex"]
    return [value]


def _agent_filename(target: str) -> str:
    if target == "claude":
        return "CLAUDE.md"
    if target == "codex":
        return "AGENTS.md"
    raise ValueError(f"unknown target: {target}")


def _write_file(path: Path, contents: str, force: bool) -> None:
    if path.exists() and not force:
        raise SystemExit(f"{path} already exists. Re-run with --force to overwrite it.")
    path.write_text(contents, encoding="utf-8")


def _relative_files(root: Path) -> list[str]:
    return sorted(path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file())


def _init_agent_files(target: str, directory: str, force: bool) -> int:
    root = Path(directory).resolve()
    root.mkdir(parents=True, exist_ok=True)
    written: list[str] = []

    for item in _targets(target):
        path = root / _agent_filename(item)
        _write_file(path, guidance_for_target(item), force=force)
        written.append(str(path))

    print(json.dumps({"written": written}, indent=2))
    return 0


def _skill_path(target: str, directory: str) -> Path:
    root = Path(directory).resolve()
    if target == "claude":
        return root / ".claude" / "skills" / "vibe-debug" / "SKILL.md"
    if target == "codex":
        return root / ".codex" / "skills" / "vibe-debug" / "SKILL.md"
    raise ValueError(f"unknown target: {target}")


def _init_cli_skill(target: str, directory: str, force: bool) -> int:
    written: list[str] = []
    for item in _targets(target):
        path = _skill_path(item, directory)
        path.parent.mkdir(parents=True, exist_ok=True)
        _write_file(path, CLI_DISCOVERY_SKILL, force=force)
        written.append(str(path))

    print(json.dumps({"written": written}, indent=2))
    return 0


def _demo_prompt(target: str) -> str:
    if target == "claude":
        return 'claude -p "There is a bug in buggy_invoice.py. Figure out what is wrong and propose the fix. Do not edit files."'
    if target == "codex":
        return 'codex exec "There is a bug in buggy_invoice.py. Figure out what is wrong and propose the fix. Do not edit files."'
    return 'Ask your coding agent: There is a bug in buggy_invoice.py. Figure out what is wrong and propose the fix. Do not edit files.'


def _demo_project(target: str, directory: str, force: bool) -> int:
    root = Path(directory).resolve()
    root.mkdir(parents=True, exist_ok=True)
    _write_file(root / "buggy_invoice.py", DEMO_BUG, force=force)

    for item in _targets(target):
        _write_file(root / _agent_filename(item), guidance_for_target(item), force=force)
        skill_path = _skill_path(item, str(root))
        skill_path.parent.mkdir(parents=True, exist_ok=True)
        _write_file(skill_path, CLI_DISCOVERY_SKILL, force=force)

    prompts = [_demo_prompt(item) for item in _targets(target)]
    print(
        json.dumps(
            {
                "project": str(root),
                "created": _relative_files(root),
                "next": prompts,
            },
            indent=2,
        )
    )
    return 0


def _parse_breakpoint(value: str) -> dict[str, object]:
    file_name, separator, line_text = value.rpartition(":")
    if not separator or not file_name or not line_text:
        raise argparse.ArgumentTypeError("breakpoints must look like path/to/file.py:12")
    try:
        line = int(line_text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"breakpoint line must be an integer: {value}") from exc
    if line <= 0:
        raise argparse.ArgumentTypeError(f"breakpoint line must be positive: {value}")
    return {"file": file_name, "line": line}


def _source_location(location: object) -> str:
    if not isinstance(location, dict):
        return "unknown"
    source = location.get("source")
    path = ""
    if isinstance(source, dict):
        path = str(source.get("path") or source.get("name") or "")
    line = location.get("line")
    name = location.get("name")
    if path and isinstance(line, int) and isinstance(name, str):
        return f"{Path(path).name}:{line} in {name}"
    if path and isinstance(line, int):
        return f"{Path(path).name}:{line}"
    if isinstance(name, str):
        return name
    return "unknown"


def _location_summary(location: object) -> dict[str, object]:
    if not isinstance(location, dict):
        return {}
    source = location.get("source")
    path = ""
    if isinstance(source, dict):
        path = str(source.get("path") or "")
    return {
        "file": path,
        "line": location.get("line"),
        "function": location.get("name"),
    }


def _breakpoint_summaries(results: list[dict[str, object]]) -> list[dict[str, object]]:
    summaries: list[dict[str, object]] = []
    for result in results:
        file_name = result.get("file")
        breakpoints = result.get("breakpoints")
        if not isinstance(file_name, str) or not isinstance(breakpoints, list):
            continue
        for breakpoint in breakpoints:
            if not isinstance(breakpoint, dict):
                continue
            summaries.append(
                {
                    "file": file_name,
                    "line": breakpoint.get("line"),
                    "verified": bool(breakpoint.get("verified")),
                }
            )
    return summaries


def _local_summaries(snapshot: dict[str, object]) -> list[dict[str, object]]:
    locals_ = snapshot.get("locals")
    if not isinstance(locals_, list):
        return []
    summaries: list[dict[str, object]] = []
    for item in locals_:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        value = item.get("value")
        if not isinstance(name, str) or not isinstance(value, str):
            continue
        summary: dict[str, object] = {"name": name, "value": value}
        type_name = item.get("type")
        if isinstance(type_name, str):
            summary["type"] = type_name
        summaries.append(summary)
    return summaries


def _parse_env_assignment(value: str) -> tuple[str, str]:
    key, separator, env_value = value.partition("=")
    if not separator or not key:
        raise argparse.ArgumentTypeError("environment values must look like KEY=VALUE")
    return key, env_value


def _parse_path_mapping(value: str) -> dict[str, str]:
    local_root, separator, remote_root = value.partition("=")
    if not separator or not local_root or not remote_root:
        raise argparse.ArgumentTypeError("path mappings must look like LOCAL_ROOT=REMOTE_ROOT")
    return {"localRoot": local_root, "remoteRoot": remote_root}


def _env_dict(values: list[tuple[str, str]] | None) -> dict[str, str]:
    return {key: value for key, value in values or []}


def _evaluation_summaries(session: DebugSession, expressions: list[str]) -> list[dict[str, object]]:
    evaluations: list[dict[str, object]] = []
    for expression in expressions:
        try:
            result = session.evaluate(expression=expression)
            evaluations.append(
                {
                    "expression": expression,
                    "result": result.get("result"),
                    "type": result.get("type"),
                }
            )
        except Exception as exc:
            evaluations.append(
                {
                    "expression": expression,
                    "error": str(exc),
                    "exceptionType": type(exc).__name__,
                }
            )
    return evaluations


def _stopped_summary(stopped: dict[str, object]) -> dict[str, object]:
    summary: dict[str, object] = {
        "state": stopped.get("state"),
        "event": stopped.get("event"),
        "reason": stopped.get("stoppedReason"),
    }
    summary.update(_location_summary(stopped.get("location")))
    return summary


def _request_url(url: str, timeout: float) -> dict[str, object]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            body = response.read(1024).decode("utf-8", errors="replace")
            return {
                "ok": True,
                "url": url,
                "status": response.status,
                "bodyPreview": body,
            }
    except urllib.error.HTTPError as exc:
        body = exc.read(1024).decode("utf-8", errors="replace")
        return {
            "ok": True,
            "url": url,
            "status": exc.code,
            "bodyPreview": body,
        }


def _start_url_trigger(url: str, delay: float, timeout: float) -> tuple[threading.Thread, dict[str, object]]:
    result: dict[str, object] = {"kind": "url", "state": "pending", "url": url}

    def run() -> None:
        if delay > 0:
            time.sleep(delay)

        deadline = time.monotonic() + timeout
        attempts = 0
        last_error: BaseException | None = None

        while time.monotonic() < deadline:
            attempts += 1
            result.update({"state": "running", "attempts": attempts})
            try:
                request_timeout = min(1.0, max(0.1, deadline - time.monotonic()))
                result.update(_request_url(url, timeout=request_timeout))
                result["state"] = "completed"
                return
            except Exception as exc:
                last_error = exc
                time.sleep(0.1)

        result.update(
            {
                "state": "failed",
                "ok": False,
                "attempts": attempts,
                "error": str(last_error) if last_error else "trigger timed out",
                "exceptionType": type(last_error).__name__ if last_error else "TimeoutError",
            }
        )

    thread = threading.Thread(target=run, name="vibe-debug-url-trigger", daemon=True)
    thread.start()
    return thread, result


def _start_command_trigger(command: str, cwd: str | None, delay: float, timeout: float) -> tuple[threading.Thread, dict[str, object]]:
    result: dict[str, object] = {"kind": "command", "state": "pending", "command": command}

    def run() -> None:
        if delay > 0:
            time.sleep(delay)

        result["state"] = "running"
        try:
            process = subprocess.run(
                shlex.split(command),
                cwd=cwd,
                text=True,
                capture_output=True,
                timeout=timeout,
                check=False,
            )
            result.update(
                {
                    "state": "completed",
                    "ok": process.returncode == 0,
                    "returncode": process.returncode,
                    "stdoutPreview": process.stdout[:1024],
                    "stderrPreview": process.stderr[:1024],
                }
            )
        except Exception as exc:
            result.update(
                {
                    "state": "failed",
                    "ok": False,
                    "error": str(exc),
                    "exceptionType": type(exc).__name__,
                }
            )

    thread = threading.Thread(target=run, name="vibe-debug-command-trigger", daemon=True)
    thread.start()
    return thread, result


def _collect_stopped_state(
    session: DebugSession,
    stopped: dict[str, object],
    locals_limit: int,
    expressions: list[str],
) -> tuple[dict[str, object], list[dict[str, object]]]:
    snapshot: dict[str, object] = {}
    evaluations: list[dict[str, object]] = []
    if stopped.get("state") == "stopped":
        snapshot = session.top_frame_locals(limit=locals_limit)
        evaluations = _evaluation_summaries(session, expressions)
    return snapshot, evaluations


def _debug_python_payload(args: argparse.Namespace) -> dict[str, object]:
    breakpoints = args.breakpoints or []
    stop_on_entry = bool(args.stop_on_entry or not breakpoints)
    session: DebugSession | None = None

    try:
        session = DebugSession.launch(
            program=args.program,
            args=args.program_args or [],
            cwd=args.cwd,
            python=args.python,
            stop_on_entry=stop_on_entry,
            timeout=float(args.timeout),
        )
        breakpoint_results = [
            session.set_breakpoints(file=str(item["file"]), lines=[int(item["line"])], cwd=args.cwd)
            for item in breakpoints
        ]
        stopped = session.continue_execution(timeout=float(args.timeout))
        snapshot: dict[str, object] = {}
        evaluations: list[dict[str, object]] = []

        if stopped.get("state") == "stopped":
            snapshot = session.top_frame_locals(limit=int(args.locals_limit))
            evaluations = _evaluation_summaries(session, args.evaluate or [])

        return {
            "ok": True,
            "program": str(Path(args.program).resolve()),
            "cwd": str(Path(args.cwd or os.getcwd()).resolve()),
            "breakpoints": _breakpoint_summaries(breakpoint_results),
            "stopped": _stopped_summary(stopped),
            "locals": _local_summaries(snapshot),
            "evaluations": evaluations,
        }
    finally:
        if session is not None:
            try:
                session.stop(terminate_debuggee=True)
            except Exception:
                pass


def _print_debug_python_human(payload: dict[str, object]) -> None:
    stopped = payload.get("stopped")
    if isinstance(stopped, dict) and stopped.get("state") == "stopped":
        location = {
            "source": {"path": stopped.get("file")},
            "line": stopped.get("line"),
            "name": stopped.get("function"),
        }
        print(f"Stopped: {_source_location(location)}")
        reason = stopped.get("reason")
        if isinstance(reason, str):
            print(f"Reason: {reason}")
    elif isinstance(stopped, dict):
        event = stopped.get("event") or stopped.get("state")
        print(f"Stopped: no ({event})")
    else:
        print("Stopped: no")

    locals_ = []
    maybe_locals = payload.get("locals")
    if isinstance(maybe_locals, list):
        locals_ = [item for item in maybe_locals if isinstance(item, dict)]

    if locals_:
        print("Locals:")
        for item in locals_:
            name = item.get("name")
            value = item.get("value")
            if isinstance(name, str) and isinstance(value, str):
                print(f"  {name} = {value}")
    else:
        print("Locals: none")

    evaluations = payload.get("evaluations")
    if isinstance(evaluations, list) and evaluations:
        print("Evaluations:")
        for item in evaluations:
            if not isinstance(item, dict):
                continue
            expression = item.get("expression")
            if not isinstance(expression, str):
                continue
            if "error" in item:
                print(f"  {expression} -> ERROR: {item.get('error')}")
            else:
                print(f"  {expression} -> {item.get('result')}")


def _debug_python(args: argparse.Namespace) -> int:
    try:
        payload = _debug_python_payload(args)
    except Exception as exc:
        if args.json:
            print(
                json.dumps(
                    {
                        "ok": False,
                        "error": str(exc),
                        "exceptionType": type(exc).__name__,
                    },
                    indent=2,
                )
            )
        else:
            print(f"debug-python failed: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        _print_debug_python_human(payload)
    return 0


def _debug_request_payload(args: argparse.Namespace) -> dict[str, object]:
    breakpoints = args.breakpoints or []
    session: DebugSession | None = None
    trigger_thread: threading.Thread | None = None
    trigger_result: dict[str, object] = {}
    cwd = args.cwd or os.getcwd()

    try:
        session = DebugSession.launch(
            program=args.program,
            args=args.program_args or [],
            cwd=args.cwd,
            python=args.python,
            env=_env_dict(args.env),
            stop_on_entry=bool(args.stop_on_entry or not breakpoints),
            timeout=float(args.timeout),
        )
        breakpoint_results = [
            session.set_breakpoints(file=str(item["file"]), lines=[int(item["line"])], cwd=args.cwd)
            for item in breakpoints
        ]
        trigger_thread, trigger_result = _start_url_trigger(
            url=args.url,
            delay=float(args.trigger_delay),
            timeout=float(args.trigger_timeout or args.timeout),
        )
        stopped = session.continue_execution(timeout=float(args.timeout))
        snapshot, evaluations = _collect_stopped_state(
            session=session,
            stopped=stopped,
            locals_limit=int(args.locals_limit),
            expressions=args.evaluate or [],
        )
        trigger_thread.join(timeout=0.2)

        return {
            "ok": True,
            "mode": "debug-request",
            "program": str(Path(args.program).resolve()),
            "cwd": str(Path(cwd).resolve()),
            "url": args.url,
            "breakpoints": _breakpoint_summaries(breakpoint_results),
            "stopped": _stopped_summary(stopped),
            "locals": _local_summaries(snapshot),
            "evaluations": evaluations,
            "trigger": dict(trigger_result),
        }
    finally:
        if session is not None:
            try:
                session.stop(terminate_debuggee=True)
            except Exception:
                pass


def _debug_request(args: argparse.Namespace) -> int:
    try:
        payload = _debug_request_payload(args)
    except Exception as exc:
        if args.json:
            print(
                json.dumps(
                    {
                        "ok": False,
                        "mode": "debug-request",
                        "error": str(exc),
                        "exceptionType": type(exc).__name__,
                    },
                    indent=2,
                )
            )
        else:
            print(f"debug-request failed: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        _print_debug_python_human(payload)
    return 0


def _attach_python_payload(args: argparse.Namespace) -> dict[str, object]:
    session: DebugSession | None = None
    trigger_thread: threading.Thread | None = None
    trigger_result: dict[str, object] = {}

    try:
        session = DebugSession.attach(
            host=args.host,
            port=int(args.port),
            timeout=float(args.timeout),
            path_mappings=args.path_mappings,
        )
        breakpoint_results = [
            session.set_breakpoints(file=str(item["file"]), lines=[int(item["line"])], cwd=args.cwd)
            for item in args.breakpoints or []
        ]

        if args.trigger_url:
            trigger_thread, trigger_result = _start_url_trigger(
                url=args.trigger_url,
                delay=float(args.trigger_delay),
                timeout=float(args.trigger_timeout or args.timeout),
            )
        elif args.trigger_command:
            trigger_thread, trigger_result = _start_command_trigger(
                command=args.trigger_command,
                cwd=args.cwd,
                delay=float(args.trigger_delay),
                timeout=float(args.trigger_timeout or args.timeout),
            )

        stopped = session.continue_execution(timeout=float(args.timeout))
        snapshot, evaluations = _collect_stopped_state(
            session=session,
            stopped=stopped,
            locals_limit=int(args.locals_limit),
            expressions=args.evaluate or [],
        )
        if trigger_thread is not None:
            trigger_thread.join(timeout=0.2)

        return {
            "ok": True,
            "mode": "attach-python",
            "host": args.host,
            "port": int(args.port),
            "breakpoints": _breakpoint_summaries(breakpoint_results),
            "stopped": _stopped_summary(stopped),
            "locals": _local_summaries(snapshot),
            "evaluations": evaluations,
            "trigger": dict(trigger_result) if trigger_result else None,
        }
    finally:
        if session is not None:
            try:
                session.stop(terminate_debuggee=bool(args.terminate_debuggee))
            except Exception:
                pass


def _attach_python(args: argparse.Namespace) -> int:
    try:
        payload = _attach_python_payload(args)
    except Exception as exc:
        if args.json:
            print(
                json.dumps(
                    {
                        "ok": False,
                        "mode": "attach-python",
                        "error": str(exc),
                        "exceptionType": type(exc).__name__,
                    },
                    indent=2,
                )
            )
        else:
            print(f"attach-python failed: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        _print_debug_python_human(payload)
    return 0


def _load_json_maybe(value: object) -> object | None:
    if isinstance(value, dict):
        return value
    if not isinstance(value, str):
        return None
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return None


def _debugger_tool_name(name: str) -> str:
    prefix = f"mcp__{MCP_SERVER_NAME}__"
    if name.startswith(prefix):
        return f"{MCP_SERVER_NAME}.{name.removeprefix(prefix)}"
    return name


def _is_debugger_tool_name(name: str) -> bool:
    return name.startswith(f"mcp__{MCP_SERVER_NAME}__")


def _basename(path: object) -> str:
    if not isinstance(path, str) or not path:
        return ""
    return Path(path).name


def _format_tool_use(name: str, tool_input: object) -> str:
    if not isinstance(tool_input, dict):
        return f"Tool: {_debugger_tool_name(name)}"

    if name.endswith("__debug_python_repro"):
        program = _basename(tool_input.get("program"))
        if program:
            return f"Tool: {_debugger_tool_name(name)} ({program})"
    if name.endswith("__debug_evaluate"):
        expression = tool_input.get("expression")
        if isinstance(expression, str):
            return f"Tool: {_debugger_tool_name(name)} ({expression})"
    if name.endswith("__debug_stop"):
        return f"Tool: {_debugger_tool_name(name)}"
    if name == "Read":
        file_path = _basename(tool_input.get("file_path"))
        if file_path:
            return f"Tool: Read ({file_path})"
    if name == "Bash":
        command = tool_input.get("command")
        if isinstance(command, str) and "vibe-debug" in command and "debug-python" in command:
            return "Tool: Bash (vibe-debug debug-python)"
        description = tool_input.get("description")
        if isinstance(description, str) and description:
            return f"Tool: Bash ({description})"
    if name == "ToolSearch":
        return "Tool: ToolSearch"
    return f"Tool: {_debugger_tool_name(name)}"


def _format_locals(locals_: object) -> str | None:
    if not isinstance(locals_, list):
        return None
    values: list[str] = []
    for item in locals_:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        value = item.get("value")
        if isinstance(name, str) and isinstance(value, str):
            values.append(f"{name}={value}")
    if not values:
        return None
    return "Locals: " + " ".join(values)


def _format_debugger_result(payload: object, tool_name: str | None) -> list[str]:
    data = _load_json_maybe(payload)
    if not isinstance(data, dict):
        return []

    lines: list[str] = []
    stopped = data.get("stopped")
    if isinstance(stopped, dict):
        location = stopped.get("location")
        if isinstance(location, dict):
            source = location.get("source")
            file_name = ""
            if isinstance(source, dict):
                file_name = _basename(source.get("path"))
            name = location.get("name")
            line = location.get("line")
            if isinstance(name, str) and isinstance(line, int):
                prefix = f"{file_name}:" if file_name else ""
                lines.append(f"Stopped: {prefix}{line} in {name}")
        else:
            file_name = _basename(stopped.get("file"))
            name = stopped.get("function")
            line = stopped.get("line")
            if isinstance(name, str) and isinstance(line, int):
                prefix = f"{file_name}:" if file_name else ""
                lines.append(f"Stopped: {prefix}{line} in {name}")

    snapshot = data.get("snapshot")
    if isinstance(snapshot, dict):
        locals_line = _format_locals(snapshot.get("locals"))
        if locals_line:
            lines.append(locals_line)
    else:
        locals_line = _format_locals(data.get("locals"))
        if locals_line:
            lines.append(locals_line)

    expression = data.get("expression")
    result = data.get("result")
    if isinstance(expression, str) and isinstance(result, str):
        lines.append(f"Eval: {expression} -> {result}")

    evaluations = data.get("evaluations")
    if isinstance(evaluations, list):
        for item in evaluations:
            if not isinstance(item, dict):
                continue
            expression = item.get("expression")
            result = item.get("result")
            if isinstance(expression, str) and result is not None:
                lines.append(f"Eval: {expression} -> {result}")

    state = data.get("state")
    if tool_name and tool_name.endswith("__debug_stop") and isinstance(state, str):
        lines.append(f"Debug session: {state}")

    error = data.get("error")
    if isinstance(error, str):
        lines.append(f"Tool error: {error}")

    return lines


def _message_content_items(event: dict[str, object]) -> list[dict[str, object]]:
    message = event.get("message")
    if not isinstance(message, dict):
        return []
    content = message.get("content")
    if not isinstance(content, list):
        return []
    return [item for item in content if isinstance(item, dict)]


def _format_claude_stream(input_stream, output_stream) -> int:
    tool_names: dict[str, str] = {}
    pending_tool_errors: dict[str, list[str]] = {}
    debugger_status: str | None = None
    debugger_active_printed = False

    def flush_tool_errors(tool_name: str | None = None) -> None:
        names = [tool_name] if tool_name else list(pending_tool_errors)
        for name in names:
            if not name:
                continue
            errors = pending_tool_errors.pop(name, [])
            for error in errors:
                print(error, file=output_stream, flush=True)

    for raw_line in input_stream:
        line = raw_line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            print(line, file=output_stream, flush=True)
            continue
        if not isinstance(event, dict):
            continue

        event_type = event.get("type")
        if event_type == "system" and event.get("subtype") == "init":
            cwd = event.get("cwd")
            if isinstance(cwd, str):
                print(f"Working directory: {cwd}", file=output_stream, flush=True)
            mcp_servers = event.get("mcp_servers")
            if isinstance(mcp_servers, list):
                for server in mcp_servers:
                    if not isinstance(server, dict) or server.get("name") != MCP_SERVER_NAME:
                        continue
                    server_name = str(server.get("name"))
                    status = server.get("status")
                    if isinstance(status, str):
                        debugger_status = status
                        display_status = "starting" if status == "pending" else status
                    else:
                        display_status = "unknown"
                    print(f"MCP: {server_name} {display_status}", file=output_stream, flush=True)
            continue

        if event_type == "assistant":
            for item in _message_content_items(event):
                content_type = item.get("type")
                if content_type == "text":
                    flush_tool_errors()
                    text = item.get("text")
                    if isinstance(text, str) and text.strip():
                        print(text.strip(), file=output_stream, flush=True)
                elif content_type == "tool_use":
                    tool_id = item.get("id")
                    name = item.get("name")
                    if isinstance(tool_id, str) and isinstance(name, str):
                        tool_names[tool_id] = name
                    if isinstance(name, str):
                        if (
                            _is_debugger_tool_name(name)
                            and debugger_status != "connected"
                            and not debugger_active_printed
                        ):
                            print("MCP: vibe-debug active", file=output_stream, flush=True)
                            debugger_active_printed = True
                        print(_format_tool_use(name, item.get("input")), file=output_stream, flush=True)
            continue

        if event_type == "user":
            for item in _message_content_items(event):
                if item.get("type") != "tool_result":
                    continue
                tool_id = item.get("tool_use_id")
                tool_name = tool_names.get(tool_id) if isinstance(tool_id, str) else None
                if item.get("is_error"):
                    error_lines = [f"Tool error from {_debugger_tool_name(tool_name or 'unknown')}"]
                    for formatted in _format_debugger_result(item.get("content"), tool_name):
                        error_lines.append(formatted)
                    pending_tool_errors.setdefault(tool_name or "unknown", []).extend(error_lines)
                    continue
                content = item.get("content")
                if isinstance(content, list):
                    flush_tool_errors(tool_name)
                    if tool_name == "ToolSearch":
                        print("Debugger tools loaded", file=output_stream, flush=True)
                    continue
                formatted_lines = _format_debugger_result(content, tool_name)
                if formatted_lines:
                    pending_tool_errors.pop(tool_name or "unknown", None)
                for formatted in formatted_lines:
                    print(formatted, file=output_stream, flush=True)
            continue

        if event_type == "result":
            flush_tool_errors()
            subtype = event.get("subtype")
            duration_ms = event.get("duration_ms")
            turns = event.get("num_turns")
            summary: list[str] = [f"Done: {subtype}"] if isinstance(subtype, str) else ["Done"]
            if isinstance(turns, int):
                summary.append(f"{turns} turns")
            if isinstance(duration_ms, int):
                summary.append(f"{duration_ms / 1000:.1f}s")
            print(" | ".join(summary), file=output_stream, flush=True)

    flush_tool_errors()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Utilities for the vibe-debug MCP server.")
    parser.add_argument("--version", action="version", version=f"vibe-debug {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    doctor = subparsers.add_parser("doctor", help="Verify debugpy and the MCP server entry point.")
    doctor.add_argument("--json", action="store_true", help="Print the full machine-readable report.")
    doctor.add_argument("--quiet", action="store_true", help="Print only one status line.")
    instructions = subparsers.add_parser("agent-instructions", help="Print recommended agent guidance.")
    instructions.add_argument("--target", choices=["generic", "claude", "codex"], default="generic")
    install = subparsers.add_parser("install-snippet", help="Print an MCP install command or config snippet.")
    install.add_argument("target", choices=["codex", "claude", "json"], help="Snippet target.")
    init_files = subparsers.add_parser("init-agent-files", help="Write CLAUDE.md and/or AGENTS.md guidance.")
    init_files.add_argument("--target", choices=["claude", "codex", "both"], default="both")
    init_files.add_argument("--directory", default=".")
    init_files.add_argument("--force", action="store_true")
    init_skill = subparsers.add_parser("init-cli-skill", help="Write a project skill that teaches agents the debugger CLI.")
    init_skill.add_argument("--target", choices=["claude", "codex", "both"], default="claude")
    init_skill.add_argument("--directory", default=".")
    init_skill.add_argument("--force", action="store_true")
    demo = subparsers.add_parser("demo-project", help="Create a tiny prompt-based debugger demo project.")
    demo.add_argument("directory", nargs="?", default=".")
    demo.add_argument("--target", choices=["claude", "codex", "both"], default="claude")
    demo.add_argument("--force", action="store_true")
    debug_python = subparsers.add_parser(
        "debug-python",
        help="Run a Python script under the debugger and print stopped-frame state.",
    )
    debug_python.add_argument("program", help="Python script to launch under debugpy.")
    debug_python.add_argument(
        "--arg",
        dest="program_args",
        action="append",
        default=[],
        help="Argument passed to the program.",
    )
    debug_python.add_argument(
        "--break",
        "-b",
        dest="breakpoints",
        action="append",
        type=_parse_breakpoint,
        default=[],
        metavar="FILE:LINE",
        help="Set a line breakpoint before continuing. Repeat for multiple breakpoints.",
    )
    debug_python.add_argument("--cwd", help="Working directory for the target program.")
    debug_python.add_argument("--python", help="Python executable for debugpy and the target.")
    debug_python.add_argument("--timeout", type=float, default=20.0)
    debug_python.add_argument("--locals-limit", type=int, default=40)
    debug_python.add_argument("--stop-on-entry", action="store_true")
    debug_python.add_argument("--eval", dest="evaluate", action="append", default=[], help="Evaluate expression at stop.")
    debug_python.add_argument("--json", action="store_true", help="Print machine-readable JSON output.")
    debug_request = subparsers.add_parser(
        "debug-request",
        help="Launch a Python web app under the debugger, trigger a local URL, and print stopped-frame state.",
    )
    debug_request.add_argument("program", help="Python script that starts the local web server.")
    debug_request.add_argument("--url", required=True, help="Local URL to request after the debuggee starts.")
    debug_request.add_argument(
        "--arg",
        dest="program_args",
        action="append",
        default=[],
        help="Argument passed to the server program.",
    )
    debug_request.add_argument(
        "--break",
        "-b",
        dest="breakpoints",
        action="append",
        type=_parse_breakpoint,
        default=[],
        metavar="FILE:LINE",
        help="Set a line breakpoint before triggering the URL. Repeat for multiple breakpoints.",
    )
    debug_request.add_argument("--cwd", help="Working directory for the target program.")
    debug_request.add_argument("--python", help="Python executable for debugpy and the target.")
    debug_request.add_argument(
        "--env",
        action="append",
        type=_parse_env_assignment,
        default=[],
        metavar="KEY=VALUE",
        help="Environment variable to add to the target process. Repeat for multiple values.",
    )
    debug_request.add_argument("--timeout", type=float, default=20.0)
    debug_request.add_argument("--trigger-timeout", type=float, help="Timeout for the URL trigger. Defaults to --timeout.")
    debug_request.add_argument("--trigger-delay", type=float, default=0.25)
    debug_request.add_argument("--locals-limit", type=int, default=40)
    debug_request.add_argument("--stop-on-entry", action="store_true")
    debug_request.add_argument("--eval", dest="evaluate", action="append", default=[], help="Evaluate expression at stop.")
    debug_request.add_argument("--json", action="store_true", help="Print machine-readable JSON output.")
    attach_python = subparsers.add_parser(
        "attach-python",
        help="Attach to an existing debugpy listener, set breakpoints, optionally trigger work, and inspect state.",
    )
    attach_python.add_argument("--host", default="127.0.0.1", help="debugpy listener host.")
    attach_python.add_argument("--port", type=int, required=True, help="debugpy listener port.")
    attach_python.add_argument(
        "--break",
        "-b",
        dest="breakpoints",
        action="append",
        type=_parse_breakpoint,
        default=[],
        metavar="FILE:LINE",
        help="Set a line breakpoint before continuing. Repeat for multiple breakpoints.",
    )
    attach_python.add_argument("--cwd", help="Base directory for resolving relative breakpoint paths.")
    attach_python.add_argument(
        "--path-map",
        dest="path_mappings",
        action="append",
        type=_parse_path_mapping,
        default=[],
        metavar="LOCAL_ROOT=REMOTE_ROOT",
        help="Path mapping for remote/container attach. Repeat for multiple mappings.",
    )
    attach_python.add_argument("--trigger-url", help="URL to request after breakpoints are set.")
    attach_python.add_argument("--trigger-command", help="Command to run after breakpoints are set.")
    attach_python.add_argument("--trigger-timeout", type=float, help="Timeout for trigger work. Defaults to --timeout.")
    attach_python.add_argument("--trigger-delay", type=float, default=0.0)
    attach_python.add_argument("--timeout", type=float, default=20.0)
    attach_python.add_argument("--locals-limit", type=int, default=40)
    attach_python.add_argument("--eval", dest="evaluate", action="append", default=[], help="Evaluate expression at stop.")
    attach_python.add_argument(
        "--terminate-debuggee",
        action="store_true",
        help="Terminate the attached process on disconnect. By default attach detaches without terminating it.",
    )
    attach_python.add_argument("--json", action="store_true", help="Print machine-readable JSON output.")
    subparsers.add_parser("claude-progress", help="Format Claude Code stream-json output for humans.")

    args = parser.parse_args(argv)
    if args.command == "doctor":
        return _doctor(json_output=args.json, quiet=args.quiet)
    if args.command == "agent-instructions":
        if args.target == "generic":
            print(AGENT_USAGE_GUIDANCE)
        else:
            print(guidance_for_target(args.target))
        return 0
    if args.command == "install-snippet":
        return _print_install(args.target)
    if args.command == "init-agent-files":
        return _init_agent_files(args.target, args.directory, args.force)
    if args.command == "init-cli-skill":
        return _init_cli_skill(args.target, args.directory, args.force)
    if args.command == "demo-project":
        return _demo_project(args.target, args.directory, args.force)
    if args.command == "debug-python":
        return _debug_python(args)
    if args.command == "debug-request":
        return _debug_request(args)
    if args.command == "attach-python":
        return _attach_python(args)
    if args.command == "claude-progress":
        return _format_claude_stream(sys.stdin, sys.stdout)
    parser.error(f"unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
