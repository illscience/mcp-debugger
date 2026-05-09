from __future__ import annotations


AGENT_USAGE_GUIDANCE = """Use vibe-debug when live Python runtime state would help debug, verify, or implement behavior that static reading does not fully explain.

Prefer live debugger state when:
- a test, script, command, or local request exercises the behavior;
- a bug or feature depends on branches, state, inputs, or object values;
- you need to verify a fix or implementation takes the expected path;
- an exception stack is insufficient and local variables matter;
- you are about to guess what a variable contains.

Recommended workflow:
1. If a vibe-debug skill is available, use its CLI workflow first.
2. Run `debug-python` for a script/test, `debug-request` for a local HTTP request, or `attach-python` for an existing debugpy listener.
3. If the MCP server is installed, `debug_python_repro` is the best first MCP tool for script repros.
4. Inspect stopped location, locals, and evaluations before editing code.

Communication:
- before using the debugger, state the mode, target script/test/request or attach port, and breakpoint;
- after it stops, state the stopped file, line, function, and the observed values that matter;
- if you skip the debugger for runnable Python behavior, briefly state why.

Evaluate only read-style expressions. Treat evaluation like running code inside the debuggee.
"""


PROJECT_DEBUGGING_GUIDANCE = """# Debugging Workflow

When Python behavior can be exercised by a script, test, command, or local request, prefer observing live runtime state before guessing, proposing a fix, or claiming a feature works.

Use the `vibe-debug` skill or CLI when:
- the behavior depends on branches, inputs, object state, or local variables;
- source reading suggests multiple possible causes;
- you need to verify a fix, feature, or code path works as intended;
- you are about to infer a variable value that can be observed directly.

Recommended workflow:
1. Use the `vibe-debug` skill if it is available in this project.
2. Run `npx -y github:illscience/vibe-debug debug-python <script.py> --break <file.py>:<line> --json` for scripts/tests.
3. For local web requests, run `npx -y github:illscience/vibe-debug debug-request <server.py> --url <local-url> --break <file.py>:<line> --json`.
4. For an existing debugpy listener, run `npx -y github:illscience/vibe-debug attach-python --port <port> --break <file.py>:<line> --json`.
5. Inspect the stopped location, locals, and evaluations.
6. Use `--eval` only for read-style expressions; it can execute code in the debuggee.
7. If an MCP server named `vibe-debug` is installed, `debug_python_repro` is also acceptable.

Always tell the user when and how you are using the debugger: before running it, state the mode, target script/test/request or attach port, and breakpoint; after it stops, state the stopped file, line, function, and observed values that matter. If you skip the debugger for runnable Python behavior, briefly state why.

When reporting a bug, fix, or verification result, include the runtime values you observed and distinguish them from source-code inference.
"""


def guidance_for_target(target: str) -> str:
    if target not in {"generic", "claude", "codex"}:
        raise ValueError(f"unknown guidance target: {target}")
    return PROJECT_DEBUGGING_GUIDANCE
