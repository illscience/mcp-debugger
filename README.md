# Vibe Debug

A debugger CLI and MCP server for coding agents.

`vibe-debug` gives Codex, Claude Code, Cursor-style agents, and other MCP clients real debugger tools: launch, attach, set breakpoints, continue, step into/out/over, inspect stack frames, read locals, expand variables, evaluate expressions, and stop sessions.

The goal is simple: when an agent is fixing a bug, it should be able to use a debugger the same way a human engineer would.

```text
coding agent
  -> CLI command or MCP tool call
  -> vibe-debug
  -> Debug Adapter Protocol
  -> language debugger backend
  -> your app
```

## Install

### Claude Code

```bash
claude mcp add -s user vibe-debug -- npx -y github:illscience/vibe-debug
```

### Codex

```bash
codex mcp add vibe_debug -- npx -y github:illscience/vibe-debug
```

Codex's MCP config table names are safest with underscores, so the Codex config entry is `vibe_debug` even though the project and MCP server identify as `vibe-debug`.

### Other MCP Clients

Use this stdio server config:

```json
{
  "mcpServers": {
    "vibe-debug": {
      "command": "npx",
      "args": ["-y", "github:illscience/vibe-debug"],
      "env": {}
    }
  }
}
```

### Health Check

The MCP server uses a small Python/debugpy cache behind the `npx` wrapper. Warm and verify it with:

```bash
npx -y github:illscience/vibe-debug doctor
```

Expected output:

```text
vibe-debug 0.2.1
Python: ...
debugpy import: ok
MCP initialize: ok
```

For the full machine-readable report, run `npx -y github:illscience/vibe-debug doctor --json`.

### Disable

MCP tools are visible to the agent while the server is enabled. If you only want debugger tools for a specific project or debugging session, remove the MCP entry when you are done:

```bash
claude mcp remove vibe-debug -s user
codex mcp remove vibe_debug
```

## Use Without MCP

You can also run the debugger as a normal CLI from any coding agent shell. This avoids adding persistent MCP tools when you only need debugger state for a single bug:

```bash
npx -y github:illscience/vibe-debug debug-python ./buggy_invoice.py --break ./buggy_invoice.py:13 --eval "subtotal * (1 - rate)"
```

Human output is concise:

```text
Stopped: buggy_invoice.py:13 in invoice_total
Reason: breakpoint
Locals:
  customer_tier = 'gold'
  rate = 0.15
  subtotal = 120.0
  total = 119.85
Evaluations:
  subtotal * (1 - rate) -> 102.0
```

Use `--json` when you want machine-readable output for an agent or script:

```bash
npx -y github:illscience/vibe-debug debug-python ./buggy_invoice.py --break ./buggy_invoice.py:13 --eval "subtotal * (1 - rate)" --json
```

To help agents discover the CLI without keeping MCP tools enabled, add a lightweight project skill:

```bash
npx -y github:illscience/vibe-debug init-cli-skill --target claude
```

That writes `.claude/skills/vibe-debug/SKILL.md`: a short skill whose frontmatter explicitly triggers on reproducible Python bugs, failing Python tests/scripts, wrong output, exceptions, and logic errors where live runtime state would help. The skill body is CLI documentation for `debug-python`.

## Status

This is an alpha release. The first debugger backend is Python via [`debugpy`](https://github.com/microsoft/debugpy); the MCP server is designed to grow to TypeScript/Node and other language runtimes.

The npm package name in this repository is `@illscience/vibe-debug`. Until it is published to npm, the install commands use `npx -y github:illscience/vibe-debug`. After publishing, that can become:

```bash
npx -y @illscience/vibe-debug
```

## Optional Clean-Room Test

This proves the MCP works the way people actually use it: from a normal bug-fixing prompt, not from a Python test script and not by explicitly telling the agent which debugger tool to call.

### Claude Code

Paste this block:

```bash
npx -y github:illscience/vibe-debug doctor

claude mcp add -s user vibe-debug -- npx -y github:illscience/vibe-debug
claude mcp get vibe-debug

rm -rf /tmp/vibe-debug-claude-verify
mkdir /tmp/vibe-debug-claude-verify
cd /tmp/vibe-debug-claude-verify
npx -y github:illscience/vibe-debug demo-project --target claude .

claude -p --output-format stream-json --verbose "There is a bug in buggy_invoice.py. Figure out what is wrong and propose the fix. Do not edit files." | npx -y github:illscience/vibe-debug claude-progress
```

If you previously installed an old version, reset first:

```bash
claude mcp remove vibe-debug -s local 2>/dev/null || true
claude mcp remove vibe-debug -s user 2>/dev/null || true
```

The `demo-project` command creates:

- `buggy_invoice.py`: a tiny Python program with a real arithmetic bug.
- `CLAUDE.md`: Claude Code project memory that says to use live runtime debugging when a reproducible bug has observable runtime state.

Claude Code loads project instructions from `./CLAUDE.md` ([Anthropic docs](https://docs.anthropic.com/en/docs/claude-code/memory)). `vibe-debug` can create that file for you in a test project, as shown above.

You should see `vibe-debug` listed as connected, starting, or active and, on a successful debugger-assisted run, a tool call such as `mcp__vibe-debug__debug_python_repro`.

What you want to see:

- Claude calls the `vibe-debug` MCP server, usually starting with `debug_python_repro`.
- Claude reports runtime values such as `subtotal = 120.0`, `customer_tier = 'gold'`, and `rate = 0.15`.
- Claude explains that the program subtracts `0.15` directly instead of subtracting `120.0 * 0.15`.
- Claude proposes `total = subtotal * (1 - rate)` or `total = subtotal - (subtotal * rate)`.

### Codex

```bash
npx -y github:illscience/vibe-debug doctor

codex mcp add vibe_debug -- npx -y github:illscience/vibe-debug
codex mcp get vibe_debug

rm -rf /tmp/vibe-debug-codex
mkdir /tmp/vibe-debug-codex
cd /tmp/vibe-debug-codex
npx -y github:illscience/vibe-debug demo-project --target codex .
codex exec "There is a bug in buggy_invoice.py. Figure out what is wrong and propose the fix. Do not edit files."
```

If you previously tried an old version, reset first:

```bash
codex mcp remove vibe_debug 2>/dev/null || true
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
    "debug_evaluate default top frame",
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
npx -y github:illscience/vibe-debug init-agent-files --target claude
npx -y github:illscience/vibe-debug init-agent-files --target codex
npx -y github:illscience/vibe-debug init-agent-files --target both
```

Or print the guidance:

```bash
npx -y github:illscience/vibe-debug agent-instructions --target claude
npx -y github:illscience/vibe-debug agent-instructions --target codex
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
.venv/bin/python -m pip wheel . -w /tmp/vibe-debug-wheel
```

## Safety

`debug_evaluate` can execute code inside the target process. Treat it like running code in the debuggee. The server defaults to localhost debug adapter connections and cleans up launched sessions when the MCP server exits.

## Roadmap

- `debug_pytest_failure`: run a failing pytest test under the debugger automatically.
- Breakpoints by function name, symbol, marker comment, or exception type.
- Richer first-stop summaries with surrounding source and suggested next debugger actions.
- Agent-optimized CLI commands, with MCP as a thin wrapper for clients that prefer tools over shell commands.
- Node.js / Next.js support through the Node inspector or Chrome DevTools Protocol.

## License

MIT
