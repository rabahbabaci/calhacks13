"""Offline tests for the legal agent: no API keys, network or database needed.

Run from the repo root: data/.venv/bin/python -m unittest discover agents/legal
"""
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

import agent  # noqa: E402
from research import format_pages  # noqa: E402

CASE = json.loads((Path(__file__).resolve().parent / "cases" / "mold_oakland.json").read_text(encoding="utf-8"))
OAKLAND = {"state": "CA", "city": "Oakland"}


class FakeResearch:
    """Stands in for research.Research: pages already 'read' and canned search results."""

    has_courtlistener = False

    def __init__(self, pages=None, results=None):
        self.pages = dict(pages or {})
        self.results = results or []

    def search(self, query, max_results=3):
        return self.results

    def read(self, url):
        return "pages", self.results


class PackTest(unittest.TestCase):
    def test_finds_pack_case_insensitively(self):
        self.assertEqual(agent.find_pack({"state": "ca", "city": "OAKLAND"})["pack_id"], "ca-oakland")

    def test_no_pack_for_other_cities(self):
        self.assertIsNone(agent.find_pack({"state": "NY", "city": "Brooklyn"}))
        self.assertIsNone(agent.find_pack(None))

    def test_pack_sources_for_a_hazard(self):
        sources, law = agent.pack_sources(agent.find_pack(OAKLAND), "dampness_mold")
        self.assertEqual(law["hazard"], "dampness_mold")
        self.assertIn("P0", sources)
        self.assertEqual(sources["P1"]["cite"], "Cal. Health & Safety Code 17920.3(a)(11)")
        self.assertTrue(all(s["verified"] for s in sources.values()))
        self.assertTrue(all(s["jurisdiction"] == "Oakland, CA" for s in sources.values()))
        # Pack rules flagged for legal aid keep their flag, so the validator can move them.
        self.assertTrue(any(s.get("show_as") == "legal_aid_question" for k, s in sources.items() if k.startswith("G")))

    def test_hazard_missing_from_pack_still_gets_general_rules(self):
        sources, law = agent.pack_sources(agent.find_pack(OAKLAND), "not_a_hazard")
        self.assertIsNone(law)
        self.assertFalse(any(k.startswith("P") for k in sources))
        self.assertTrue(any(k.startswith("G") for k in sources))

    def test_no_pack_means_no_sources(self):
        self.assertEqual(agent.pack_sources(None, "dampness_mold"), ({}, None))


class SaveSourceTest(unittest.TestCase):
    PAGE = "## Section 1941.1\nA dwelling shall be deemed untenantable if it substantially lacks **effective waterproofing** and weather protection."

    def run_with_page(self, page=PAGE):
        return agent.HazardRun(FakeResearch({"https://law.example/1941": page}), {})

    def args(self, **over):
        base = {"id": "R1", "url": "https://law.example/1941", "cite": "Cal. Civ. Code 1941.1", "kind": "statute",
                "excerpt": "substantially lacks effective waterproofing and weather protection"}
        return {**base, **over}

    def test_accepts_excerpt_found_on_page_despite_markdown(self):
        run = self.run_with_page()
        self.assertEqual(run.handle("save_source", self.args()), "Saved R1.")
        self.assertTrue(run.sources["R1"]["excerpt_checked"])
        self.assertFalse(run.sources["R1"]["verified"])

    def test_rejects_excerpt_not_on_page(self):
        with self.assertRaisesRegex(RuntimeError, "not found on that page"):
            self.run_with_page().handle("save_source", self.args(excerpt="landlords must fix every leak within 10 days"))

    def test_rejects_unread_url_short_excerpt_and_reused_id(self):
        run = self.run_with_page()
        with self.assertRaisesRegex(RuntimeError, "not read"):
            run.handle("save_source", self.args(url="https://law.example/other"))
        with self.assertRaisesRegex(RuntimeError, "too short"):
            run.handle("save_source", self.args(excerpt="waterproofing"))
        run.handle("save_source", self.args())
        with self.assertRaisesRegex(RuntimeError, "already used"):
            run.handle("save_source", self.args())

    def test_pdf_excerpts_are_kept_but_marked_unchecked(self):
        run = self.run_with_page(page=None)
        run.handle("save_source", self.args(excerpt="anything quoted from the PDF document"))
        self.assertFalse(run.sources["R1"]["excerpt_checked"])

    def test_normalize_ignores_quotes_case_and_whitespace(self):
        self.assertEqual(agent.normalize("The “Landlord’s”\n  duty"), agent.normalize('the "landlord\'s" duty'))


class BudgetTest(unittest.TestCase):
    def test_research_budget_is_enforced(self):
        run = agent.HazardRun(FakeResearch(results=[{"url": "u", "title": "t", "text": "x"}]), {})
        for _ in range(agent.RESEARCH_BUDGET):
            run.handle("web_search", {"query": "oakland habitability"})
        with self.assertRaisesRegex(RuntimeError, "budget"):
            run.handle("web_search", {"query": "one too many"})

    def test_empty_results_do_not_count(self):
        run = agent.HazardRun(FakeResearch(results=[]), {})
        for _ in range(agent.RESEARCH_BUDGET + 3):
            run.handle("web_search", {"query": "blocked"})
        self.assertEqual(run.used, 0)

    def test_submit_only_once(self):
        run = agent.HazardRun(FakeResearch(), {})
        run.handle("submit_assessment", {"landlord_duty": []})
        with self.assertRaisesRegex(RuntimeError, "already submitted"):
            run.handle("submit_assessment", {"landlord_duty": []})

    def test_unknown_tool(self):
        with self.assertRaisesRegex(RuntimeError, "unknown tool"):
            agent.HazardRun(FakeResearch(), {}).handle("delete_everything", {})


