"""No network. Layout, packet rules, the action validator, clinics, history, and the API."""
import json
import threading
from pathlib import Path
from urllib.parse import quote
from urllib.request import Request, urlopen

import pytest

from tenant.clinics import lookup, parse_directory
from tenant.draft import validate_draft
from tenant.fixture import COMPLAINT_URL, vision_report
from tenant.history import history_for
from tenant.layout import layout_from_report
from tenant.mailer import MemoryMailer
from tenant.packet import build_tiles
from tenant.refs import RULES
from tenant.server import AppState, make_handler, serve
from tenant.view import hazard_payload, new_case

ROOT = Path(__file__).resolve().parents[2]
PACK = json.loads((ROOT / "data" / "packs" / "ca_oakland.json").read_text(encoding="utf-8"))
PACK_URLS = set()
for group in ("city_procedures", "general_rules", "legal_aid"):
    for entry in PACK.get(group) or []:
        if entry.get("source_url"):
            PACK_URLS.add(entry["source_url"])
for law in PACK.get("hazard_laws") or []:
    for cite in law.get("citations") or []:
        if cite.get("source_url"):
            PACK_URLS.add(cite["source_url"])


def case(confirmed=True):
    item = new_case("demo", "demo@localhost", "Demo", "123 Example St, Oakland, CA 94612")
    if confirmed:
        for h in item["vision"]["hazards"]:
            item["confirmed"][f"{h['hazard']}|{h['location']}"] = True
    return item


def test_sheet_sends_bullets_and_initials():
    item = case()
    detail = hazard_payload(item, "dampness_mold|bedroom wall beside the window")
    health = detail["blocks"]["health"]
    assert health["status"] == "done"
    assert any("cough" in text for text in health["risks"])
    assert len(health["precautions"]) == 2
    landlord = detail["blocks"]["landlord"]
    assert landlord["company"] == "Example Owner LLC"
    assert landlord["initials"] == "EO"
    assert landlord["logo_url"] == ""
    item["property"]["owner"]["logo_url"] = "http://example.test/logo.png"
    assert hazard_payload(item, "dampness_mold|bedroom wall beside the window")["blocks"]["landlord"]["logo_url"] == ""
    item["property"]["owner"]["logo_url"] = "https://example.com/logo.png"
    assert hazard_payload(item, "dampness_mold|bedroom wall beside the window")["blocks"]["landlord"]["logo_url"].startswith("https://")


def test_layout_spreads_icons_in_one_room():
    layout = layout_from_report(vision_report())
    assert [r["name"] for r in layout["rooms"]] == ["kitchen", "bedroom"]
    bedroom = layout["rooms"][1]
    assert len(bedroom["hazards"]) == 2
    xs = [icon["icon"][0] for icon in bedroom["hazards"]]
    assert xs[0] != xs[1]
    assert bedroom["door"]["wall"] == "south"
    assert bedroom["height"] == 2.6


def test_history_fix_only_when_the_record_states_it():
    item = case()
    damp = history_for("dampness_mold", item["property"])
    assert damp["open"] == 1 and damp["closed"] == 0
    assert damp["records"][0]["fix"] == ""
    leak = history_for("water_leak", item["property"])
    assert leak["closed"] == 1
    assert "replaced" in leak["records"][0]["fix"]
    assert history_for("lead_paint", item["property"])["records"] == []


def test_form_tile_uses_the_oakland_complaint_page():
    tiles = {t["id"]: t for t in build_tiles(case())}
    form = tiles["form-complaint"]
    url = form["preview"]["form"]["url"]
    assert url == COMPLAINT_URL
    assert url in PACK_URLS
    how = PACK["city_procedures"][0]["how"]
    steps = " ".join(form["preview"]["steps"])
    assert how.split(".")[0] in steps
    assert "BBCode-inspect@oaklandca.gov" in steps
    filled = {row["label"]: row["value"] for row in form["preview"]["filled"]}
    assert "123 Example St" in filled["Address"]
    assert "Your signature" in form["preview"]["still_needed"]


