import unittest

from transcript import classify_input


def claude(text, **extra):
    record = {"type": "user", "message": {"role": "user", "content": text}, "userType": "external"}
    record.update(extra)
    return record


def codex(text):
    return {
        "type": "event_msg",
        "payload": {
            "type": "item_completed",
            "item": {"type": "UserMessage", "content": [{"type": "text", "text": text}]},
        },
    }


class InputClassificationTests(unittest.TestCase):
    def test_plain_claude_input_is_living(self):
        result = classify_input(claude("Please inspect the parser"))
        self.assertEqual((result.status, result.source), ("living", "claude_user"))

    def test_plain_codex_user_message_is_living(self):
        result = classify_input(codex("Please inspect the parser"))
        self.assertEqual((result.status, result.source), ("living", "codex_user_message"))

    def test_harness_tags_are_machine(self):
        result = classify_input(claude("<local-command-caveat>not a turn</local-command-caveat>"))
        self.assertEqual(result.status, "machine")

    def test_explicit_relay_is_machine(self):
        result = classify_input(codex("Soft.{ abc Sender Text.hello }"))
        self.assertEqual(result.status, "machine")

    def test_datom_shape_is_typed_uncertainty(self):
        result = classify_input(claude("Lock.{ name [ /tmp/file ] }"))
        self.assertEqual(result.status, "ambiguous")
        self.assertEqual(result.reason, "datom_shape_without_authorship")

    def test_meta_user_record_is_machine(self):
        result = classify_input(claude("generated", isMeta=True))
        self.assertEqual(result.status, "machine")


if __name__ == "__main__":
    unittest.main()