def good_draft(*_args):
    return {
        "landlord_duty": [{"text": "Dampness that endangers occupants can make a unit substandard.", "source_ids": ["P1", "P0"]}],
        "tenant_options": [{"text": "You can withhold rent until it is fixed.", "source_ids": ["P0"]}],
        "deadlines": [],
        "ask_legal_aid": [],
        "similar_cases": [],
    }, {"calls": 1}


class AssessCaseTest(unittest.TestCase):
    """The run lifecycle against a fake database: which reducers get called, in what order."""

    def run_case(self, case=CASE, llm=good_draft, existing_case=False, save=True):
        calls = []
        rows = [{"case_id": case["case_id"]}] if existing_case else []
        with mock.patch.object(agent, "call_reducer", side_effect=lambda name, args: calls.append((name, args))), \
             mock.patch.object(agent, "sql", return_value=rows), \
             mock.patch.object(agent, "run_llm", side_effect=llm):
            out = agent.assess_case(case, save=save, client=object(), research=FakeResearch())
        return out, calls

    def test_creates_case_then_one_run_per_confirmed_hazard(self):
        out, calls = self.run_case()
        names = [n for n, _ in calls]
        self.assertEqual(names[0], "create_case")
        self.assertEqual([a for n, a in calls if n == "start_run"], [[CASE["case_id"], "legal", "dampness_mold"], [CASE["case_id"], "legal", "water_leak"]])
        self.assertEqual(names.count("finish_run"), 2)
        self.assertNotIn("fail_run", names)
        self.assertEqual([a["hazard"] for a in out["assessments"]], ["dampness_mold", "water_leak"])
        self.assertEqual(out["not_assessed"], [{"hazard": "rodents", "reason": "low confidence and not confirmed by tenant"}])
        self.assertNotIn("_sources", out)

    def test_existing_case_is_not_overwritten(self):
        _, calls = self.run_case(existing_case=True)
        self.assertNotIn("create_case", [n for n, _ in calls])

    def test_finish_run_payload_is_validated_output(self):
        out, calls = self.run_case()
        payload = json.loads(next(a for n, a in calls if n == "finish_run")[3])
        self.assertEqual(payload["hazard"], "dampness_mold")
        self.assertEqual(payload["tenant_options"], [])  # the withholding claim was dropped
        self.assertTrue(any(d["reason"].startswith("never_say") for d in out["dropped"]))
        self.assertEqual({s["id"] for s in payload["sources"]}, {"P1", "P0"})
        self.assertFalse(payload["urgent"])

    def test_only_sources_with_urls_go_to_legal_source(self):
        _, calls = self.run_case()
        added = [a for n, a in calls if n == "add_legal_source"]
        self.assertTrue(added)
        self.assertTrue(all(a[0].startswith("https://") for a in added))  # P0 has no url and is skipped
        self.assertTrue(all(a[5] is True for a in added))

    def test_failure_marks_the_run_failed_and_continues(self):
        def flaky(client, research, case, h, *rest):
            if h["hazard"] == "dampness_mold":
                raise RuntimeError("model never called submit_assessment")
            return good_draft()

        out, calls = self.run_case(llm=flaky)
        failed = [a for n, a in calls if n == "fail_run"]
        self.assertEqual(failed, [[CASE["case_id"], "legal", "dampness_mold", "model never called submit_assessment"]])
        self.assertEqual([a["hazard"] for a in out["assessments"]], ["water_leak"])
        self.assertEqual(out["failed"][0]["hazard"], "dampness_mold")

    def test_urgent_hazards_reuse_the_health_safety_message(self):
        case = {**CASE, "hazards": [{"hazard": "gas_leak", "location": "kitchen", "confidence": 0.9, "confirmed_by_tenant": True}]}
        out, _ = self.run_case(case=case)
        self.assertTrue(out["assessments"][0]["urgent"])
        self.assertIn("911", out["assessments"][0]["urgent_message"])

    def test_no_save_makes_no_database_calls(self):
        out, calls = self.run_case(save=False)
        self.assertEqual(calls, [])
        self.assertEqual(len(out["assessments"]), 2)


class EnvTest(unittest.TestCase):
    def test_legal_db_settings_override_shared_ones(self):
        with mock.patch.dict(agent.os.environ, {"STDB_URL": "https://shared", "STDB_URL_Legal": "http://legal"}, clear=False):
            agent.load_env()
            self.assertEqual(agent.os.environ["STDB_URL"], "http://legal")


class FormatPagesTest(unittest.TestCase):
    def test_wraps_pages_and_escapes_titles(self):
        out = format_pages([{"url": "https://a", "title": 'Say "hi"', "text": "body"}])
        self.assertIn('<page url="https://a" title="Say \'hi\'">', out)

    def test_empty_results_suggest_another_copy(self):
        self.assertIn("No pages returned", format_pages([]))


if __name__ == "__main__":
    unittest.main()
