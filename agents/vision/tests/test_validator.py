import copy
import time

import pytest

import agent
import rules
from conftest import FakeClient, load_fixture, make_frames

SEG1 = [("video_1_t010s", 10), ("video_1_t011s", 11), ("video_1_t012s", 12)]
SEG2 = [("video_1_t100s", 100), ("video_1_t101s", 101)]


def raw(fixture, seg):
    return copy.deepcopy(fixture[seg]["content"][0]["input"])


@pytest.fixture
def fx():
    return load_fixture("unit_two_segments")


def base_hazard(**kw):
    h = {"hazard": "water_leak", "location": "kitchen ceiling", "room": "kitchen",
         "evidence_ids": ["video_1_t010s"], "first_seen_s": 10, "observed_signs": ["brown ring stain"],
         "severity": "low", "severity_reason": "stain", "extent": "spot", "confidence": 0.7,
         "needs_professional_check": False, "check_reason": ""}
    h.update(kw)
    return h


def run_one(tmp_path, **kw):
    frames = make_frames(tmp_path, SEG1)
    return agent.validate({"hazards": [base_hazard(**kw)]}, frames)


@pytest.mark.parametrize("kw, reason", [
    ({"hazard": "asbestos"}, "unknown tag"),
    ({"evidence_ids": []}, "no evidence_ids"),
    ({"evidence_ids": ["video_1_t010s", "video_1_t050s"]}, "evidence_id not in frames sent"),
    ({"observed_signs": ["  "]}, "empty observed_signs"),
    ({"bbox": [0.2, 0.2, 1.3, 0.5]}, "bbox outside 0-1"),
    ({"bbox": [0.5, 0.2, 0.4, 0.5]}, "bbox outside 0-1"),
    ({"bbox": [0.1, 0.2]}, "bbox outside 0-1"),
])
def test_drop_reasons(tmp_path, kw, reason):
    hazards, _, dropped = run_one(tmp_path, **kw)
    assert hazards == []
    assert len(dropped) == 1 and dropped[0]["reason"].startswith(reason)


@pytest.mark.parametrize("text", [
    "This is mold on the ceiling",
    "You can withhold rent until it is fixed",
    "you have a case against the landlord",
    "The landlord must fix this within 30 days",
    "Photos are court-ready",
    "toxic mold visible",
])
def test_never_say_drops(tmp_path, text):
    hazards, _, dropped = run_one(tmp_path, observed_signs=[text])
    assert hazards == [] and dropped[0]["reason"].startswith("never_say")


def test_never_say_in_other_text_fields(tmp_path):
    hazards, _, dropped = run_one(tmp_path, severity_reason="it is mold, clearly")
    assert hazards == [] and "never_say" in dropped[0]["reason"]


def test_lead_banned_wording(tmp_path):
    hazards, _, dropped = run_one(tmp_path, hazard="lead_paint", observed_signs=["peeling paint that contains lead"])
    assert hazards == [] and dropped[0]["reason"].startswith("banned for lead_paint")


def test_possible_mold_wording_allowed(tmp_path):
    hazards, _, dropped = run_one(tmp_path, hazard="dampness_mold", observed_signs=["dark spotting, possible mold growth"])
    assert len(hazards) == 1 and not dropped
    assert hazards[0]["label"] == "Visible dampness or possible mold growth"


@pytest.mark.parametrize("given, expected", [(1.7, 1.0), (-2, 0.0), ("high", 0.0), (0.42, 0.42)])
def test_confidence_clamp(tmp_path, given, expected):
    hazards, *_ = run_one(tmp_path, confidence=given)
    assert hazards[0]["confidence"] == expected


@pytest.mark.parametrize("tag", sorted(rules.URGENT_TAGS))
@pytest.mark.parametrize("sev", ["low", "moderate", "high", "urgent", "bogus"])
def test_urgent_override_never_lowered(tmp_path, tag, sev):
    hazards, *_ = run_one(tmp_path, hazard=tag, severity=sev, confidence=0.1)
    assert hazards[0]["severity"] == "urgent"
    assert hazards[0]["needs_professional_check"] is True


def test_low_confidence_urgent_still_reported(tmp_path):
    hazards, _, dropped = run_one(tmp_path, hazard="gas_leak", confidence=0.05)
    assert len(hazards) == 1 and not dropped


def test_dampness_rubric_floors(tmp_path):
    assert run_one(tmp_path, hazard="dampness_mold", location="bathroom ceiling")[0][0]["severity"] == "high"
    assert run_one(tmp_path, hazard="dampness_mold", location="kitchen wall", extent="patch")[0][0]["severity"] == "moderate"
    assert run_one(tmp_path, hazard="dampness_mold", location="kitchen wall", extent="widespread")[0][0]["severity"] == "high"
    assert run_one(tmp_path, hazard="dampness_mold", location="kitchen wall", severity="high")[0][0]["severity"] == "high"


