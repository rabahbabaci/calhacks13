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


# ---------- SpacetimeDB save path ----------

def test_save_success_one_call_per_hazard(report, monkeypatch):
    calls = []
    monkeypatch.setattr(agent, "call_reducer", lambda name, args: calls.append((name, args)))
    agent.save_detections(report)
    assert [c[0] for c in calls] == ["save_detection"] * len(report["hazards"])
    for (_, args), h in zip(calls, report["hazards"]):
        case_id, hazard, severity, payload = args
        assert (case_id, hazard, severity) == (report["case_id"], h["hazard"], h["severity"])
        assert json.loads(payload)["evidence_ids"] == h["evidence_ids"]


def test_save_530_rejection(report, monkeypatch):
    def boom(name, args):
        raise StdbError(530, "unknown hazard: rust", "https://x/call/save_detection")
    monkeypatch.setattr(agent, "call_reducer", boom)
    with pytest.raises(RuntimeError, match="save_detection rejected"):
        agent.save_detections(report)


def test_save_reducer_not_published(report, monkeypatch):
    def missing(name, args):
        raise StdbError(404, "No such procedure", "https://x/call/save_detection")
    monkeypatch.setattr(agent, "call_reducer", missing)
    with pytest.raises(agent.ReducerMissing, match="not published"):
        agent.save_detections(report)


def test_save_server_down(report, monkeypatch):
    def down(name, args):
        raise urllib.error.URLError("Connection refused")
    monkeypatch.setattr(agent, "call_reducer", down)
    with pytest.raises(agent.DataLayerDown):
        agent.save_detections(report)


def test_analyze_does_not_save_by_default(tmp_path, case, monkeypatch):
    monkeypatch.setattr(agent, "call_reducer", lambda *a: pytest.fail("saved without save=True"))
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
