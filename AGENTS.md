# Debugging Workflow

When Python or TypeScript behavior can be exercised by a script, test, command, or local request, prefer observing live runtime state before guessing, proposing a fix, or claiming a feature works.

Use the `vibe-debug` skill or CLI when:
- the behavior depends on branches, inputs, object state, or local variables;
- source reading suggests multiple possible causes;
- you need to verify a fix, feature, or code path works as intended;
- you are about to infer a variable value that can be observed directly.

Recommended workflow:
1. Use the `vibe-debug` skill if it is available in this project.
2. Run `npx -y github:illscience/vibe-debug debug-python <script.py> --break <file.py>:<line> --json` for scripts/tests.
3. Run `npx -y github:illscience/vibe-debug debug-typescript <script.ts> --break <file.ts>:<line> --json` for TypeScript/JavaScript scripts.
4. For local Python web requests, run `npx -y github:illscience/vibe-debug debug-request <server.py> --url <local-url> --break <file.py>:<line> --json`.
5. For an existing debugpy listener, run `npx -y github:illscience/vibe-debug attach-python --port <port> --break <file.py>:<line> --json`.
6. Inspect the stopped location, locals, and evaluations.
7. Use `--eval` only for read-style expressions; it can execute code in the debuggee.
8. If an MCP server named `vibe-debug` is installed, `debug_python_repro` and `debug_typescript_repro` are also acceptable.

Always tell the user when and how you are using the debugger: before running it, state the mode, target script/test/request or attach port, and breakpoint; after it stops, state the stopped file, line, function, and observed values that matter. If you skip the debugger for runnable Python or TypeScript behavior, briefly state why.

When reporting a bug, fix, or verification result, include the runtime values you observed and distinguish them from source-code inference.