def test_wet_surface_exclusion(tmp_path):
    h = run_one(tmp_path, hazard="dampness_mold", location="shower wall grout", extent="patch", severity="moderate")[0][0]
    assert h["severity"] == "low" and h["wet_surface"] is True
    h = run_one(tmp_path, hazard="dampness_mold", location="shower walls and tub", extent="widespread")[0][0]
    assert h["severity"] == "moderate"
    h = run_one(tmp_path, hazard="dampness_mold", location="bathroom ceiling above shower", extent="spot")[0][0]
    assert h["severity"] == "high" and h["wet_surface"] is False


def test_lead_paint_fixed_moderate(tmp_path):
    h = run_one(tmp_path, hazard="lead_paint", severity="high", observed_signs=["peeling paint on sill"])[0][0]
    assert h["severity"] == "moderate" and h["needs_professional_check"] and h["check_reason"]


def test_first_seen_from_frames_not_model(tmp_path):
    h = run_one(tmp_path, evidence_ids=["video_1_t012s", "video_1_t011s"], first_seen_s=999)[0][0]
    assert h["first_seen_s"] == 11
    assert h["evidence_ids"] == ["video_1_t011s", "video_1_t012s"]


def test_confirmed_by_tenant_always_false(tmp_path):
    h = run_one(tmp_path, confirmed_by_tenant=True)[0][0]
    assert h["confirmed_by_tenant"] is False


def test_fixture_segment(tmp_path, fx):
    frames = make_frames(tmp_path, SEG1)
    hazards, unlabeled, dropped = agent.validate(raw(fx, "segment_1"), frames)
    tags = [h["hazard"] for h in hazards]
    assert tags == ["dampness_mold", "exposed_wiring"]
    reasons = {d["item"].get("hazard", d["kind"]): d["reason"] for d in dropped}
    assert reasons["structural_crack"] == "unknown tag: structural_crack"
    assert reasons["water_leak"].startswith("evidence_id not in frames sent")
    assert [u["suggested_tag"] for u in unlabeled] == ["rust_corrosion"]
    assert any(d["kind"] == "unlabeled" for d in dropped)
    assert hazards[1]["severity"] == "urgent"
    assert hazards[0]["bbox"] == [0.1, 0.5, 0.4, 0.9] and hazards[0]["bbox_frame"] == "video_1_t011s"


def test_merge_across_segments(tmp_path, fx, case):
    s1, s2 = make_frames(tmp_path, SEG1), make_frames(tmp_path, SEG2)
    client = FakeClient(fx["segment_1"], fx["segment_2"])
    out = agent.analyze(case, [{"type": "video", "frames": s1}, {"type": "video", "frames": s2}],
                        client=client, live=True)
    rep = out["vision_report"]
    damp = [h for h in rep["hazards"] if h["hazard"] == "dampness_mold"]
    assert len(damp) == 1, "same tag + same room (case and spacing differ) must merge"
    d = damp[0]
    assert d["evidence_ids"] == ["video_1_t011s", "video_1_t100s", "video_1_t101s"]
    assert d["confidence"] == 1.0
    assert d["severity"] == "high"  # ceiling location from the higher-confidence segment
    assert d["first_seen_s"] == 11
    assert d["observed_signs"].count("dark spotting along the tile joint") == 1
    nv = {s.split(":")[0]: s for s in rep["not_visible_from_video"]}
    assert set(nv) == set(rules.TAGS) - {"dampness_mold", "exposed_wiring"}, "derived in code from what was found"
    assert "CO alarm" in nv["carbon_monoxide"]
    assert rep["rooms"] == [{"room": "bathroom",
                             "evidence_ids": ["video_1_t012s", "video_1_t011s", "video_1_t100s", "video_1_t101s"]}]
    assert rep["hazards"][0]["hazard"] == "exposed_wiring", "urgent sorts first"
    assert "911" in rep["hazards"][0]["urgent_message"]
    assert len(client.messages.calls) == 2, "one call per segment"


def test_room_key_ignores_parentheticals():
    assert agent._room_key("Bedroom (studio sleeping area)") == agent._room_key(" bedroom ")
    assert agent._room_key("unknown room (by the window)") == "unknown room"


def test_segments_run_in_parallel(tmp_path, fx, case):
    s1, s2 = make_frames(tmp_path, SEG1), make_frames(tmp_path, SEG2)
    client = FakeClient(fx["segment_1"], fx["segment_2"], delay=0.5)
    t0 = time.time()
    out = agent.analyze(case, [{"type": "video", "frames": s1}, {"type": "video", "frames": s2}],
                        client=client, live=True)
    assert time.time() - t0 < 0.9, "two 0.5s calls should overlap"
    assert [u["segment"] for u in out["vision_report"]["usage"]] == [0, 1], "results keep segment order"
    assert out["vision_report"]["wall_seconds"] < 0.9


def test_unknown_tag_never_in_outputs(tmp_path, fx, case):
    client = FakeClient(fx["segment_1"])
    out = agent.analyze(case, [{"type": "video", "frames": make_frames(tmp_path, SEG1)}], client=client, live=True)
    assert all(h["hazard"] in rules.TAGS for h in out["vision_report"]["hazards"])
    assert all(h["hazard"] in rules.TAGS for h in out["case_input"]["hazards"])
    assert any(d.get("hazard") == "structural_crack" for d in out["vision_report"]["dropped"])
