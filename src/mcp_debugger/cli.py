from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

from . import __version__
from .agent_guidance import AGENT_USAGE_GUIDANCE, guidance_for_target


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
        print(f"codex mcp add mcp_debugger -- {command}")
    elif target == "claude":
        print(f"claude mcp add -s user mcp-debugger -- {command}")
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

    prompts = [_demo_prompt(item) for item in _targets(target)]
    print(
        json.dumps(
            {
                "project": str(root),
                "created": sorted(path.name for path in root.iterdir() if path.is_file()),
                "next": prompts,
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
    instructions = subparsers.add_parser("agent-instructions", help="Print recommended agent guidance.")
    instructions.add_argument("--target", choices=["generic", "claude", "codex"], default="generic")
    install = subparsers.add_parser("install-snippet", help="Print an MCP install command or config snippet.")
    install.add_argument("target", choices=["codex", "claude", "json"], help="Snippet target.")
    init_files = subparsers.add_parser("init-agent-files", help="Write CLAUDE.md and/or AGENTS.md guidance.")
    init_files.add_argument("--target", choices=["claude", "codex", "both"], default="both")
    init_files.add_argument("--directory", default=".")
    init_files.add_argument("--force", action="store_true")
    demo = subparsers.add_parser("demo-project", help="Create a tiny prompt-based debugger demo project.")
    demo.add_argument("directory", nargs="?", default=".")
    demo.add_argument("--target", choices=["claude", "codex", "both"], default="claude")
    demo.add_argument("--force", action="store_true")

    args = parser.parse_args(argv)
    if args.command == "doctor":
        return _doctor()
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
    if args.command == "demo-project":
        return _demo_project(args.target, args.directory, args.force)
    parser.error(f"unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
