"""Unit tests for bridge prompt/edit parsing (stdlib only)."""
import json
import unittest

from bridge import _text_from_run_jsonl, build_prompt, extract_edits

# Recorded verbatim from `opencode run --format json` (opencode v2.0.26,
# prompt asked for exactly {"edits": []}). IDs are random local identifiers.
RECORDED_JSONL = (
    '{"type":"step_start","timestamp":1792084433991,"sessionID":"ses_ebf71a8b8ffefaSaPzjk5HXiGR",'
    '"part":{"id":"prt_1408e5c46001Ajf4I049bpFrUx","sessionID":"ses_ebf71a8b8ffefaSaPzjk5HXiGR",'
    '"messageID":"msg_1408e57c5001jqF6iL23AVhMKV","type":"step-start"}}\n'
    '{"type":"text","timestamp":1792084434157,"sessionID":"ses_ebf71a8b8ffefaSaPzjk5HXiGR",'
    '"part":{"id":"prt_1408e57c5001jqF6iL23AVhMKV_text-0","sessionID":"ses_ebf71a8b8ffefaSaPzjk5HXiGR",'
    '"messageID":"msg_1408e57c5001jqF6iL23AVhMKV","type":"text","text":"{\\"edits\\": []}",'
    '"time":{"start":1792084432872,"end":1792084434157}}}\n'
)


class TestPrompt(unittest.TestCase):
    def test_build_includes_command_and_selection(self):
        p = build_prompt("Summarize", "selection", "page body", "sel text", "https://x")
        self.assertIn("Summarize", p)
        self.assertIn("sel text", p)
        self.assertIn("edits", p)


class TestExtract(unittest.TestCase):
    def test_plain_json(self):
        out = '{"edits": [{"mode": "replace-selection", "text": "hi"}]}'
        self.assertEqual(
            extract_edits(out, True),
            [{"mode": "replace-selection", "text": "hi"}],
        )

    def test_fenced_json(self):
        out = 'bla ```json\n{"edits": [{"mode": "append", "text": "x"}]}\n``` tail'
        self.assertEqual(extract_edits(out, False), [{"mode": "append", "text": "x"}])

    def test_invalid_mode_defaults(self):
        out = '{"edits": [{"mode": "nope", "text": "x"}]}'
        self.assertEqual(extract_edits(out, True)[0]["mode"], "replace-selection")
        self.assertEqual(extract_edits(out, False)[0]["mode"], "append")

    def test_fallback_wraps_prose(self):
        self.assertEqual(
            extract_edits("just some prose", True),
            [{"mode": "replace-selection", "text": "just some prose"}],
        )

    def test_empty_returns_empty(self):
        self.assertEqual(extract_edits("   ", True), [])

    def test_explicit_empty_list_returns_empty(self):
        # Model explicitly returned no edits: must NOT wrap raw JSON as an edit.
        self.assertEqual(extract_edits('{"edits": []}', False), [])
        self.assertEqual(extract_edits('{"edits": []}', True), [])


class TestRunJsonl(unittest.TestCase):
    def test_recorded_probe_yields_assistant_text(self):
        self.assertEqual(_text_from_run_jsonl(RECORDED_JSONL), '{"edits": []}')

    def test_recorded_probe_end_to_end_to_edits(self):
        self.assertEqual(extract_edits(_text_from_run_jsonl(RECORDED_JSONL), False), [])

    def test_concatenates_streamed_chunks(self):
        def tev(t):
            return json.dumps({"type": "text", "part": {"type": "text", "text": t}})
        out = tev('{"edits": [{"mode": "app') + "\n" + tev('end", "text": "x"}]}') + "\n"
        self.assertEqual(
            extract_edits(_text_from_run_jsonl(out), False),
            [{"mode": "append", "text": "x"}],
        )

    def test_ignores_non_text_events(self):
        out = (
            '{"type":"step_start","part":{"type":"step-start"}}\n'
            '{"type":"reasoning","part":{"type":"reasoning","text":"{think}"}}\n'
            'not json at all\n'
            '{"type":"text","part":{"type":"text","text":"hi"}}\n'
        )
        self.assertEqual(_text_from_run_jsonl(out), "hi")

    def test_no_text_events_returns_empty(self):
        self.assertEqual(_text_from_run_jsonl('{"type":"step_start","part":{}}\n'), "")

    def test_run_opencode_uses_jsonl_stdout(self):
        # Genuine run_opencode path with a stub binary replaying recorded
        # bytes: exercises subprocess wiring + JSONL parsing, no model call.
        import os
        import shlex
        import tempfile

        import bridge

        with tempfile.TemporaryDirectory() as d:
            fix = os.path.join(d, "out.txt")
            with open(fix, "w") as f:
                f.write(RECORDED_JSONL)
            stub = os.path.join(d, "fake-opencode")
            with open(stub, "w") as f:
                f.write("#!/bin/sh\ncat " + shlex.quote(fix) + "\n")
            os.chmod(stub, 0o755)
            old_bin, old_mock = bridge.OPENCODE_BIN, bridge.MOCK
            bridge.OPENCODE_BIN, bridge.MOCK = stub, False
            try:
                self.assertEqual(bridge.run_opencode("prompt", ""), '{"edits": []}')
            finally:
                bridge.OPENCODE_BIN, bridge.MOCK = old_bin, old_mock


if __name__ == "__main__":
    unittest.main()
