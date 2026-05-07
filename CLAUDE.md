# Debugging Workflow

When a Python bug has a reproducible script, test, command, or request, prefer observing live runtime state before proposing a fix.

Use the `mcp-debugger` MCP tools when:
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
