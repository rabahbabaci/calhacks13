import json
import urllib.error

import pytest

import agent
from conftest import FakeClient, load_fixture, make_frames
from stdb import StdbError

FRAMES = [("video_1_t010s", 10), ("video_1_t011s", 11), ("video_1_t012s", 12)]


@pytest.fixture
def report(tmp_path, case):
    fx = load_fixture("unit_two_segments")
    out = agent.analyze(case, [{"type": "video", "frames": make_frames(tmp_path, FRAMES)}],
                        client=FakeClient(fx["segment_1"]), live=True)
    return out["vision_report"]


# ---------- SpacetimeDB save path (contracts section 6) ----------

@pytest.fixture
def db(monkeypatch):
    """Records reducer calls; `rows` is what SELECT ... FROM hazard returns."""
    state = {"calls": [], "rows": []}
    monkeypatch.setattr(agent, "call_reducer", lambda name, args: state["calls"].append((name, args)))
    monkeypatch.setattr(agent, "sql", lambda q: state["rows"])
    return state


def test_save_adds_hazards_then_finishes_run(report, db):
    added = agent.save_detections(report)
    names = [c[0] for c in db["calls"]]
    assert names == ["add_hazard"] * len(report["hazards"]) + ["finish_run"]
    assert added == [h["hazard"] for h in report["hazards"]]
    for (_, args), h in zip(db["calls"], report["hazards"]):
        case_id, hazard, location, confidence, ids = args
        assert (case_id, hazard, location) == (report["case_id"], h["hazard"], h["location"])
        assert 0 <= confidence <= 1 and json.loads(ids) == h["evidence_ids"]
    case_id, agent_name, run_hazard, payload = db["calls"][-1][1]
    assert (case_id, agent_name, run_hazard) == (report["case_id"], "hazard", "")
    payload = json.loads(payload)
    assert payload["hazards"] == json.loads(json.dumps(report["hazards"]))
    assert all("path" not in f for f in payload["frames"]), "no local file paths in the shared db"


def test_save_skips_hazards_already_on_the_case(report, db):
    h0, h1 = report["hazards"][0], report["hazards"][1]
    db["rows"] = [
        {"hazard": h0["hazard"], "location": "somewhere else", "evidence_ids_json": json.dumps(h0["evidence_ids"][:1])},
        {"hazard": h1["hazard"], "location": h1["location"], "evidence_ids_json": "[]"},
        {"hazard": "gas_leak", "location": "kitchen", "evidence_ids_json": "[]"},
    ]
    added = agent.save_detections(report)
    assert added == [h["hazard"] for h in report["hazards"][2:]]
    assert [c[0] for c in db["calls"]][-1] == "finish_run"


def test_analyze_save_records_run(tmp_path, case, db):
    fx = load_fixture("unit_two_segments")
    out = agent.analyze(case, [{"type": "video", "frames": make_frames(tmp_path, FRAMES)}],
                        client=FakeClient(fx["segment_1"]), live=True, save=True)
    names = [c[0] for c in db["calls"]]
    assert names[:2] == ["create_case", "start_run"] and names[-1] == "finish_run"
    assert db["calls"][1][1] == [case["case_id"], "hazard", ""]
    assert json.loads(db["calls"][0][1][2])["case_id"] == case["case_id"]
    assert names.count("add_hazard") == len(out["vision_report"]["hazards"])


def test_analyze_save_fails_run_on_error(tmp_path, case, db):
    with pytest.raises(agent.NoCachedResponse):
        agent.analyze(case, [{"type": "video", "frames": make_frames(tmp_path, FRAMES)}],
                      client=FakeClient(), live=False, save=True)
    names = [c[0] for c in db["calls"]]
    assert names == ["create_case", "start_run", "fail_run"]
    assert db["calls"][-1][1][3].startswith("NoCachedResponse")


def test_save_530_rejection(report, monkeypatch):
    def boom(name, args):
        raise StdbError(530, "unknown case: case_x", f"https://x/call/{name}")
    monkeypatch.setattr(agent, "sql", lambda q: [])
    monkeypatch.setattr(agent, "call_reducer", boom)
    with pytest.raises(RuntimeError, match="add_hazard rejected"):
        agent.save_detections(report)


