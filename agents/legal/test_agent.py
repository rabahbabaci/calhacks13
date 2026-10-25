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

    def assert_rejected(self, run, args, message):
        with self.assertRaisesRegex(RuntimeError, message):
            run.handle("save_source", args)

    def test_rejects_excerpt_not_on_page(self):
        run, args = self.run_with_page(), self.args(excerpt="landlords must fix every leak within 10 days")
        self.assert_rejected(run, args, "not found on that page")

    def test_rejects_unread_url(self):
        run, args = self.run_with_page(), self.args(url="https://law.example/other")
        self.assert_rejected(run, args, "not read")

    def test_rejects_short_excerpt(self):
        run, args = self.run_with_page(), self.args(excerpt="waterproofing")
        self.assert_rejected(run, args, "too short")

    def test_rejects_reused_id(self):
        run, args = self.run_with_page(), self.args()
        run.handle("save_source", args)
        self.assert_rejected(run, args, "already used")

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
        run.handle("submit_assessment", {"landlord_duty": [], "similar_cases": [{"text": "x", "source_ids": ["R1"]}]})
        with self.assertRaisesRegex(RuntimeError, "already submitted"):
            run.handle("submit_assessment", {"landlord_duty": []})

    def test_no_similar_cases_is_sent_back_once_with_research_on(self):
        run = agent.HazardRun(FakeResearch(), {}, deep=True)
        with self.assertRaisesRegex(RuntimeError, "similar_cases is empty"):
            run.handle("submit_assessment", {"landlord_duty": [], "similar_cases": []})
        self.assertEqual(run.handle("submit_assessment", {"landlord_duty": [], "similar_cases": []}), "Submitted. You are done.")

    def test_unknown_tool(self):
        run = agent.HazardRun(FakeResearch(), {})
        with self.assertRaisesRegex(RuntimeError, "unknown tool"):
            run.handle("delete_everything", {})


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