def test_unconfirmed_hazards_have_no_email_or_form():
    tiles = build_tiles(case(confirmed=False))
    assert [t["kind"] for t in tiles] == ["form"]  # the open public record only
    assert tiles[0]["id"].startswith("record|")


def test_urgent_tile_dials_and_does_not_email():
    item = case(confirmed=False)
    item["vision"]["hazards"].append({
        "hazard": "gas_leak",
        "label": "Possible gas leak",
        "location": "kitchen stove",
        "room": "kitchen",
        "severity": "urgent",
        "confidence": 0.9,
        "evidence_ids": ["video_1_t010s"],
        "first_seen_s": 10,
    })
    phone = next(t for t in build_tiles(item) if t["kind"] == "phone")
    assert phone["preview"]["phone"] == "911"
    assert "leave the building" in phone["preview"]["body"].lower()
    assert not any(t["kind"] == "email" and t["title"] == "Email the landlord" for t in build_tiles(item) if "gas" in t["id"])


def test_sent_email_flips_the_tile_and_asks_for_the_reply():
    item = case()
    tile_id = "email-landlord|dampness_mold|bedroom wall beside the window"
    item["messages"].append({
        "id": "fake-1", "direction": "out", "role": "landlord", "status": "sent",
        "tile_id": tile_id, "subject": "Repair request", "counterparty": "a@b.c",
    })
    tiles = {t["id"]: t for t in build_tiles(item)}
    assert tiles[tile_id]["status"] == "done"
    assert tiles["log-reply"]["changed"].startswith("The landlord email was sent")
    item["messages"].append({"id": "in-1", "direction": "in", "role": "landlord", "status": "received", "tile_id": "log-reply"})
    assert "log-reply" not in {t["id"] for t in build_tiles(item)}


def test_new_evidence_marks_tiles():
    item = case()
    item["evidence"].append({"evidence_id": "photo_1", "kind": "photo", "at": "now"})
    assert all(t["changed"] == "New evidence added." or t["changed"].startswith("Sent") for t in build_tiles(item))


def test_validator_drops_uncited_and_never_say():
    sources = {"P1": {"text": "Habitable rooms are to be kept free of dampness.", "kind": "statute"}}
    out = validate_draft({
        "subject": "Repair request",
        "sections": [
            {"text": "Habitable rooms are to be kept free of dampness.", "source_ids": ["P1"]},
            {"text": "You should fix this soon.", "source_ids": []},
            {"text": "You will win if you file.", "source_ids": ["P1"]},
        ],
    }, sources)
    assert len(out["sections"]) == 1
    assert out["subject"] == "Repair request"
    reasons = [d["reason"] for d in out["dropped"]]
    assert any("no source" in r for r in reasons)
    assert any("never_say" in r for r in reasons)


def test_validator_drops_a_banned_subject():
    out = validate_draft({"subject": "You will win", "sections": []}, {})
    assert out["subject"] == ""
    assert out["dropped"][0]["field"] == "subject"


def test_clinic_parser_and_hrsa_fallback():
    parsed = parse_directory({"results": [
        {"displayName": {"text": "West Oakland Health"}, "formattedAddress": "700 Adeline St", "location": {"latitude": 37.8, "longitude": -122.3}},
        {"name": ""},
    ]})
    assert parsed[0]["name"] == "West Oakland Health"
    assert "37.8" in parsed[0]["maps"] and "-122.3" in parsed[0]["maps"]
    assert len(parsed) == 1
    fallback = lookup("94612", [{"text": "See a clinician if a cough starts."}])
    assert fallback["clinics"] == []
    assert "94612" in fallback["search_url"]
    assert "invent" in fallback["note"]


