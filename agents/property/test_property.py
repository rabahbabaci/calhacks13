"""Offline tests for the property agent: no API keys, network or database needed.

Run from the repo root: data/.venv/bin/python -m unittest discover -s agents/property -p "test_*.py"
"""
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

import agent  # noqa: E402
import sources as src  # noqa: E402
from geocode import parse_house_number  # noqa: E402
from known import hints_for  # noqa: E402
from validate import date_variants, validate  # noqa: E402

RAW = {
    "D1": json.dumps([{"violationid": "19324223", "class": "C", "inspectiondate": "2026-10-22T00:00:00.000", "novdescription": "WATER LEAK AT THE CEILING"}]),
    "D2": json.dumps([{"REAC_LAST_INSPECTION_SCORE": "62", "REAC_LAST_INSPECTION_DATE": 1778112000000, "MGMT_AGENT_ORG_NAME": "HumanGood Affordable Housing"}]),
    "D3": json.dumps([{"type": "CorporateOwner", "corporationname": "1106 REMSEN REALTY LLC"}]),
}


def record(**over):
    base = {"id": "R1", "data_id": "D1", "kind": "violation", "title": "Class C water leak", "date": "2026-10-22", "status": "open", "detail": "", "ref": "19324223"}
    return {**base, **over}


class ValidateTest(unittest.TestCase):
    def test_keeps_grounded_record_by_ref_or_date(self):
        out, dropped = validate({"records": [record(), record(id="R2", ref="", date="2026-10-22")]}, RAW)
        self.assertEqual([r["id"] for r in out["records"]], ["R1", "R2"])
        self.assertEqual(dropped, [])

    def test_drops_records_not_in_the_cited_data(self):
        cases = [
            (record(data_id="D9"), "unknown data_id"),
            (record(ref="99999999", date="2020-01-01"), "neither ref nor date"),
            (record(kind="rumor"), "unknown kind"),
            (record(title=""), "empty title"),
        ]
        for rec, reason in cases:
            with self.subTest(reason=reason):
                out, dropped = validate({"records": [rec]}, RAW)
                self.assertEqual(out["records"], [])
                self.assertIn(reason, dropped[0]["reason"])

    def test_arcgis_epoch_dates_ground_a_record(self):
        out, _ = validate({"records": [record(data_id="D2", kind="inspection", ref="", date="2026-05-07")]}, RAW)
        self.assertEqual(len(out["records"]), 1)
        self.assertIn("1778112000000", date_variants("2026-05-07"))

    def test_findings_must_cite_kept_records_and_not_accuse(self):
        draft = {
            "records": [record(), record(id="R2", ref="nope", date="1999-01-01")],
            "patterns": [{"text": "Repeated leak violations in 2026.", "record_ids": ["R1"]}],
            "unresolved": [
                {"text": "Leak violation still open.", "record_ids": ["R2"]},
                {"text": "The landlord is a negligent slumlord.", "record_ids": ["R1"]},
            ],
            "summary": "The building has an open Class C leak violation.",
        }
        out, dropped = validate(draft, RAW)
        self.assertEqual(len(out["patterns"]), 1)
        self.assertEqual(out["unresolved"], [])
        self.assertEqual(sorted(d["reason"] for d in dropped if d["field"] == "unresolved"), ["accusatory language", "cites no kept record"])

    def test_record_ids_are_renumbered_after_drops(self):
        draft = {"records": [record(id="R1", data_id="D9"), record(id="R2")], "patterns": [{"text": "Leak.", "record_ids": ["R2"]}]}
        out, _ = validate(draft, RAW)
        self.assertEqual(out["records"][0]["id"], "R1")
        self.assertEqual(out["patterns"][0]["record_ids"], ["R1"])

    def test_record_categories_are_not_accusations(self):
        draft = {"records": [record()], "patterns": [
            {"text": "Illegal dumping and tenant harassment complaints recur.", "record_ids": ["R1"]},
            {"text": "The owner illegally converted units.", "record_ids": ["R1"]},
        ]}
        out, dropped = validate(draft, RAW)
        self.assertEqual([p["text"] for p in out["patterns"]], ["Illegal dumping and tenant harassment complaints recur."])
        self.assertEqual(dropped[0]["reason"], "accusatory language")

    def test_accusatory_summary_is_cleared(self):
        out, dropped = validate({"summary": "The owner acted in bad faith."}, RAW)
        self.assertEqual(out["summary"], "")
        self.assertEqual(dropped[0]["reason"], "accusatory language")

    def test_owner_must_appear_in_its_data(self):
        out, _ = validate({"owner": {"name": "1106 Remsen Realty LLC", "data_id": "D3"}, "manager": {"name": "Someone Else", "data_id": "D3"}}, RAW)
        self.assertEqual(out["owner"]["name"], "1106 Remsen Realty LLC")
        self.assertIsNone(out["manager"])

    def test_owner_by_mailing_address_when_no_name_is_published(self):
        raw = {"D1": json.dumps([{"FULL_OWNERADDRESS": "411 2ND ST OAKLAND CA 94607"}])}
        out, _ = validate({"owner": {"name": "Unknown LLC", "mailing_address": "411 2nd St Oakland CA 94607", "data_id": "D1"}}, raw)
        self.assertEqual(out["owner"], {"name": "", "role": "", "mailing_address": "411 2nd St Oakland CA 94607", "data_id": "D1"})