class NationwideTest(unittest.TestCase):
    TEXAS = {"case_id": "tx_1", "address": "100 Congress Ave, Austin, TX 78701",
             "hazards": [{"hazard": "water_leak", "location": "kitchen", "confidence": 0.9, "confirmed_by_tenant": True}]}

    def test_jurisdiction_comes_from_the_geocoder_when_missing(self):
        geo = {"state": "TX", "city": "Austin", "county": "Travis County"}
        with mock.patch.object(agent, "geocode", return_value=geo) as g:
            j = agent.resolve_jurisdiction(self.TEXAS)
        g.assert_called_once()
        self.assertEqual(j, {"state": "TX", "city": "Austin", "county": "Travis County"})

    def test_given_jurisdiction_is_kept_without_geocoding(self):
        with mock.patch.object(agent, "geocode") as g:
            self.assertEqual(agent.resolve_jurisdiction(CASE), OAKLAND)
        g.assert_not_called()

    def test_state_pack_is_the_fallback(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "tx.json").write_text(json.dumps({"pack_id": "tx", "jurisdiction": {"state": "TX", "city": ""}}))
            Path(tmp, "tx_austin.json").write_text(json.dumps({"pack_id": "tx-austin", "jurisdiction": {"state": "TX", "city": "Austin"}}))
            with mock.patch.object(agent, "PACKS", Path(tmp)):
                self.assertEqual(agent.find_pack({"state": "TX", "city": "Austin"})["pack_id"], "tx-austin")
                self.assertEqual(agent.find_pack({"state": "TX", "city": "Dallas"})["pack_id"], "tx")
                self.assertIsNone(agent.find_pack({"state": "OK", "city": "Tulsa"}))

    def test_prior_sources_from_this_state(self):
        rows = [
            {"url": "https://statutes.capitol.texas.gov/92.052", "cite": "Tex. Prop. Code 92.052", "kind": "statute", "jurisdiction": "Dallas, TX", "excerpt": "A landlord shall make a diligent effort to repair", "fetched_at": [2]},
            {"url": "https://austintexas.gov/code", "cite": "Austin code 25-12", "kind": "ordinance", "jurisdiction": "Austin, TX", "excerpt": "Austin property maintenance", "fetched_at": [1]},
            {"url": "https://houston.gov/code", "cite": "Houston code", "kind": "ordinance", "jurisdiction": "Houston, TX", "excerpt": "other city", "fetched_at": [3]},
            {"url": "https://leginfo.ca.gov/1941", "cite": "Cal. Civ. Code 1941", "kind": "statute", "jurisdiction": "Oakland, CA", "excerpt": "other state", "fetched_at": [4]},
            {"url": "https://taken.example", "cite": "Already a pack source", "kind": "statute", "jurisdiction": "Austin, TX", "excerpt": "x", "fetched_at": [5]},
        ]
        with mock.patch.object(agent, "sql", return_value=rows):
            prior = agent.prior_sources({"state": "TX", "city": "Austin"}, {"P1": {"url": "https://taken.example"}})
        self.assertEqual(sorted(s["cite"] for s in prior.values()), ["Austin code 25-12", "Tex. Prop. Code 92.052"])
        self.assertTrue(all(k.startswith("L") and s["prior"] and not s["verified"] for k, s in prior.items()))

    def test_prior_sources_skip_quietly_when_the_db_is_down(self):
        import urllib.error

        with mock.patch.object(agent, "sql", side_effect=urllib.error.URLError("down")):
            self.assertEqual(agent.prior_sources({"state": "TX", "city": "Austin"}, {}), {})
        self.assertEqual(agent.prior_sources({}, {}), {})

    def test_prompt_without_a_pack_points_at_state_law_and_courts(self):
        case = {**self.TEXAS, "jurisdiction": {"state": "TX", "city": "Austin"}}
        prior = {"L1": {"cite": "Tex. Prop. Code 92.052", "jurisdiction": "Dallas, TX", "text": "diligent effort to repair", "prior": True}}
        text = agent.describe(case, case["hazards"][0], None, prior, None, deep=True)
        self.assertIn("No verified pack covers Austin, TX", text)
        self.assertIn("supreme court: tex", text)
        self.assertIn("L1: Tex. Prop. Code 92.052", text)

    def test_prior_sources_are_citable_but_not_saved_again(self):
        calls = []
        prior = {"L1": {"cite": "Tex. Prop. Code 92.052", "url": "https://statutes.capitol.texas.gov/92.052", "kind": "statute", "verified": False, "text": "diligent effort to repair", "prior": True, "jurisdiction": "Dallas, TX"}}

        def llm(*_a):
            return {"landlord_duty": [{"text": "Texas landlords must make a diligent effort to repair.", "source_ids": ["L1"]}]}, {"calls": 1}

        case = {**self.TEXAS, "jurisdiction": {"state": "TX", "city": "Austin"}}
        with mock.patch.dict(agent.os.environ, {"LEGAL_RESEARCH": "1"}), \
             mock.patch.object(agent, "call_reducer", side_effect=lambda n, a: calls.append((n, a))), \
             mock.patch.object(agent, "sql", return_value=[]), \
             mock.patch.object(agent, "prior_sources", return_value=prior), \
             mock.patch.object(agent, "run_llm", side_effect=llm):
            out = agent.assess_case(case, save=True, client=object(), research=FakeResearch())
        self.assertEqual(out["assessments"][0]["landlord_duty"][0]["source_ids"], ["L1"])
        self.assertEqual(out["pack_id"], None)
        self.assertNotIn("add_legal_source", [n for n, _ in calls])


class FakeClient:
    """Records the request and answers with one submit_assessment call."""

    def __init__(self, draft):
        self.requests = []
        self.draft = draft
        self.beta = self
        self.messages = self

    def create(self, **kwargs):
        self.requests.append(kwargs)
        block = mock.Mock(type="tool_use", id="t1", input=self.draft)
        block.name = "submit_assessment"
        usage = mock.Mock(input_tokens=1, cache_read_input_tokens=0, cache_creation_input_tokens=0, output_tokens=1)
        return mock.Mock(stop_reason="tool_use", content=[block], usage=usage)