def test_orchestrator_launches_property_once_per_address():
    import importlib.util
    spec = importlib.util.spec_from_file_location("orchestrator", ROOT / "integration" / "orchestrator.py")
    orch = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(orch)
    now = 1_000_000
    cases = [{"case_id": "c1", "address": "1 Main St"}, {"case_id": "c2", "address": "  "}]
    keys = orch.case_level_keys(cases, {}, ["property", "health"], now)
    assert keys == [("c1", "property", "")]
    runs = {("c1", "property", ""): {"status": "done", "updated_at": [0]}}
    assert orch.case_level_keys(cases, runs, ["property"], now) == []
    per_hazard, case_level = orch.split_agents("health,legal,property")
    assert list(per_hazard) == ["health", "legal"]
    assert list(case_level) == ["property"]


class Client:
    def __init__(self, base):
        self.base = base
        self.cookie = ""

    def call(self, method, path, data=None, content_type=None):
        headers = {}
        if self.cookie:
            headers["Cookie"] = self.cookie
        if content_type:
            headers["Content-Type"] = content_type
        req = Request(self.base + path, data=data, headers=headers, method=method)
        with urlopen(req) as res:
            raw = res.read()
            cookie = res.headers.get("Set-Cookie")
            if cookie:
                self.cookie = cookie.split(";", 1)[0]
            if res.headers.get("Content-Type", "").startswith("application/pdf"):
                return res.status, raw
            return res.status, json.loads(raw.decode())


@pytest.fixture
def api():
    state = AppState(mailer=MemoryMailer(), dev_login=True)
    httpd = serve(port=0, state=state)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    host, port = httpd.server_address
    client = Client(f"http://{host}:{port}")
    yield client, state
    httpd.shutdown()


def _onboard(client, address="123 Example St, Oakland, CA 94612", filename=None, content=b""):
    boundary = "----homeboundary"
    parts = [
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"address\"\r\n\r\n{address}\r\n".encode(),
    ]
    if filename:
        parts.append(
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"video\"; filename=\"{filename}\"\r\n"
            f"Content-Type: application/octet-stream\r\n\r\n".encode() + content + b"\r\n"
        )
    parts.append(f"--{boundary}--\r\n".encode())
    return client.call("POST", "/api/onboarding", b"".join(parts), f"multipart/form-data; boundary={boundary}")


def test_preview_does_not_send_and_send_calls_once(api):
    client, state = api
    client.call("POST", "/api/dev/login", b"{}")
    status, home = _onboard(client, filename="walk.mp4", content=b"fake-video")
    assert status == 200
    assert len(home["hazards"]) == 3
    assert len(home["layout"]["rooms"]) == 2
    hid = home["hazards"][0]["hazard_id"]
    _status, detail = client.call("GET", "/api/hazards/" + quote(hid, safe=""))
    assert detail["blocks"]["health"]["status"] == "waiting"
    client.call("POST", "/api/hazards/" + quote(hid, safe="") + "/confirm")
    _status, preview = client.call("POST", "/api/tiles/form-complaint/preview", b"{}")
    assert preview["preview"]["form"]["url"] == COMPLAINT_URL
    assert state.mailer.sent == []
    email_id = "email-landlord|" + hid
    _status, sent = client.call(
        "POST", "/api/tiles/" + quote(email_id, safe="") + "/send",
        json.dumps({"to": "owner@example.com"}).encode(), "application/json",
    )
    assert sent["status"] == "done"
    assert len(state.mailer.sent) == 1
    assert state.mailer.sent[0]["to"] == "owner@example.com"
    _status, pdf = client.call("GET", "/api/tiles/form-complaint/packet.pdf")
    assert pdf.startswith(b"%PDF")
    assert COMPLAINT_URL.encode() in pdf


def test_home_list_does_not_send(api):
    client, state = api
    client.call("POST", "/api/dev/login", b"{}")
    _onboard(client)
    client.call("GET", "/api/home")
    assert state.mailer.sent == []


def test_pack_loader_matches_the_form_tile():
    pack = RULES.load_pack({"state": "CA", "city": "Oakland"})
    urls = {p["source_url"] for p in pack["city_procedures"]}
    form = next(t for t in build_tiles(case()) if t["id"] == "form-complaint")
    assert form["preview"]["form"]["url"] in urls