class SourceChecksTest(unittest.TestCase):
    def test_domains(self):
        self.assertEqual(src.check_domain("Data.CityOfChicago.org"), "data.cityofchicago.org")
        for bad in ["localhost", "127.0.0.1", "https://data.seattle.gov", "data.seattle.gov/x", "printer.local", ""]:
            with self.subTest(domain=bad), self.assertRaises(src.SourceError):
                src.check_domain(bad)

    def test_layer_urls(self):
        good = "https://services.arcgis.com/abc/arcgis/rest/services/Parcels/FeatureServer/0"
        self.assertEqual(src.check_layer_url(good), good)
        for bad in [
            "http://services.arcgis.com/a/FeatureServer/0",
            "https://10.0.0.5/arcgis/rest/services/a/FeatureServer/0",
            "https://localhost/a/FeatureServer/0",
            "https://example.com/a/FeatureServer/0?token=x",
            "https://example.com/a/FeatureServer",
        ]:
            with self.subTest(url=bad), self.assertRaises(src.SourceError):
                src.check_layer_url(bad)

    def test_dataset_ids_and_sql_literals(self):
        sources = src.Sources.__new__(src.Sources)
        with self.assertRaises(src.SourceError):
            sources.query_dataset("data.cityofnewyork.us", "../../etc", "1=1")
        self.assertEqual(src.sql_literal("O'Brien"), "'O''Brien'")

    def test_accela_inputs_are_checked(self):
        sources = src.Sources.__new__(src.Sources)
        with self.assertRaises(src.SourceError):
            sources.accela("OAK/../x", "Enforcement", "1", "MAIN")
        with self.assertRaises(src.SourceError):
            sources.accela("OAKLAND", "Everything", "1", "MAIN")


class AccelaRowsTest(unittest.TestCase):
    ROWS = [  # as Oakland's portal returned them, trimmed
        ["Showing 1-10 of 24\t|\tDownload results", "Showing 1-10 of 24", "|", "Download results"],
        ["Showing 1-10 of 24", "|", "Download results"],
        ["", "Filed Date", "Record Number", "Record Type", "Address", "Description", "Project Name", "Status", "Action", ""],
        ["", "10/16/2026", "2606851", "Housing Habitability Complaint", "1510 WEBSTER ST, 603, Oakland", "x" * 500, "", "OP-Insp-No Entry", "", ""],
        ["", "< Prev", "1", "2", "3", "Next >"],
        ["", "Filed Date", "Record Number", "Record Type", "Address", "Description", "Project Name", "Status", "Action", ""],
        ["", "09/17/2026", "2606100", "Blight - Facility Complaint", "1510 WEBSTER ST, Oakland", "weeds", "", "Closed", "", ""],
    ]

    def test_finds_the_header_skips_banners_pagers_and_repeats(self):
        records = src.parse_accela_rows(self.ROWS)
        self.assertEqual([r["Record Number"] for r in records], ["2606851", "2606100"])
        self.assertEqual(records[0]["Status"], "OP-Insp-No Entry")
        self.assertEqual(len(records[0]["Description"]), 240)

    def test_no_header_means_no_records(self):
        self.assertEqual(src.parse_accela_rows([["no records found"]]), [])


