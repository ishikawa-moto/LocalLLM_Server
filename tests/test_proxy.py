import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
from codex_proxy import normalize


class ProxyNormalizationTests(unittest.TestCase):
    def test_merges_developer_message_into_instructions(self):
        body = {
            "instructions": "base",
            "input": [
                {"type": "message", "role": "developer", "content": [{"type": "input_text", "text": "policy"}]},
                {"type": "message", "role": "user", "content": "question"},
            ],
        }
        result = normalize(body)
        self.assertEqual(result["instructions"], "base\n\npolicy")
        self.assertEqual([item["role"] for item in result["input"]], ["user"])

    def test_preserves_non_message_items(self):
        body = {"input": [{"type": "function_call_output", "call_id": "1", "output": "ok"}]}
        self.assertEqual(normalize(body), body)

    def test_accepts_string_policy_content(self):
        body = {"input": [{"type": "message", "role": "system", "content": "policy"}]}
        self.assertEqual(normalize(body), {"input": [], "instructions": "policy"})

    def test_leaves_string_input_unchanged(self):
        body = {"input": "hello"}
        self.assertEqual(normalize(body), body)


if __name__ == "__main__":
    unittest.main()
