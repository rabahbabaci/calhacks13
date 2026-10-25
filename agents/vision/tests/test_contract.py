"""The vision agent's case_input must go straight into the health agent's assess_case() unchanged."""
import importlib.util

import pytest

import agent
from conftest import FakeClient, load_fixture, make_frames

SECTION_2_KEYS = {"hazard", "location", "confidence", "confirmed_by_tenant", "evidence_ids"}
SECTION_3_KEYS = {"case_id", "address", "jurisdiction", "hazards", "household"}


def load_health_agent():
    path = agent.ROOT / "agents" / "health" / "agent.py"
    spec = importlib.util.spec_from_file_location("health_agent", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def vision_out(tmp_path, case):
    fx = load_fixture("unit_two_segments")
    frames = make_frames(tmp_path, [("video_1_t010s", 10), ("video_1_t011s", 11), ("video_1_t012s", 12)])
    return agent.analyze(case, [{"type": "video", "frames": frames}], client=FakeClient(fx["segment_1"]), live=True)


def test_case_input_shape(vision_out, case):
    ci = vision_out["case_input"]
    assert SECTION_3_KEYS <= set(ci) <= SECTION_3_KEYS | {"notes"}
    assert ci["notes"] == case["notes"]
    assert ci["household"] == {"children": True, "older_adults": False, "pregnancy": False, "breathing_condition": False}
    for h in ci["hazards"]:
        assert set(h) == SECTION_2_KEYS
        assert h["confirmed_by_tenant"] is False
        assert isinstance(h["confidence"], float)


def test_report_shape(vision_out, case):
    rep = vision_out["vision_report"]
    assert set(rep) >= {"case_id", "source", "frames", "hazards", "unlabeled_observations",
                        "not_visible_from_video", "dropped", "usage"}
    assert rep["notes"] == case["notes"]
    for h in rep["hazards"]:
        assert h["legal"]["pack_id"] == "ca-oakland"
        assert h["legal"]["citations"]


class HealthFake:
    """Plays the health agent's two-call tool loop: search_sources, then save_assessment."""

    def __init__(self):
        self.messages = self

    def create(self, **kw):
        last = kw["messages"][-1]["content"]
        if isinstance(last, str):
            return Resp([Block("tool_use", "search_sources", {"hazard": "dampness_mold"}, "t1")], "tool_use")
        draft = {"hazard": "dampness_mold", "exposure_pathways": [{"text": "Breathing in spores.", "chunk_ids": [1]}],
                 "health_risks": [], "vulnerable_groups": [], "precautions": [], "see_a_doctor_if": []}
        return Resp([Block("tool_use", "save_assessment", draft, "t2")], "tool_use")


class Block:
    def __init__(self, type_, name, input_, id_):
        self.type, self.name, self.input, self.id = type_, name, input_, id_


class Usage:
    input_tokens = 10
    output_tokens = 5


class Resp:
    def __init__(self, content, stop_reason):
        self.content, self.stop_reason, self.usage = content, stop_reason, Usage()


def test_health_agent_consumes_case_input_unchanged(vision_out, monkeypatch):
    health = load_health_agent()
    chunks = [{"id": 1, "source_url": "https://www.cdc.gov/mold", "hazard": "dampness_mold", "ordinal": 0, "text": "Mold..."}]
    sources = [{"url": "https://www.cdc.gov/mold", "title": "Mold", "publisher": "CDC"}]
    monkeypatch.setattr(health, "fetch_corpus", lambda hazard: (chunks, sources))
    result = health.assess_case(vision_out["case_input"], save=False, client=HealthFake())
    by_tag = {a["hazard"]: a for a in result["assessments"]}
    assert set(by_tag) == {"dampness_mold", "exposed_wiring"}
    assert by_tag["exposed_wiring"]["urgent"] is True
    assert by_tag["dampness_mold"]["exposure_pathways"][0]["chunk_ids"] == [1]
    assert result["not_assessed"] == []
