# MCP Debugger Agent Guidance

When debugging Python runtime bugs, prefer observing live state with `mcp-debugger` instead of guessing from source alone.

Use the MCP debugger when:
- a Python script, test, command, or request reproduces the bug;
- the failure depends on branch choices, object state, inputs, or local variables;
- you are about to infer a value that can be observed at runtime.

Recommended workflow:
1. Start with `debug_python_repro` when you can provide a script and likely breakpoint lines.
2. Inspect the returned stack and `snapshot.locals` before editing code.
3. Use `debug_step`, `debug_stack`, `debug_scopes`, `debug_variables`, and `debug_evaluate` for deeper inspection.
4. Use `debug_stop` when finished with a session.

`debug_evaluate` can execute code inside the target process. Use it for read-style expressions unless the user explicitly wants a state-changing evaluation.