def test_save_server_down(report, monkeypatch):
    def down(*a):
        raise urllib.error.URLError("Connection refused")
    monkeypatch.setattr(agent, "sql", down)
    monkeypatch.setattr(agent, "call_reducer", down)
    with pytest.raises(agent.DataLayerDown):
        agent.save_detections(report)


def test_analyze_does_not_save_by_default(tmp_path, case, monkeypatch):
    monkeypatch.setattr(agent, "call_reducer", lambda *a: pytest.fail("saved without save=True"))
    monkeypatch.setattr(agent, "sql", lambda *a: pytest.fail("queried without save=True"))
    fx = load_fixture("unit_two_segments")
    agent.analyze(case, [{"type": "video", "frames": make_frames(tmp_path, FRAMES)}],
                  client=FakeClient(fx["segment_1"]), live=True)


# ---------- record / replay cache and budget ----------

def test_no_live_no_cache_raises_with_estimate(tmp_path, case):
    frames = make_frames(tmp_path, FRAMES)
    client = FakeClient()
    with pytest.raises(agent.NoCachedResponse, match="Rerun with --live"):
        agent.call_model(client, frames, case, cache=agent.ResponseCache(tmp_path / "c"), cache_name="x")
    assert client.messages.calls == []


def test_live_records_then_replays_free(tmp_path, case):
    fx = load_fixture("unit_two_segments")
    frames = make_frames(tmp_path, FRAMES)
    cache = agent.ResponseCache(tmp_path / "c")
    client = FakeClient(fx["segment_1"])
    _, _, cached = agent.call_model(client, frames, case, cache=cache, cache_name="eval_x", live=True)
    assert cached is False and len(client.messages.calls) == 1
    rec = cache.get("eval_x")
    assert rec["key"] == agent.cache_key(frames, case)
    assert "data" not in json.dumps(rec["frames"]), "no image bytes in fixtures"
    raw, _, cached = agent.call_model(FakeClient(), frames, case, cache=cache, cache_name="eval_x")
    assert cached is True and raw["hazards"]


def test_stale_fixture_detected(tmp_path, case):
    fx = load_fixture("unit_two_segments")
    frames = make_frames(tmp_path, FRAMES)
    cache = agent.ResponseCache(tmp_path / "c")
    agent.call_model(FakeClient(fx["segment_1"]), frames, case, cache=cache, cache_name="eval_x", live=True)
    frames[0]["sha256"] = "changed"
    with pytest.raises(agent.StaleFixture):
        agent.call_model(FakeClient(), frames, case, cache=cache, cache_name="eval_x")


def test_budget_aborts_before_call(tmp_path, case):
    frames = make_frames(tmp_path, FRAMES)
    client = FakeClient({"content": [], "usage": {}})
    with pytest.raises(agent.BudgetExceeded):
        agent.call_model(client, frames, case, live=True, budget=agent.Budget(0.001))
    assert client.messages.calls == []


def test_request_shape(tmp_path, case):
    fx = load_fixture("unit_two_segments")
    frames = make_frames(tmp_path, FRAMES)
    client = FakeClient(fx["segment_1"])
    agent.call_model(client, frames, case, live=True)
    req = client.messages.calls[0]
    assert req["model"] == "claude-sonnet-5-5" and req["max_tokens"] == 2000
    assert [t["name"] for t in req["tools"]] == ["report_hazards"]
    assert "tool_choice" not in req, "this model rejects forced tool_choice"
    assert req["system"][0]["cache_control"] == {"type": "ephemeral"}
    content = req["messages"][0]["content"]
    assert sum(1 for b in content if b["type"] == "image") == 3
    assert content[0]["text"].startswith("evidence_id: video_1_t010s")


def test_usage_cost():
    assert agent.usage_usd({"input_tokens": 1_000_000, "output_tokens": 0}) == 3.0
    assert agent.usage_usd({"input_tokens": 0, "output_tokens": 100_000}) == 1.5
