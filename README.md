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
codex mcp add mcp_debugger -- mcp-debugger-server
```

If running from a local checkout:

```bash
codex mcp add mcp_debugger -- /absolute/path/to/mcp-debugger/.venv/bin/mcp-debugger-server
```

Codex's MCP config table names are safest with underscores, so the Codex config entry is `mcp_debugger` even though the project, package, and MCP server identify as `mcp-debugger`.

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
claude mcp add -s user mcp-debugger -- mcp-debugger-server
```

Or print the command:

```bash
mcp-debugger install-snippet claude
```

Confirm Claude Code can connect:

```bash
claude mcp get mcp-debugger
```

Expected:

```text
Status: ✓ Connected
Type: stdio
```

The `-s user` scope is intentional: it makes the MCP server available from fresh project directories. If you omit it, Claude Code defaults to local scope and the server is only available from the directory where you added it.

Claude Code loads project instructions from `./CLAUDE.md` ([Anthropic docs](https://docs.anthropic.com/en/docs/claude-code/memory)). `mcp-debugger` can create that file for you in a test project:

```bash
mcp-debugger init-agent-files --target claude
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

## Clean-Room Prompt Test

This is the fastest way to prove the MCP works the way people will actually use it: from a normal bug-fixing prompt, not from a Python test script and not by explicitly telling the agent which debugger tool to call.

Start in a fresh directory after installing the MCP:

```bash
mkdir /tmp/mcp-debugger-cleanroom
cd /tmp/mcp-debugger-cleanroom
mcp-debugger demo-project --target claude .
```

That creates:

- `buggy_invoice.py`: a tiny Python program with a real arithmetic bug.
- `CLAUDE.md`: Claude Code project memory that says to use live runtime debugging when a Python bug is reproducible.

Run a natural prompt:

```bash
claude -p "There is a bug in buggy_invoice.py. Figure out what is wrong and propose the fix. Do not edit files."
```

To prove from the transcript that Claude used the MCP debugger, run the same prompt in stream-json mode:

```bash
claude -p --output-format stream-json --verbose "There is a bug in buggy_invoice.py. Figure out what is wrong and propose the fix. Do not edit files." | tee /tmp/mcp-debugger-claude.jsonl
grep -E "mcp__mcp-debugger|debug_python_repro|mcp-debugger" /tmp/mcp-debugger-claude.jsonl
```

You should see `mcp-debugger` listed as connected and, on a successful debugger-assisted run, a tool call such as `mcp__mcp-debugger__debug_python_repro`.

What you want to see:

- Claude calls the `mcp-debugger` MCP server, usually starting with `debug_python_repro`.
- Claude reports runtime values such as `subtotal = 120.0`, `customer_tier = 'gold'`, and `rate = 0.15`.
- Claude explains that the program subtracts `0.15` directly instead of subtracting `120.0 * 0.15`.
- Claude proposes `total = subtotal * (1 - rate)` or `total = subtotal - (subtotal * rate)`.

You can run the same clean-room prompt test with Codex:

```bash
mkdir /tmp/mcp-debugger-codex
cd /tmp/mcp-debugger-codex
mcp-debugger demo-project --target codex .
codex exec "There is a bug in buggy_invoice.py. Figure out what is wrong and propose the fix. Do not edit files."
```

The key proof is the transcript: the agent should use debugger tools from a normal debugging request and cite observed runtime state in its answer.

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

## Scripted Runtime Proof

The repository also includes a deterministic proof script for CI and development:

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

MCP makes the debugger available, but the agent still needs a workflow preference that says runtime bugs should be investigated with live runtime state when possible.

For Claude Code, use `CLAUDE.md`. Anthropic documents `./CLAUDE.md` as project memory that Claude Code loads automatically.

For Codex, use `AGENTS.md`.

Create the right file in a target project:

```bash
mcp-debugger init-agent-files --target claude
mcp-debugger init-agent-files --target codex
mcp-debugger init-agent-files --target both
```

Or print the guidance:

```bash
mcp-debugger agent-instructions --target claude
mcp-debugger agent-instructions --target codex
```

The key instruction:

```text
When a Python bug has a reproducible script, test, command, or request, prefer observing live runtime state before proposing a fix.
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