class HintsTest(unittest.TestCase):
    def test_brooklyn_gets_nyc_hints_through_postal_city(self):
        hints = hints_for({"state": "NY", "city": "New York", "postal_city": "Brooklyn"})
        self.assertTrue(any("wvxf-dwi5" in h for h in hints))

    def test_unknown_city_gets_none(self):
        self.assertEqual(hints_for({"state": "WY", "city": "Cheyenne"}), [])
        self.assertEqual(hints_for(None), [])

    def test_house_number_from_input(self):
        self.assertEqual(parse_house_number("1106 Remsen Ave, Brooklyn"), "1106")
        self.assertEqual(parse_house_number("12-14 Main St"), "12-14")
        self.assertEqual(parse_house_number("Main St"), "")


class FakeWeb:
    has_courtlistener = False
    pages = {}

    def search(self, query, max_results=3):
        return [{"url": "https://news.example/a", "title": "t", "text": "x"}]

    def read(self, url):
        return ("pdf", "AAAA") if url.endswith(".pdf") else ("pages", [{"url": url, "title": "t", "text": "x"}])


class FakeSources(src.Sources):
    def __init__(self, rows=None):
        self.web = FakeWeb()
        self.results = {}
        self.rows = rows if rows is not None else [{"violationid": "19324223", "inspectiondate": "2026-10-22T00:00:00.000"}]

    def hud_assisted(self, geo):
        return []

    def query_dataset(self, *a, **k):
        return self.rows


class RunTest(unittest.TestCase):
    def test_budget_and_refunds(self):
        run = agent.PropertyRun(FakeSources())
        for _ in range(agent.DATA_BUDGET):
            run.handle("query_dataset", {"domain": "d.example.gov", "dataset_id": "abcd-1234", "where": "1=1"})
        with self.assertRaisesRegex(src.SourceError, "budget"):
            run.handle("query_dataset", {"domain": "d.example.gov", "dataset_id": "abcd-1234", "where": "1=1"})

    def test_empty_results_are_refunded(self):
        run = agent.PropertyRun(FakeSources(rows=[]))
        for _ in range(agent.DATA_BUDGET + 2):
            run.handle("query_dataset", {"domain": "d.example.gov", "dataset_id": "abcd-1234", "where": "1=1"})
        self.assertEqual(run.used, 0)

    def test_results_get_data_ids_and_records_must_cite_one(self):
        run = agent.PropertyRun(FakeSources())
        text = run.handle("query_dataset", {"domain": "d.example.gov", "dataset_id": "abcd-1234", "where": "1=1"})
        self.assertTrue(text.startswith("data_id D1"))
        self.assertEqual(run.handle("save_record", record()), "Saved R1.")
        with self.assertRaisesRegex(src.SourceError, "unknown data_id"):
            run.handle("save_record", record(data_id="D7"))

    def test_pdfs_are_refused(self):
        run = agent.PropertyRun(FakeSources())
        with self.assertRaisesRegex(src.SourceError, "PDF"):
            run.handle("read_page", {"url": "https://city.example/report.pdf"})
        self.assertEqual(run.used, 0)

    def test_early_submit_is_sent_back_once_then_accepted(self):
        run = agent.PropertyRun(FakeSources())
        report = {"summary": "", "patterns": [], "unresolved": [], "coverage": []}
        with self.assertRaisesRegex(src.SourceError, "Not submitted yet"):
            run.handle("submit_report", report)
        self.assertEqual(run.handle("submit_report", report), "Submitted. You are done.")
        with self.assertRaisesRegex(src.SourceError, "already submitted"):
            run.handle("submit_report", report)

    def test_submit_after_enough_queries_is_accepted_first_time(self):
        run = agent.PropertyRun(FakeSources())
        for _ in range(agent.MIN_QUERIES):
            run.handle("query_dataset", {"domain": "d.example.gov", "dataset_id": "abcd-1234", "where": "1=1"})
        self.assertEqual(run.handle("submit_report", {"summary": "", "patterns": [], "unresolved": [], "coverage": []}), "Submitted. You are done.")


