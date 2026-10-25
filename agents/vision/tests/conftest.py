import json
import sys
import threading
import time
from pathlib import Path

import pytest
from PIL import Image

HERE = Path(__file__).resolve().parent
VISION = HERE.parent
sys.path.insert(0, str(VISION))

FIXTURES = VISION / "fixtures"


def load_fixture(name):
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


def _cited(resp):
    ids = set()
    for b in resp.get("content", []):
        for h in (b.get("input") or {}).get("hazards", []):
            ids.update(h.get("evidence_ids", []))
    return ids


class FakeMessages:
    """Thread-safe. With parallel segments, picks the response whose cited frames appear in the request."""

    def __init__(self, responses, delay=0.0):
        self.responses = list(responses)
        self.calls = []
        self.delay = delay
        self._lock = threading.Lock()

    def create(self, **kwargs):
        sent = {b["text"].split()[1] for b in kwargs["messages"][0]["content"]
                if b.get("type") == "text" and b["text"].startswith("evidence_id:")}
        with self._lock:
            self.calls.append(kwargs)
            pick = next((r for r in self.responses if _cited(r) & sent), self.responses[0])
            self.responses.remove(pick)
        if self.delay:
            time.sleep(self.delay)
        return pick


class FakeClient:
    """Stands in for anthropic.Anthropic. Returns recorded responses in order; never touches the network."""

    def __init__(self, *responses, delay=0.0):
        self.messages = FakeMessages(responses, delay=delay)


def make_frames(tmp_path, ids_and_times, size=(320, 180)):
    """Tiny real JPEGs so build_content can read them. Returns frame dicts like frames.extract_video."""
    frames = []
    for i, (eid, t) in enumerate(ids_and_times):
        p = tmp_path / f"{eid}.jpg"
        Image.new("RGB", size, (40 * i % 255, 80, 120)).save(p, "JPEG")
        frames.append({"evidence_id": eid, "t_s": t, "path": str(p), "sha256": f"sha_{eid}",
                       "width": size[0], "height": size[1]})
    return frames


@pytest.fixture
def case():
    return {
        "case_id": "case_unit",
        "address": "123 Example St, Oakland, CA 94612",
        "jurisdiction": {"state": "CA", "city": "Oakland"},
        "household": {"children": True},
        "notes": "unit test",
    }
