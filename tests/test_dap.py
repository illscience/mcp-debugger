from __future__ import annotations

import io
import unittest

from codex_debugger.dap import encode_dap_message, read_dap_message


class DAPFramingTests(unittest.TestCase):
    def test_round_trip_content_length_frame(self) -> None:
        payload = {
            "seq": 1,
            "type": "request",
            "command": "stackTrace",
            "arguments": {"threadId": 9},
        }

        framed = encode_dap_message(payload)
        self.assertTrue(framed.startswith(b"Content-Length: "))
        self.assertEqual(read_dap_message(io.BytesIO(framed)), payload)


if __name__ == "__main__":
    unittest.main()
