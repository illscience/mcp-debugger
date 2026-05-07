from __future__ import annotations


AGENT_USAGE_GUIDANCE = """Use vibe-debug when a Python bug has runtime behavior that static reading does not fully explain.

Prefer debugger tools when:
- a test, script, or command reproduces the bug;
- the bug depends on branches, state, inputs, or object values;
- an exception stack is insufficient and local variables matter;
- you are about to guess what a variable contains.

Recommended workflow:
1. Use debug_python_repro for the first pass when you have a Python script and likely breakpoint lines.
2. Inspect returned stack and topFrameLocals before editing code.
3. Use debug_step, debug_stack, debug_scopes, debug_variables, and debug_evaluate only when more detail is needed.
4. Use debug_stop when the session is no longer needed.

Do not use debug_evaluate for arbitrary side effects. Treat it like running code inside the debuggee.
"""


PROJECT_DEBUGGING_GUIDANCE = """# Debugging Workflow

When a Python bug has a reproducible script, test, command, or request, prefer observing live runtime state before proposing a fix.

Use the `vibe-debug` MCP tools when:
- the failure depends on branches, inputs, object state, or local variables;
- source reading suggests multiple possible causes;
- you are about to infer a variable value that can be observed directly.

Recommended workflow:
1. Start with `debug_python_repro` for Python scripts or minimal repro files.
2. Inspect the returned stack and `snapshot.locals`.
3. Use `debug_step`, `debug_stack`, `debug_scopes`, and `debug_variables` when more detail is needed.
4. Use `debug_evaluate` only for read-style expressions; it can execute code in the debuggee.
5. Use `debug_stop` when finished with a debug session.

When reporting a bug, include the runtime values you observed and distinguish them from source-code inference.
"""


def guidance_for_target(target: str) -> str:
    if target not in {"generic", "claude", "codex"}:
        raise ValueError(f"unknown guidance target: {target}")
    return PROJECT_DEBUGGING_GUIDANCE
