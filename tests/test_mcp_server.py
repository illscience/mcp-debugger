from __future__ import annotations

import unittest

from mcp_debugger.mcp_server import MCPDebuggerServer


class MCPServerTests(unittest.TestCase):
    def test_initialize_and_tool_list(self) -> None:
        server = MCPDebuggerServer()

        initialize = server.handle_message(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": "2025-06-18"},
            }
        )
        self.assertEqual(initialize["result"]["serverInfo"]["name"], "mcp-debugger")

        listed = server.handle_message({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        tools = {tool["name"] for tool in listed["result"]["tools"]}
        self.assertIn("debug_guidance", tools)
        self.assertIn("debug_python_repro", tools)
        self.assertIn("debug_launch", tools)
        self.assertIn("debug_set_breakpoints", tools)
        self.assertIn("debug_step", tools)
        self.assertIn("debug_variables", tools)


if __name__ == "__main__":
    unittest.main()
