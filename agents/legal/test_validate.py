"""Offline tests for the legal validator. Run: data/.venv/bin/python -m unittest agents/legal/test_validate.py"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from validate import validate  # noqa: E402

SOURCES = {
    "P1": {"text": "Dampness of habitable rooms.", "kind": "statute"},
    "G2": {"text": "30 days after notice is presumed reasonable", "kind": "statute", "show_as": "legal_aid_question"},
    "R1": {"text": "The court awarded the tenant $5,000.", "kind": "case_law"},
    "R2": {"text": "City guide.", "kind": "agency"},
}


def run(field, text, ids):
    out, cited, dropped = validate("dampness_mold", {field: [{"text": text, "source_ids": ids}]}, SOURCES)
    return out, dropped


class ValidateTest(unittest.TestCase):
    def test_keeps_cited_claim(self):
        out, dropped = run("landlord_duty", "Dampness can make a unit substandard.", ["P1"])
        self.assertEqual(len(out["landlord_duty"]), 1)
        self.assertEqual(dropped, [])

    def test_drops_uncited_and_unknown(self):
        self.assertEqual(run("landlord_duty", "No source.", [])[1][0]["reason"], "no source_ids")
        self.assertIn("unknown source ids", run("landlord_duty", "Bad id.", ["P9"])[1][0]["reason"])

    def test_never_say(self):
        for text in [
            "You can withhold rent until it is fixed.",
            "You have a strong case.",
            "Your evidence packet is court-ready.",
            "This is mold.",
            "You could get $2,000.",
        ]:
            _, dropped = run("tenant_options", text, ["P1"])
            self.assertTrue(dropped and dropped[0]["reason"].startswith("never_say"), text)

    def test_bare_string_claim_is_dropped_not_crashed(self):
        out, cited, dropped = validate("dampness_mold", {"deadlines": ["none", {"text": "Dampness.", "source_ids": ["P1"]}]}, SOURCES)
        self.assertEqual(len(out["deadlines"]), 1)
        self.assertEqual(dropped, [{"hazard": "dampness_mold", "field": "deadlines", "text": "none", "reason": "claim is not an object"}])

    def test_days_must_appear_in_cited_source(self):
        self.assertEqual(run("deadlines", "Wait 14 days after notice.", ["G2"])[1][0]["reason"], "14 days not stated in the cited sources")
        out, dropped = run("deadlines", "30 days after notice is presumed reasonable, not a fixed deadline.", ["G2"])
        self.assertEqual((len(out["deadlines"]), dropped), (1, []))

    def test_legal_aid_question_moves(self):
        out, _ = run("tenant_options", "Ask legal aid about repair-and-deduct.", ["G2"])
        self.assertEqual((out["tenant_options"], len(out["ask_legal_aid"])), ([], 1))

    def test_similar_cases_need_case_law_and_allow_amounts(self):
        out, dropped = run("similar_cases", "A tenant was awarded $5,000 in a mold case.", ["R1"])
        self.assertEqual((len(out["similar_cases"]), dropped), (1, []))
        self.assertIn("no court opinion", run("similar_cases", "A guide says tenants win.", ["R2"])[1][0]["reason"])


if __name__ == "__main__":
    unittest.main()