class PackOnlyDefaultTest(unittest.TestCase):
    """LEGAL_RESEARCH off (the default): verified pack only, one call, no research."""

    def setUp(self):
        patcher = mock.patch.dict(agent.os.environ, {"LEGAL_RESEARCH": ""})
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_case(self, case):
        calls = []
        client = FakeClient({"landlord_duty": [{"text": "Dampness can make a unit substandard.", "source_ids": ["P1"]}]})
        with mock.patch.object(agent, "call_reducer", side_effect=lambda n, a: calls.append((n, a))), \
             mock.patch.object(agent, "sql", side_effect=AssertionError("prior sources must not be read")) as sql, \
             mock.patch.object(agent, "ensure_case"):
            out = agent.assess_case(case, save=True, client=client, research=None)
        return out, calls, client, sql

    def test_off_by_default(self):
        with mock.patch.dict(agent.os.environ, {}, clear=True):
            self.assertFalse(agent.research_enabled())
        for on in ["1", "true", "ON"]:
            with mock.patch.dict(agent.os.environ, {"LEGAL_RESEARCH": on}):
                self.assertTrue(agent.research_enabled())

    def test_pack_case_uses_one_call_with_only_the_submit_tool(self):
        case = {**CASE, "hazards": CASE["hazards"][:1]}
        out, calls, client, _ = self.run_case(case)
        self.assertEqual(len(client.requests), 1)
        self.assertEqual([t["name"] for t in client.requests[0]["tools"]], ["submit_assessment"])
        self.assertIn("use only the verified jurisdiction pack", client.requests[0]["system"])
        self.assertEqual(out["assessments"][0]["landlord_duty"][0]["source_ids"], ["P1"])
        self.assertEqual([n for n, _ in calls if n in ("start_run", "finish_run")], ["start_run", "finish_run"])

    def test_no_pack_means_not_verified_and_no_model_call(self):
        case = {"case_id": "tx_1", "address": "100 Congress Ave, Austin, TX", "jurisdiction": {"state": "TX", "city": "Austin"},
                "hazards": [{"hazard": "water_leak", "location": "kitchen", "confidence": 0.9, "confirmed_by_tenant": True}]}
        out, calls, client, _ = self.run_case(case)
        self.assertEqual(out["not_assessed"], [{"hazard": "water_leak", "reason": "not verified for your area"}])
        self.assertEqual((out["assessments"], out["legal_aid"], out["pack_id"]), ([], [], None))
        self.assertEqual(client.requests, [])
        self.assertNotIn("start_run", [n for n, _ in calls])

    def test_hazard_missing_from_the_pack_is_not_verified(self):
        pack = {**agent.find_pack(OAKLAND), "hazard_laws": [h for h in agent.find_pack(OAKLAND)["hazard_laws"] if h["hazard"] != "water_leak"]}
        with mock.patch.object(agent, "find_pack", return_value=pack):
            out, _, client, _ = self.run_case(CASE)
        self.assertIn({"hazard": "water_leak", "reason": "not verified for your area"}, out["not_assessed"])
        self.assertEqual([a["hazard"] for a in out["assessments"]], ["dampness_mold"])
        self.assertEqual(len(client.requests), 1)

    def test_research_on_restores_the_tools(self):
        with mock.patch.dict(agent.os.environ, {"LEGAL_RESEARCH": "1"}):
            client = FakeClient({"similar_cases": [{"text": "x", "source_ids": ["P1"]}]})
            sources, law = agent.pack_sources(agent.find_pack(OAKLAND), "dampness_mold")
            agent.run_llm(client, FakeResearch(), CASE, CASE["hazards"][0], agent.find_pack(OAKLAND), sources, law)
        self.assertIn("web_search", [t["name"] for t in client.requests[0]["tools"]])


class LoadCaseTest(unittest.TestCase):
    def test_loads_a_case_inside_the_repo(self):
        self.assertEqual(agent.load_case(str(agent.ROOT / "agents/legal/cases/mold_oakland.json"))["case_id"], CASE["case_id"])

    def test_rejects_paths_outside_the_repo_or_not_json(self):
        for bad in ["/etc/passwd", str(agent.ROOT / ".." / "outside.json"), str(agent.ROOT / "agents/legal/agent.py"), str(agent.ROOT / "missing.json")]:
            with self.subTest(path=bad), self.assertRaises(SystemExit):
                agent.load_case(bad)


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
