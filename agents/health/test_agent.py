"""Offline tests for the health validator. No network, no keys.
Run: data/.venv/bin/python -m unittest discover -s agents/health -p "test_*.py"
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from agent import validate  # noqa: E402

CHUNKS = [
    {"id": 1, "source_url": "https://www.cdc.gov/mold/", "hazard": "dampness_mold", "ordinal": 0, "text": "Damp spaces are linked with coughing."},
    {"id": 2, "source_url": "https://www.cdc.gov/mold/", "hazard": "dampness_mold", "ordinal": 1, "text": "Fix leaks and dry wet areas."},
]
SOURCES = [{"url": "https://www.cdc.gov/mold/", "title": "Mold", "publisher": "CDC", "hazard": "dampness_mold"}]


def run(**fields):
    draft = {"exposure_pathways": [], "health_risks": [], "vulnerable_groups": [], "precautions": [], "see_a_doctor_if": [], **fields}
    return validate("dampness_mold", draft, CHUNKS, SOURCES)


class ValidateTest(unittest.TestCase):
    def test_keeps_cited_claim_and_attaches_source(self):
        out, dropped = run(health_risks=[{"text": "Damp indoor spaces are linked with coughing.", "chunk_ids": [1]}])
        self.assertEqual(len(out["health_risks"]), 1)
        self.assertEqual([s["publisher"] for s in out["sources"]], ["CDC"])
        self.assertEqual(dropped, [])

    def test_bare_string_claim_is_dropped_not_crashed(self):
        out, dropped = run(precautions=["none", {"text": "Fix leaks and dry wet areas.", "chunk_ids": [2]}])
        self.assertEqual(len(out["precautions"]), 1)
        self.assertEqual(dropped, [{"hazard": "dampness_mold", "field": "precautions", "text": "none", "reason": "claim is not an object"}])

    def test_never_say_mold_as_finding(self):
        for text in ["This is mold on the ceiling.", "It is black mold.", "You have mold in the bathroom."]:
            _, dropped = run(health_risks=[{"text": text, "chunk_ids": [1]}])
            self.assertEqual(dropped[0]["reason"], "never_say: mold stated as a finding", text)

    def test_never_say_symptom_attribution(self):
        for text in ["Your child's cough is likely from the dampness.", "Your cough may improve.", "The headache is probably caused by this."]:
            _, dropped = run(health_risks=[{"text": text, "chunk_ids": [1]}])
            self.assertEqual(dropped[0]["reason"], "never_say: symptom attributed to this hazard", text)

    def test_general_claims_about_children_are_kept(self):
        out, dropped = run(vulnerable_groups=[{"text": "Children and people with asthma are more sensitive to damp indoor air.", "chunk_ids": [1]}])
        self.assertEqual((len(out["vulnerable_groups"]), dropped), (1, []))


if __name__ == "__main__":
    unittest.main()
