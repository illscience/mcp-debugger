# MCP Debugger

A debugger MCP server for coding agents.

`mcp-debugger` gives Codex, Claude Code, Cursor-style agents, and other MCP clients real debugger tools: launch, attach, set breakpoints, continue, step into/out/over, inspect stack frames, read locals, expand variables, evaluate expressions, and stop sessions.

The goal is simple: when an agent is fixing a bug, it should be able to use a debugger the same way a human engineer would.

```text
coding agent
  -> MCP tool call
  -> mcp-debugger
  -> Debug Adapter Protocol
  -> language debugger backend
  -> your app
```

## Install + Verify

### Claude Code

Paste this whole block. It resets stale MCP config, installs the server, creates a fresh demo project, and asks Claude to debug it:

```bash
# Install or reset the MCP server at user scope.
claude mcp remove mcp-debugger -s local 2>/dev/null || true
claude mcp remove mcp-debugger -s user 2>/dev/null || true
claude mcp add -s user mcp-debugger -- npx -y github:illscience/mcp-debugger
claude mcp get mcp-debugger

# Create a disposable demo project.
rm -rf /tmp/mcp-debugger-claude-verify
mkdir /tmp/mcp-debugger-claude-verify
cd /tmp/mcp-debugger-claude-verify
npx -y github:illscience/mcp-debugger demo-project --target claude .
ls

# Verify from a normal prompt with readable live progress.
claude -p --output-format stream-json --verbose "There is a bug in buggy_invoice.py. Figure out what is wrong and propose the fix. Do not edit files." | npx -y github:illscience/mcp-debugger claude-progress
```

Expected MCP install output includes:

```text
Status: ✓ Connected
Type: stdio
Command: npx
```

Expected Claude result:

```text
MCP: mcp-debugger connected
Tool: mcp-debugger.debug_python_repro (buggy_invoice.py)
Locals: customer_tier='gold' rate=0.15 subtotal=120.0 total=119.85
Eval: subtotal * (1 - rate) -> 102.0
The bug is in invoice_total: it subtracts the raw rate 0.15 from 120.0, producing 119.85.
It should subtract subtotal * rate, producing 102.0.
```

The remove commands are intentional. They clear old local or user entries that may point at a stale executable such as `mcp-debugger-server`.

Claude Code loads project instructions from `./CLAUDE.md` ([Anthropic docs](https://docs.anthropic.com/en/docs/claude-code/memory)). `mcp-debugger` can create that file for you in a test project; see the clean-room test below.

### Codex

Paste this whole block. It resets stale MCP config, installs the server, creates a fresh demo project, and asks Codex to debug it:

```bash
codex mcp remove mcp_debugger 2>/dev/null || true
codex mcp remove codex-debugger 2>/dev/null || true
codex mcp add mcp_debugger -- npx -y github:illscience/mcp-debugger
codex mcp get mcp_debugger

rm -rf /tmp/mcp-debugger-codex-verify
mkdir /tmp/mcp-debugger-codex-verify
cd /tmp/mcp-debugger-codex-verify
npx -y github:illscience/mcp-debugger demo-project --target codex .
ls

codex exec "There is a bug in buggy_invoice.py. Figure out what is wrong and propose the fix. Do not edit files."
```

Codex's MCP config table names are safest with underscores, so the Codex config entry is `mcp_debugger` even though the project and MCP server identify as `mcp-debugger`. The `codex-debugger` remove command clears the old project name if you tried an earlier version.

Expected Codex result:

```text
The bug is in invoice_total: it subtracts the raw rate 0.15 from 120.0, producing 119.85.
It should subtract subtotal * rate, producing 102.0.
```

## Status

This is an alpha release. The first debugger backend is Python via [`debugpy`](https://github.com/microsoft/debugpy); the MCP server is designed to grow to TypeScript/Node and other language runtimes.

The npm package name in this repository is `@illscience/mcp-debugger`. Until it is published to npm, the install commands use `npx -y github:illscience/mcp-debugger`. After publishing, that can become:

```bash
npx -y @illscience/mcp-debugger
```

## Generic MCP Config

```json
{
  "mcpServers": {
    "mcp-debugger": {
      "command": "npx",
      "args": ["-y", "github:illscience/mcp-debugger"],
      "env": {}
    }
  }
}
```

## Clean-Room Prompt Test

This is the fastest way to prove the MCP works the way people will actually use it: from a normal bug-fixing prompt, not from a Python test script and not by explicitly telling the agent which debugger tool to call.

For Claude Code, paste this whole block:

```bash
# Install or reset the MCP server at user scope.
claude mcp remove mcp-debugger -s local 2>/dev/null || true
claude mcp remove mcp-debugger -s user 2>/dev/null || true
claude mcp add -s user mcp-debugger -- npx -y github:illscience/mcp-debugger
claude mcp get mcp-debugger

# Create a fresh demo project.
rm -rf /tmp/mcp-debugger-cleanroom
mkdir /tmp/mcp-debugger-cleanroom
cd /tmp/mcp-debugger-cleanroom
npx -y github:illscience/mcp-debugger demo-project --target claude .
ls

# Ask a normal debugging question and show readable live progress.
claude -p --output-format stream-json --verbose "There is a bug in buggy_invoice.py. Figure out what is wrong and propose the fix. Do not edit files." | tee /tmp/mcp-debugger-claude.jsonl | npx -y github:illscience/mcp-debugger claude-progress

# Optional: grep the raw transcript for MCP/debugger evidence.
grep -E "mcp__mcp-debugger|debug_python_repro|mcp-debugger" /tmp/mcp-debugger-claude.jsonl
```

The `demo-project` command creates:

- `buggy_invoice.py`: a tiny Python program with a real arithmetic bug.
- `CLAUDE.md`: Claude Code project memory that says to use live runtime debugging when a reproducible bug has observable runtime state.

You should see `mcp-debugger` listed as connected and, on a successful debugger-assisted run, a tool call such as `mcp__mcp-debugger__debug_python_repro`.

What you want to see:

- Claude calls the `mcp-debugger` MCP server, usually starting with `debug_python_repro`.
- Claude reports runtime values such as `subtotal = 120.0`, `customer_tier = 'gold'`, and `rate = 0.15`.
- Claude explains that the program subtracts `0.15` directly instead of subtracting `120.0 * 0.15`.
- Claude proposes `total = subtotal * (1 - rate)` or `total = subtotal - (subtotal * rate)`.

You can run the same clean-room prompt test with Codex:

```bash
codex mcp remove mcp_debugger 2>/dev/null || true
codex mcp remove codex-debugger 2>/dev/null || true
codex mcp add mcp_debugger -- npx -y github:illscience/mcp-debugger
codex mcp get mcp_debugger

rm -rf /tmp/mcp-debugger-codex
mkdir /tmp/mcp-debugger-codex
cd /tmp/mcp-debugger-codex
npx -y github:illscience/mcp-debugger demo-project --target codex .
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
npx -y github:illscience/mcp-debugger init-agent-files --target claude
npx -y github:illscience/mcp-debugger init-agent-files --target codex
npx -y github:illscience/mcp-debugger init-agent-files --target both
```

Or print the guidance:

```bash
npx -y github:illscience/mcp-debugger agent-instructions --target claude
npx -y github:illscience/mcp-debugger agent-instructions --target codex
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
