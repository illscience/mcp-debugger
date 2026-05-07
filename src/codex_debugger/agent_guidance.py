from __future__ import annotations


AGENT_USAGE_GUIDANCE = """Use codex-debugger when a Python bug has runtime behavior that static reading does not fully explain.

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
