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
        for text in ("Lock.{ name [ /tmp/file ] }", "Observed.Locks.[]", "Err.«no such lock»"):
            result = classify_input(claude(text))
            self.assertEqual(result.status, "ambiguous")
            self.assertEqual(result.reason, "whole_datom")

    def test_sender_signature_is_machine(self):
        result = classify_input(claude("From Mind flow Primary bb30ec, a machine:\nhello"))
        self.assertEqual(result.status, "machine")

    def test_abbreviation_and_code_are_living_prose(self):
        for text in ("Dr. Smith is here", "Use Foo.{x} in the example", "code = { braces }"):
            self.assertEqual(classify_input(claude(text)).status, "living")

    def test_mixed_blocks_are_ambiguous(self):
        record = claude([
            {"type": "text", "text": "human prose"},
            {"type": "text", "text": "<system-reminder>machine text</system-reminder>"},
        ])
        result = classify_input(record)
        self.assertEqual(result.status, "ambiguous")
        self.assertEqual(result.reason, "mixed_unseparable_provenance")

    def test_meta_user_record_is_machine(self):
        result = classify_input(claude("generated", isMeta=True))
        self.assertEqual(result.status, "machine")


if __name__ == "__main__":
    unittest.main()
