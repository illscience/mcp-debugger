# MCP Debugger

An MCP debugger server that lets coding agents inspect live Python runtime state instead of guessing from source code.

`mcp-debugger` gives Codex, Claude Code, Cursor-style agents, and other MCP clients real debugger tools: launch, attach, set breakpoints, continue, step into/out/over, inspect stack frames, read locals, expand variables, evaluate expressions, and stop sessions.

The goal is simple: when an agent is fixing a bug, it should be able to use a debugger the same way a human engineer would.

```text
coding agent
  -> MCP tool call
  -> mcp-debugger
  -> Debug Adapter Protocol
  -> debugpy
  -> your Python program
```

## Status

This is an alpha release focused on Python via [`debugpy`](https://github.com/microsoft/debugpy). It is already usable as a local MCP server and includes a runtime proof that drives a real debugger session end to end.

## Quick Start

Install from GitHub:

```bash
pipx install git+https://github.com/illscience/mcp-debugger.git
```

Or install locally from a checkout:

```bash
git clone https://github.com/illscience/mcp-debugger.git
cd mcp-debugger
python3 -m venv .venv
.venv/bin/python -m pip install -e .
```

Verify the install:

```bash
mcp-debugger doctor
```

Expected result:

```json
{
  "name": "mcp-debugger",
  "checks": [
    { "name": "debugpy import", "ok": true },
    { "name": "MCP initialize", "ok": true }
  ],
  "ok": true
}
```

## Add It To Codex

If installed with `pipx`:

```bash
codex mcp add mcp-debugger -- mcp-debugger-server
```

If running from a local checkout:

```bash
codex mcp add mcp-debugger -- /absolute/path/to/mcp-debugger/.venv/bin/mcp-debugger-server
```

You can print the exact command for your environment:

```bash
mcp-debugger install-snippet codex
```

Confirm Codex sees it:

```bash
codex mcp list
```

Then start a fresh Codex session and ask it to debug a Python repro:

```text
There is a bug in examples/buggy_discount.py. Figure out what is wrong and propose the fix.
```

For a direct smoke test:

```text
Use the mcp-debugger MCP tools to debug examples/buggy_discount.py. Start with debug_python_repro, set a breakpoint at the BREAK_MAIN_CALL line, inspect runtime locals, and explain the bug.
```

## Add It To Claude Code

If installed with `pipx`:

```bash
claude mcp add mcp-debugger -- mcp-debugger-server
```

Or print the command:

```bash
mcp-debugger install-snippet claude
```

## Generic MCP Config

```json
{
  "mcpServers": {
    "mcp-debugger": {
      "command": "mcp-debugger-server",
      "args": [],
      "env": {}
    }
  }
}
```

Print an environment-specific JSON snippet:

```bash
mcp-debugger install-snippet json
```

## What The Agent Sees

The MCP server exposes agent-friendly workflow tools and lower-level debugger primitives.

Workflow tools:

- `debug_guidance`: returns instructions that tell agents when to use the debugger.
- `debug_python_repro`: best first tool for a reproducible Python bug. It launches a Python script under `debugpy`, sets breakpoints, continues to the first stop, and returns stack plus top-frame locals.

Debugger primitives:

- `debug_launch`: launch a Python script under `debugpy`.
- `debug_attach`: attach to an existing `debugpy` listener.
- `debug_set_breakpoints`: set file/line breakpoints.
- `debug_continue`: continue until breakpoint, exception, process exit, or timeout.
- `debug_step`: step `over`, `into`, or `out`.
- `debug_stack`: inspect stack frames.
- `debug_scopes`: inspect scope handles for a frame.
- `debug_variables`: expand locals, globals, objects, lists, or dicts.
- `debug_evaluate`: evaluate an expression in a paused frame.
- `debug_stop`: disconnect and clean up a session.

## Runtime Proof

Run the end-to-end proof:

```bash
python tools/runtime_proof.py
```

If using the local venv:

```bash
.venv/bin/python tools/runtime_proof.py
```

The proof talks to the MCP server over stdio, launches `examples/buggy_discount.py` under `debugpy`, sets a breakpoint, continues to it, steps into and out of functions, inspects local variables, evaluates expressions in a paused frame, tests attach mode, and cleans up the session.

Expected output:

```json
{
  "ok": true,
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
    "debug_continue to exit"
  ],
  "bugEvidence": {
    "runtimeBuggyExpression": "119.85",
    "runtimeExpectedExpression": "102.0"
  }
}
```

## Example: What A Successful Agent Run Looks Like

Given this bug:

```python
def apply_discount(price, loyalty_level):
    rate = lookup_rate(loyalty_level)
    discounted = price - rate  # BUG: should subtract price * rate.
    return round(discounted, 2)
```

Codex can call `debug_python_repro`, stop at the breakpoint, step into `apply_discount`, inspect locals, and observe:

```text
price = 120.0
loyalty_level = 'gold'
rate = 0.15
discounted = 119.85
correct_total = 102.0
```

The resulting explanation is based on runtime state:

```text
The program subtracts the rate value itself, 0.15, from 120.0.
For a 15% discount it should subtract price * rate, which is 18.0.
The correct total is 102.0, not 119.85.
```

## How To Make Agents Use It Naturally

Copy this repo's [`AGENTS.md`](AGENTS.md) guidance into the target project, or print the guidance with:

```bash
mcp-debugger agent-instructions
```

The key instruction:

```text
When debugging Python runtime bugs, prefer observing live state with mcp-debugger instead of guessing from source alone.
```

The high-level `debug_python_repro` tool is intentionally named and described so agents can pick it before reaching for raw debugger operations.

## Development

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e .
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python tools/runtime_proof.py
```

Build a wheel:

```bash
.venv/bin/python -m pip wheel . -w /tmp/mcp-debugger-wheel
```

## Safety

`debug_evaluate` can execute code inside the target process. Treat it like running code in the debuggee. The server defaults to localhost debug adapter connections and cleans up launched sessions when the MCP server exits.

## Roadmap

- `debug_pytest_failure`: run a failing pytest test under the debugger automatically.
- Breakpoints by function name, symbol, marker comment, or exception type.
- Richer first-stop summaries with surrounding source and suggested next debugger actions.
- Node.js / Next.js support through the Node inspector or Chrome DevTools Protocol.

## License

MIT
