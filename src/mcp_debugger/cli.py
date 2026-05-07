from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

from . import __version__
from .agent_guidance import AGENT_USAGE_GUIDANCE


def _mcp_command() -> str:
    command = shutil.which("mcp-debugger-server")
    if command:
        return command
    for directory in (Path(sys.prefix) / "bin", Path(sys.executable).parent):
        sibling = directory / "mcp-debugger-server"
        if sibling.exists():
            return str(sibling)
    return "mcp-debugger-server"


def _doctor() -> int:
    checks: list[dict[str, object]] = []

    try:
        import debugpy  # noqa: F401

        checks.append({"name": "debugpy import", "ok": True})
    except Exception as exc:
        checks.append({"name": "debugpy import", "ok": False, "error": str(exc)})

    command = _mcp_command()
    try:
        process = subprocess.run(
            [
                command,
            ],
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
        ok = process.returncode == 0 and '"name":"mcp-debugger"' in process.stdout
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
        "name": "mcp-debugger",
        "version": __version__,
        "python": sys.executable,
        "checks": checks,
        "ok": all(bool(check["ok"]) for check in checks),
    }
    print(json.dumps(report, indent=2))
    return 0 if report["ok"] else 1


def _print_install(target: str) -> int:
    command = _mcp_command()
    if target == "codex":
        print(f"codex mcp add mcp-debugger -- {command}")
    elif target == "claude":
        print(f"claude mcp add mcp-debugger -- {command}")
    else:
        print(
            json.dumps(
                {
                    "mcpServers": {
                        "mcp-debugger": {
                            "command": command,
                            "args": [],
                            "env": {},
                        }
                    }
                },
                indent=2,
            )
        )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Utilities for the mcp-debugger MCP server.")
    parser.add_argument("--version", action="version", version=f"mcp-debugger {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("doctor", help="Verify debugpy and the MCP server entry point.")
    subparsers.add_parser("agent-instructions", help="Print recommended agent guidance.")
    install = subparsers.add_parser("install-snippet", help="Print an MCP install command or config snippet.")
    install.add_argument("target", choices=["codex", "claude", "json"], help="Snippet target.")

    args = parser.parse_args(argv)
    if args.command == "doctor":
        return _doctor()
    if args.command == "agent-instructions":
        print(AGENT_USAGE_GUIDANCE)
        return 0
    if args.command == "install-snippet":
        return _print_install(args.target)
    parser.error(f"unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