GEO = {"matched_address": "1106 REMSEN AVE, BROOKLYN, NY, 11236", "house_number": "1106", "street_name": "REMSEN", "street_suffix": "AVE",
       "city": "New York", "postal_city": "Brooklyn", "county": "Kings County", "county_fips": "36047", "state": "NY", "zip": "11236"}
CASE = {"case_id": "property_case_001", "address": "1106 Remsen Ave, Brooklyn, NY 11236"}


def scripted_llm(client, sources, address, geo):
    sources.keep("HUD assisted multifamily", [])
    sources.keep("NYC HPD", json.loads(RAW["D1"]))
    draft = {
        "records": [record(data_id="D2")],
        "owner": None,
        "summary": "One open Class C water leak violation.",
        "patterns": [],
        "unresolved": [{"text": "A Class C leak violation is open.", "record_ids": ["R1"]}],
        "coverage": [{"source": "NYC HPD violations", "result": "1 violation"}],
    }
    return draft, {"calls": 1}


class AssessTest(unittest.TestCase):
    def run_assess(self, llm=scripted_llm, existing_case=False, save=True):
        calls = []
        with mock.patch.object(agent, "geocode", return_value=GEO), \
             mock.patch.object(agent, "call_reducer", side_effect=lambda n, a: calls.append((n, a))), \
             mock.patch.object(agent, "sql", return_value=[{"case_id": CASE["case_id"]}] if existing_case else []), \
             mock.patch.object(agent, "run_llm", side_effect=llm):
            out = agent.assess(CASE, save=save, client=object(), sources=FakeSources())
        return out, calls

    def test_run_lifecycle_and_report(self):
        out, calls = self.run_assess()
        self.assertEqual([n for n, _ in calls], ["create_case", "start_run", "finish_run"])
        self.assertEqual(calls[1][1], [CASE["case_id"], "property", ""])
        report = json.loads(calls[2][1][3])
        self.assertEqual(report["jurisdiction"], {"city": "New York", "county": "Kings County", "state": "NY", "zip": "11236"})
        self.assertEqual(len(report["records"]), 1)
        self.assertEqual(report["unresolved"][0]["record_ids"], ["R1"])
        self.assertIn("public records", report["disclaimer"])

    def test_existing_case_not_overwritten(self):
        _, calls = self.run_assess(existing_case=True)
        self.assertNotIn("create_case", [n for n, _ in calls])

    def test_failure_marks_run_failed_and_raises(self):
        def broken(*a):
            raise RuntimeError("model never called submit_report")

        with self.assertRaises(RuntimeError):
            self.run_assess(llm=broken)
        calls = []
        with mock.patch.object(agent, "geocode", return_value=GEO), \
             mock.patch.object(agent, "call_reducer", side_effect=lambda n, a: calls.append((n, a))), \
             mock.patch.object(agent, "sql", return_value=[]), \
             mock.patch.object(agent, "run_llm", side_effect=broken), \
             self.assertRaises(RuntimeError):
            agent.assess(CASE, save=True, client=object(), sources=FakeSources())
        self.assertEqual(calls[-1][0], "fail_run")

    def test_no_save_makes_no_database_calls(self):
        out, calls = self.run_assess(save=False)
        self.assertEqual(calls, [])
        self.assertEqual(out["report"]["matched_address"], GEO["matched_address"])


class CliTest(unittest.TestCase):
    def test_address_flag_builds_a_case(self):
        case = agent.case_from_args(["--address", "7951 MacArthur Blvd, Oakland, CA", "--no-save"])
        self.assertEqual(case["address"], "7951 MacArthur Blvd, Oakland, CA")
        self.assertTrue(case["case_id"].startswith("property_7951_macarthur"))

    def test_case_files_must_be_inside_the_repo(self):
        for bad in ["/etc/passwd", str(agent.ROOT / ".." / "x.json")]:
            with self.subTest(path=bad), self.assertRaises(SystemExit):
                agent.case_from_args([bad])


if __name__ == "__main__":
    unittest.main()
