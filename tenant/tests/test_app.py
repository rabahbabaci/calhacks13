"""No network. Layout, packet rules, the action validator, clinics, history, and the API."""
import json
import sys
import threading
from io import BytesIO
from pathlib import Path
from urllib.parse import quote
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from tenant.clinics import (
    _hrsa_rows_from_gis, _tests_phrase, apply_service_page, attach_coords, clinics_from_hrsa, clinics_from_pages, fill_address, fill_services, lookup, npi_specialists, parse_directory,
)
from tenant.draft import validate_draft
from tenant.fixture import COMPLAINT_URL, vision_report
from tenant.history import history_for, summary_for
from tenant.layout import layout_from_report
from tenant.mailer import MemoryMailer
from tenant.packet import build_tiles, classify_option, classify_reply, step_groups, suggest_next, thread_messages, unique_options
from tenant.refs import RULES
from tenant.server import AppState, make_handler, serve
from tenant.live import apply_property, empty_property, live_enabled, start_after_onboard
from tenant.view import clinics_payload, hazard_payload, home_payload, ingest_mail, new_case

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
    assert detail["blocks"]["law"]["options"] == []
    assert detail["blocks"]["law"]["duties"]
    assert len(health["precautions"]) == 2
    assert health["see_a_doctor"]
    item["health"]["dampness_mold"]["health_risks"].append(
        {"text": "Damp indoor spaces are linked with coughing and wheezing.", "chunk_ids": [1]}
    )
    assert len(hazard_payload(item, "dampness_mold|bedroom wall beside the window")["blocks"]["health"]["risks"]) == 1
    landlord = detail["blocks"]["landlord"]
    assert landlord["company"] == "Example Owner LLC"
    assert landlord["initials"] == "EO"
    assert landlord["logo_url"] == ""
    item["property"]["owner"]["logo_url"] = "http://example.test/logo.png"
    assert hazard_payload(item, "dampness_mold|bedroom wall beside the window")["blocks"]["landlord"]["logo_url"] == ""
    item["property"]["owner"]["logo_url"] = "https://example.com/logo.png"
    assert hazard_payload(item, "dampness_mold|bedroom wall beside the window")["blocks"]["landlord"]["logo_url"].startswith("https://")


def test_fixture_hazard_has_no_frame_photo():
    detail = hazard_payload(case(), "dampness_mold|bedroom wall beside the window")
    assert detail["hazard"]["photo"] == ""


def test_hazard_photo_is_a_zoomed_crop_of_the_cited_frame(tmp_path):
    from PIL import Image
    from tenant import snapshot

    frame = tmp_path / "video_1_t010s.jpg"
    Image.new("RGB", (400, 300), (20, 80, 20)).save(frame)
    pixels = Image.open(frame)
    draw = pixels.load()
    for x in range(40, 80):
        for y in range(30, 70):
            draw[x, y] = (220, 30, 30)
    pixels.save(frame)
    item = case()
    hid = "dampness_mold|bedroom wall beside the window"
    item["vision"]["hazards"][0]["bbox"] = [0.1, 0.1, 0.2, 0.23]
    item["vision"]["hazards"][0]["bbox_frame"] = "video_1_t010s"
    item["vision"]["frames"] = [{"evidence_id": "video_1_t010s", "path": str(frame)}]
    assert item["vision"]["hazards"][0]["hazard"]
    detail = hazard_payload(item, hid)
    assert detail["hazard"]["photo"].endswith("/photo")
    body, err = snapshot.jpeg(item, hid)
    assert err == ""
    crop = Image.open(BytesIO(body))
    assert crop.size[0] < 400 or crop.size[1] < 300
    reds = [crop.getpixel((x, y))[0] for x in range(0, crop.size[0], 8) for y in range(0, crop.size[1], 8)]
    assert max(reds) > 180


def test_layout_spreads_icons_in_one_room():
    layout = layout_from_report(vision_report())
    assert [r["name"] for r in layout["rooms"]] == ["kitchen", "bedroom"]
    kitchen, bedroom = layout["rooms"]
    assert kitchen["kind"] == "kitchen" and bedroom["kind"] == "bedroom"
    assert kitchen["polygon"] != bedroom["polygon"]
    assert len(bedroom["hazards"]) == 2
    xs = [icon["icon"][0] for icon in bedroom["hazards"]]
    assert xs[0] != xs[1]
    z0 = min(p[1] for p in bedroom["polygon"])
    z1 = max(p[1] for p in bedroom["polygon"])
    for row in bedroom["hazards"]:
        z = row["icon"][2]
        assert min(abs(z - z0), abs(z - z1)) < 0.2
    assert bedroom["door"]["wall"] == "south"
    assert bedroom["height"] == 2.6


def test_layout_uses_a_room_shape_for_each_named_space():
    layout = layout_from_report({
        "rooms": [{"room": "hallway"}, {"room": "living room"}, {"room": "dining area"}, {"room": "bedroom"}],
        "hazards": [
            {"hazard": "water_leak", "location": "hallway ceiling", "room": "hallway", "first_seen_s": 1},
            {"hazard": "dampness_mold", "location": "living room wall", "room": "living room", "first_seen_s": 2},
            {"hazard": "dampness_mold", "location": "dining area corner", "room": "dining area", "first_seen_s": 3},
            {"hazard": "lead_paint", "location": "bedroom window", "room": "bedroom", "first_seen_s": 4},
        ],
    })
    kinds = {r["name"]: r["kind"] for r in layout["rooms"]}
    assert kinds == {"hallway": "hallway", "living room": "living", "dining area": "dining", "bedroom": "bedroom"}
    sizes = {r["kind"]: (
        max(p[0] for p in r["polygon"]) - min(p[0] for p in r["polygon"]),
        max(p[1] for p in r["polygon"]) - min(p[1] for p in r["polygon"]),
    ) for r in layout["rooms"]}
    assert sizes["hallway"][0] > sizes["hallway"][1] or sizes["hallway"][1] > sizes["living"][0] * 0.4
    assert sizes["living"] != sizes["hallway"]
    assert sizes["dining"] != sizes["living"]
    assert sizes["bedroom"] != sizes["dining"]


def test_two_bedrooms_stay_two_rooms():
    layout = layout_from_report({
        "rooms": [{"room": "bedroom"}, {"room": "second bedroom"}],
        "hazards": [
            {"hazard": "lead_paint", "location": "bedroom window", "room": "bedroom", "first_seen_s": 1},
            {"hazard": "water_leak", "location": "second bedroom ceiling", "room": "second bedroom", "first_seen_s": 2},
        ],
    })
    assert [r["name"] for r in layout["rooms"]] == ["bedroom", "second bedroom"]
    assert all(r["kind"] == "bedroom" for r in layout["rooms"])


def test_history_fix_only_when_the_record_states_it():
    item = case()
    damp = history_for("dampness_mold", item["property"])
    assert damp["open"] == 1 and damp["closed"] == 0
    assert damp["records"][0]["fix"] == ""
    leak = history_for("water_leak", item["property"])
    assert leak["closed"] == 1
    assert "replaced" in leak["records"][0]["fix"]
    assert history_for("lead_paint", item["property"])["records"] == []
    all_records = summary_for(item["property"])
    assert all_records["open"] == 1 and all_records["closed"] == 1
    assert {row["title"] for row in all_records["records"]} == {
        "Dampness in a habitable room",
        "Plumbing leak under a sink",
    }
    profile = home_payload(item)["landlord"]
    assert profile["company"] == "Example Owner LLC"
    assert profile["owner"]["mailing_address"].startswith("1 Mail St")
    assert profile["open"] == 1 and len(profile["records"]) == 2


def test_live_off_keeps_the_fixture_owner(monkeypatch):
    monkeypatch.delenv("HOME_LIVE", raising=False)
    assert live_enabled() is False
    item = new_case("demo", "demo@localhost", "Demo", "123 Example St, Oakland, CA 94612")
    assert item["fixture"] is True
    assert item["property"]["owner"]["name"] == "Example Owner LLC"
    assert home_payload(item)["fixture"] is True


def test_live_case_starts_empty_and_applies_a_property_report(monkeypatch):
    monkeypatch.setenv("HOME_LIVE", "1")
    item = new_case("live", "live@localhost", "Live", "7951 MacArthur Blvd, Oakland, CA 94605")
    assert item["fixture"] is False
    assert item["property"] == empty_property("7951 MacArthur Blvd, Oakland, CA 94605")
    assert item["health"] == {} and item["legal"] == {}
    apply_property(item, {
        "report": {
            "address": "7951 MacArthur Blvd, Oakland, CA 94605",
            "owner": {
                "name": "Real Owner LLC", "role": "company", "mailing_address": "10 Oak St",
                "email": "office@realowner.test", "phone": "(510) 555-0100",
                "website": "https://realowner.test",
            },
            "records": [{
                "id": "R1", "kind": "violation", "title": "Open dampness",
                "date": "2024-01-01", "status": "open", "detail": "", "ref": "1",
            }],
            "disclaimer": "Compiled from public records.",
            "summary": "One open dampness case.",
        }
    })
    profile = home_payload(item)["landlord"]
    assert profile["company"] == "Real Owner LLC"
    assert profile["owner"]["role"] == "company"
    assert profile["owner"]["email"] == "office@realowner.test"
    assert profile["owner"]["phone"] == "(510) 555-0100"
    assert profile["owner"]["website"] == "https://realowner.test"
    assert profile["summary"] == "One open dampness case."
    assert profile["open"] == 1
    assert profile["records"][0]["title"] == "Open dampness"


def test_landlord_prefers_a_reachable_management_company(monkeypatch):
    monkeypatch.setenv("HOME_LIVE", "1")
    item = new_case("live", "live@localhost", "Live", "1666 Da Vinci Ct, Davis, CA 95618")
    apply_property(item, {
        "report": {
            "address": "1666 Da Vinci Ct, Davis, CA 95618",
            "owner": {"name": "Holding LLC", "role": "owner"},
            "manager": {
                "name": "FPI Management, Inc.", "role": "management company",
                "email": "leasing@liveatdavinci.com", "phone": "(530) 757-2800",
                "website": "https://www.liveatdavinci.com",
            },
            "summary": "FPI Management runs the community.",
        }
    })
    profile = home_payload(item)["landlord"]
    assert profile["company"] == "FPI Management, Inc."
    assert profile["manager"]["website"] == "https://www.liveatdavinci.com"
    assert profile["contact"]["email"] == "leasing@liveatdavinci.com"
    assert [row["title"] for row in profile["records"]] == ["FPI Management, Inc.", "Holding LLC"]


def test_landlord_details_are_pulled_from_report_prose(monkeypatch):
    monkeypatch.setenv("HOME_LIVE", "1")
    item = new_case("live", "live@localhost", "Live", "1666 Da Vinci Ct, Davis, CA 95618")
    apply_property(item, {
        "report": {
            "address": "1666 Da Vinci Ct, Davis, CA 95618",
            "owner": {"name": "", "role": "owner"},
            "manager": None,
            "records": [],
            "summary": (
                "The community is run under FPI Management. "
                "Mailing address: 800 Iron Point Road, Folsom, CA 95630. "
                "Email leasing@liveatdavinci.com or call (530) 757-2800. "
                "https://www.liveatdavinci.com"
            ),
        }
    })
    profile = home_payload(item)["landlord"]
    assert profile["company"] == "FPI Management"
    assert profile["owner"]["mailing_address"] == "800 Iron Point Road, Folsom, CA 95630"
    assert profile["owner"]["email"] == "leasing@liveatdavinci.com"
    assert profile["owner"]["phone"] == "(530) 757-2800"
    assert profile["owner"]["website"] == "https://www.liveatdavinci.com"
    assert profile["contact"]["email"] == "leasing@liveatdavinci.com"


def test_landlord_tiles_use_a_company_named_only_in_the_summary(monkeypatch):
    monkeypatch.setenv("HOME_LIVE", "1")
    item = new_case("live", "live@localhost", "Live", "1666 Da Vinci Ct, Davis, CA 95618")
    apply_property(item, {
        "report": {
            "address": "1666 Da Vinci Ct, Davis, CA 95618",
            "owner": {"name": "", "role": "owner"},
            "manager": None,
            "records": [],
            "summary": "DaVinci Apartments at 1666 Da Vinci Ct is a 51-unit complex. Its website is run under FPI Management.",
        }
    })
    profile = home_payload(item)["landlord"]
    assert profile["company"] == "FPI Management"
    assert profile["owner"]["name"] == "FPI Management"
    assert profile["records"][0]["title"] == "FPI Management"


def test_landlord_tiles_use_the_named_company_when_records_were_not_saved(monkeypatch):
    monkeypatch.setenv("HOME_LIVE", "1")
    item = new_case("live", "live@localhost", "Live", "1666 Da Vinci Ct, Davis, CA 95618")
    apply_property(item, {
        "report": {
            "address": "1666 Da Vinci Ct, Davis, CA 95618",
            "owner": {"name": "FPI Management, Inc.", "role": "management company"},
            "manager": None,
            "records": [],
            "summary": "FPI Management is the named company.",
        }
    })
    profile = home_payload(item)["landlord"]
    assert profile["company"] == "FPI Management, Inc."
    assert profile["records"][0]["title"] == "FPI Management, Inc."
    assert profile["open"] == 1


def test_live_api_starts_agents_without_calling_them(api, monkeypatch):
    monkeypatch.setenv("HOME_LIVE", "1")
    started = []
    monkeypatch.setattr("tenant.server.start_after_onboard", lambda case: started.append(("onboard", case["address"])))
    monkeypatch.setattr("tenant.server.start_after_confirm", lambda case, hid: started.append(("confirm", hid)))
    client, _state = api
    client.call("POST", "/api/dev/login", b"{}")
    status, home = _onboard(client)
    assert status == 200
    assert home["fixture"] is False
    assert home["landlord"]["company"] == "Landlord"
    assert started[0][0] == "onboard"
    hid = "dampness_mold|bedroom wall beside the window"
    status, detail = client.call("POST", "/api/hazards/" + quote(hid, safe="") + "/confirm")
    assert status == 200
    assert detail["blocks"]["health"]["status"] in {"waiting", "running"}
    assert detail["blocks"]["health"]["risks"] == []
    assert started[-1] == ("confirm", hid)


def test_onboard_with_video_submits_vision(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME_LIVE", "1")
    started = []
    monkeypatch.setattr("tenant.live._pool.submit", lambda *args, **kwargs: started.append(args[1]))
    monkeypatch.setattr("tenant.live.apply_key_fallbacks", lambda: None)
    item = new_case("demo", "demo@localhost", "Demo", "1666 Da Vinci Ct, Davis, CA 95618")
    video = tmp_path / "walk.mp4"
    video.write_bytes(b"x")
    item["media"] = [{"path": str(video)}]
    start_after_onboard(item)
    assert started == ["property", "plan", "hazard"]


def test_onboard_without_video_starts_property_and_plan_only(monkeypatch):
    monkeypatch.setenv("HOME_LIVE", "1")
    started = []
    monkeypatch.setattr("tenant.live._pool.submit", lambda *args, **kwargs: started.append(args[1]))
    monkeypatch.setattr("tenant.live.apply_key_fallbacks", lambda: None)
    item = new_case("demo", "demo@localhost", "Demo", "1666 Da Vinci Ct, Davis, CA 95618")
    start_after_onboard(item)
    assert started == ["property", "plan"]
    hid = "dampness_mold|bedroom wall beside the window"
    assert item["confirmed"] == {}
    assert hazard_payload(item, hid)["hazard"]["confirmed"] is False
    assert hazard_payload(item, hid)["blocks"]["health"]["status"] == "waiting"


def test_live_home_is_analyzing_only_while_the_walkthrough_runs(monkeypatch):
    monkeypatch.setenv("HOME_LIVE", "1")
    item = new_case("live", "live@localhost", "Live", "1666 Da Vinci Ct, Davis, CA 95618")
    item["runs"]["hazard"] = "running"
    assert home_payload(item)["analyzing"] is True
    item["runs"] = {"hazard": "done", "property": "running", "plan": "waiting", "health": "waiting", "legal": "waiting"}
    assert home_payload(item)["analyzing"] is False


def _only_user(state):
    (user,) = state.users.values()
    return user


def test_sign_out_clears_a_demo_case(api):
    client, state = api
    client.call("POST", "/api/dev/login", b"{}")
    _onboard(client)
    first = _only_user(state)["case_id"]
    assert first and first in state.cases
    client.call("POST", "/api/auth/logout", b"{}")
    assert first not in state.cases
    assert state.users == {}
    client.call("POST", "/api/dev/login", b"{}")
    assert _only_user(state)["case_id"] == ""


def test_two_demo_tenants_never_share_a_case(api):
    client, state = api
    other = Client(client.base)
    client.call("POST", "/api/dev/login", b"{}")
    _onboard(client)
    other.call("POST", "/api/dev/login", b"{}")
    _status, home = other.call("GET", "/api/home")
    assert home["needs_onboarding"] is True
    assert len(state.users) == 2
    _status, mine = client.call("GET", "/api/home")
    assert mine["needs_onboarding"] is False


def test_ensure_site_finds_anthropic():
    from tenant import live

    before = list(sys.path)
    try:
        sys.path[:] = [p for p in sys.path if "site-packages" not in p]
        live._ensure_site()
        import anthropic
        assert anthropic.__version__
    finally:
        sys.path[:] = before


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
    assert any(row["label"] == "City record" and "Dampness" in row["value"] for row in form["preview"]["filled"])
    assert "record|" not in {t["id"] for t in build_tiles(case())}


def test_live_legal_validate_stays_the_legal_one():
    from tenant import live

    live._modules.clear()
    live._load("property")
    legal = live._load("legal")
    fields, _cited, dropped = legal.validate(
        "dampness_mold",
        {"landlord_duty": [{"text": "Dampness can make a unit substandard.", "source_ids": ["P1"]}]},
        {"P1": {"text": "Dampness of habitable rooms.", "kind": "statute"}},
    )
    assert dropped == []
    assert len(fields["landlord_duty"]) == 1


def test_next_steps_group_unique_options_across_hazards():
    item = case()
    item["legal"]["dampness_mold"]["tenant_options"].append(
        {"text": "A tenant can report the condition through the city's property complaint form.", "source_ids": ["C1"]}
    )
    groups = step_groups(item)
    assert [g["tag"] for g in groups] == ["dampness_mold", "lead_paint"]
    mold = groups[0]
    assert {row["hazard_id"] for row in mold["hazards"]} == {
        "dampness_mold|bedroom wall beside the window",
        "dampness_mold|kitchen counter at the faucet",
    }
    texts = [row["text"] for row in mold["options"]]
    assert texts == ["A tenant can report the condition through the city's property complaint form."]
    lead = groups[1]
    assert [row["text"] for row in lead["options"]] == ["Report peeling paint through the same city complaint form."]
    payload = home_payload(item)
    assert payload["groups"][0]["options"][0]["text"] == texts[0]
    assert [row["text"] for row in unique_options(item["legal"]["dampness_mold"]) if row.get("action")] == texts
    tiles = {t["id"]: t for t in build_tiles(item)}
    assert tiles["form-complaint"]["why"].startswith("A tenant can report")
    assert "evidence-photos" not in tiles


def test_actionable_options_become_the_same_kind_of_tiles():
    assert classify_option("Take dated photos of the wall beside the window.") == "photos"
    assert classify_option("Talk to free legal aid before using any rent remedy.") == "aid"
    assert classify_option("Give the landlord written notice and keep proof.") == "notice"
    assert classify_option("California law protects against retaliation for 180 days.") is None
    item = case()
    item["legal"]["dampness_mold"]["tenant_options"].extend([
        {"text": "Take dated photos of the wall beside the window.", "source_ids": ["C1"]},
        {"text": "Talk to free legal aid before using any rent remedy.", "source_ids": ["C1"]},
        {"text": "California law protects against retaliation for 180 days.", "source_ids": ["C1"]},
    ])
    tiles = {t["id"]: t for t in build_tiles(item)}
    assert "evidence-photos" not in tiles
    assert tiles["phone-legal-aid"]["kind"] == "phone"
    assert tiles["phone-legal-aid"]["preview"]["phone"] == "510-437-1554"
    assert tiles["phone-legal-aid"]["title"] == "Call Centro Legal de la Raza at 510-437-1554"
    assert "510-437-1554" in tiles["phone-legal-aid"]["why"]
    assert tiles["form-complaint"]["why"]


def test_legal_aid_number_matches_the_city():
    oakland = next(tile for tile in build_tiles(case()) if tile["id"] == "phone-legal-aid")
    assert "510-437-1554" in oakland["title"]
    davis = case()
    davis["address"] = "1666 Da Vinci Ct, Davis, CA 95618"
    davis["jurisdiction"] = {"state": "CA", "city": "Davis"}
    davis["zip"] = "95618"
    aid = next(tile for tile in build_tiles(davis) if tile["id"] == "phone-legal-aid")
    assert "530-757-5623" in aid["title"]
    assert "City of Davis Fair Housing Services" in aid["title"]
    assert "510-437-1554" not in aid["title"]


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


def test_landlord_email_is_a_written_notice():
    email = next(tile for tile in build_tiles(case()) if tile["kind"] == "email")
    assert email["title"] == "Send written notice"
    assert email["preview"]["subject"].startswith("Written notice of needed repair")
    body = email["preview"]["body"]
    assert "written notice" in body.lower()
    assert "1. Inspect" in body
    assert "2. Complete the repair" in body
    assert "3. Reply in writing" in body
    assert "123 Example St" in body
    assert "Respectfully" in body
    assert "Demo" in body
    assert "Nothing is sent until you press Send." in email["preview"]["note"]
    assert "Tracking" not in email["preview"]["note"]
    assert "Cal. Health & Safety Code 17920.3(a)(11)" in body
    assert "Habitable rooms are to be kept free of dampness." in body
    assert "https://leginfo.legislature.ca.gov" in body
    assert "Oakland property complaint" not in body
    empty = case()
    empty["legal"] = {}
    blank = next(tile for tile in build_tiles(empty) if tile["kind"] == "email")["preview"]["body"]
    assert "Legal basis" not in blank
    assert "17920" not in blank


def test_sent_email_flips_the_tile_and_does_not_ask_to_log_a_reply():
    item = case()
    tile_id = "email-landlord|dampness_mold|bedroom wall beside the window"
    item["messages"].append({
        "id": "fake-1", "direction": "out", "role": "landlord", "status": "sent",
        "tile_id": tile_id, "subject": "Written notice of needed repair", "counterparty": "a@b.c",
        "at": "2026-10-25T10:00:00+00:00",
    })
    tiles = {t["id"]: t for t in build_tiles(item)}
    assert tiles[tile_id]["status"] == "done"
    assert tiles[tile_id]["changed"].startswith("Sent. Waiting")
    assert "log-reply" not in tiles
    assert "email-followup" not in tiles
    ingest_mail(item, [{
        "gmail_id": "gm-1", "direction": "in", "counterparty": "a@b.c",
        "subject": "Re: Written notice of needed repair", "body": "We will look at it.",
        "at": "2026-10-25T12:00:00+00:00",
    }])
    assert item["messages"][-1]["direction"] == "in"
    assert item["messages"][-1]["body"] == "We will look at it."
    ingest_mail(item, [{"gmail_id": "gm-1", "direction": "in", "body": "dup"}])
    assert sum(1 for m in item["messages"] if m.get("gmail_id") == "gm-1") == 1
    tiles = {t["id"]: t for t in build_tiles(item)}
    assert "email-followup" in tiles
    assert tiles["email-followup"]["status"] == "ready"
    assert tiles["email-followup"]["title"] == "Ask for a written schedule"
    assert tiles["email-followup"]["preview"]["subject"].startswith("Re:")
    assert "acknowledging" in tiles["email-followup"]["preview"]["body"]
    assert "Cal. Health & Safety Code 17920.3(a)(11)" in tiles["email-followup"]["preview"]["body"]
    nxt = home_payload(item)["next"]
    assert nxt["kind"] == "ack"
    assert nxt["tile_id"] == "email-followup"


def test_correspondence_starts_after_first_send():
    item = case()
    assert thread_messages(item["messages"]) == []
    assert ingest_mail(item, [{
        "gmail_id": "old-1", "direction": "in", "counterparty": "a@b.c",
        "subject": "Old lease", "body": "Welcome", "at": "Wed, 01 Jan 2020 12:00:00 +0000",
    }]) == 0
    assert item["messages"] == []
    assert home_payload(item)["tracking"]["messages"] == []
    item["messages"].append({
        "id": "out-1", "direction": "out", "role": "landlord", "status": "sent",
        "tile_id": "email-landlord|dampness_mold|bedroom wall beside the window",
        "subject": "Written notice of needed repair", "counterparty": "a@b.c",
        "at": "2026-10-25T10:00:00+00:00", "body": "Inspect and repair.",
    })
    ingest_mail(item, [{
        "gmail_id": "old-1", "direction": "in", "counterparty": "a@b.c",
        "subject": "Old lease", "body": "Welcome", "at": "Wed, 01 Jan 2020 12:00:00 +0000",
    }])
    ingest_mail(item, [{
        "gmail_id": "new-1", "direction": "in", "counterparty": "a@b.c",
        "subject": "Re: Written notice of needed repair", "body": "We will look.",
        "at": "Sun, 25 Oct 2026 12:00:00 +0000",
    }])
    thread = thread_messages(item["messages"])
    assert [row["direction"] for row in thread] == ["out", "in"]
    assert [row.get("gmail_id") or row.get("id") for row in thread] == ["out-1", "new-1"]
    assert home_payload(item)["tracking"]["messages"] == thread


def test_next_step_follows_the_landlord_reply():
    assert classify_reply({"body": "We will look at it."}) == "ack"
    assert classify_reply({"body": "Maintenance will come Tuesday at 10."}) == "schedule"
    assert classify_reply({"body": "Please send photos of the wall."}) == "info"
    assert classify_reply({"body": "We fixed the leak yesterday."}) == "completed"
    assert classify_reply({"body": "We have a backlog and no timeline."}) == "delay"
    assert classify_reply({"body": "This is not our responsibility and we will not repair it."}) == "refusal"
    assert classify_reply({"body": "If you keep writing we will evict."}) == "retaliation"
    assert classify_reply({"body": "Okay."}) == "unclear"

    item = case()
    item["messages"].append({
        "id": "out-1", "direction": "out", "role": "landlord", "status": "sent",
        "tile_id": "email-landlord|dampness_mold|bedroom wall beside the window",
        "subject": "Written notice of needed repair", "counterparty": "a@b.c",
        "at": "2026-10-25T10:00:00+00:00",
    })
    item["messages"].append({
        "id": "in-1", "direction": "in", "role": "landlord", "status": "received",
        "subject": "Re: Written notice", "counterparty": "a@b.c",
        "body": "This is not our responsibility and we will not repair it.",
        "at": "2026-10-25T12:00:00+00:00",
    })
    tiles = build_tiles(item)
    nxt = suggest_next(tiles, thread_messages(item["messages"]), item.get("filed"))
    assert nxt["kind"] == "refusal"
    assert nxt["tile_id"] == "phone-legal-aid"
    aid = next(tile for tile in tiles if tile["id"] == "phone-legal-aid")
    assert "510-437-1554" in aid["title"]
    follow = next(tile for tile in tiles if tile["id"] == "email-followup")
    assert follow["title"] == "Put the refusal on the record"
    item["filed"].append("phone-legal-aid")
    tiles = build_tiles(item)
    nxt = suggest_next(tiles, thread_messages(item["messages"]), item["filed"])
    assert nxt["kind"] == "file"
    assert nxt["tile_id"] == "form-complaint"
    form = next(tile for tile in tiles if tile["id"] == "form-complaint")
    assert "called legal aid" in form["changed"].lower()


def test_new_evidence_marks_tiles():
    item = case()
    item["evidence"].append({"evidence_id": "photo_1", "kind": "photo", "at": "now"})
    assert all(
        t["changed"] == "New evidence added." or t["changed"].startswith("Sent") or t["changed"].startswith("Saved")
        for t in build_tiles(item)
    )


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


def test_cited_clinic_rows_follow_the_page():
    quiet = "https://findahealthcenter.hrsa.gov/center"
    quiet_text = "Quiet Clinic\n10 Broadway, Oakland, CA 94612\nPrimary care visits."
    funded = "https://www.oaklandca.gov/testing"
    funded_text = (
        "Oakland Testing Site\n"
        "700 Adeline St, Oakland, CA 94607\n"
        "Free, city-funded lead testing. Medi-Cal accepted. About 1.4 miles from downtown."
    )
    city = "https://www.acphd.org/city"
    city_text = "Hilltop Health\n100 Main St, Oakland, CA 94612\nThis site is city-funded."
    parsed = parse_directory({"clinics": [
        {
            "name": "Quiet Clinic",
            "address": "10 Broadway, Oakland, CA 94612",
            "tests": "lead",
            "insurance": "Aetna",
            "funding": "free",
            "distance_mi": 3,
            "source_url": quiet,
            "page_text": quiet_text,
        },
        {
            "name": "Oakland Testing Site",
            "address": "700 Adeline St, Oakland, CA 94607",
            "tests": "lead testing",
            "insurance": "Medi-Cal",
            "source_url": funded,
            "page_text": funded_text,
        },
        {
            "name": "Ghost Clinic",
            "address": "1 Missing Rd",
            "page_text": "Ghost Clinic\n1 Missing Rd",
        },
        {
            "name": "Not On Page",
            "address": "10 Broadway, Oakland, CA 94612",
            "source_url": quiet,
            "page_text": quiet_text,
        },
        {
            "name": "Hilltop Health",
            "address": "100 Main St, Oakland, CA 94612",
            "tests": "mold",
            "insurance": "Medicare",
            "funding": "free",
            "source_url": city,
            "page_text": city_text,
        },
    ]})
    by_name = {row["name"]: row for row in parsed}
    assert "Ghost Clinic" not in by_name
    assert "Not On Page" not in by_name
    quiet_row = by_name["Quiet Clinic"]
    assert quiet_row["tests"] == ""
    assert quiet_row["insurance"] == ""
    assert quiet_row["funding"] == ""
    assert quiet_row["distance_mi"] is None
    assert quiet_row["maps"] == (
        "https://www.google.com/maps/search/?api=1&query="
        + quote("10 Broadway, Oakland, CA 94612")
    )
    funded_row = by_name["Oakland Testing Site"]
    assert funded_row["tests"] == "lead testing"
    assert funded_row["insurance"] == "Medi-Cal"
    assert funded_row["funding"] == "free, city-funded"
    assert funded_row["distance_mi"] == 1.4
    assert by_name["Hilltop Health"]["funding"] == "city-funded"
    assert by_name["Hilltop Health"]["tests"] == ""
    assert by_name["Hilltop Health"]["insurance"] == ""
    no_cost = parse_directory({"clinics": [{
        "name": "Cost Clinic",
        "address": "50 Oak St, Oakland, CA 94607",
        "source_url": "https://www.acphd.org/cost",
        "page_text": "Cost Clinic\n50 Oak St, Oakland, CA 94607\nServices are no-cost.",
    }]})
    assert no_cost[0]["funding"] == "free"


def test_page_text_keeps_one_cited_clinic():
    text = "Oakland Testing Site\n700 Adeline St, Oakland, CA 94607\nFree lead testing. Medi-Cal accepted."
    directory = clinics_from_pages([{"url": "https://www.acphd.org/std", "text": text}])
    assert len(directory["clinics"]) == 1
    row = directory["clinics"][0]
    assert row["name"] == "Oakland Testing Site"
    assert row["address"] == "700 Adeline St, Oakland, CA 94607"
    assert row["funding"] == "free"
    assert "lead" in row["tests"].lower()
    assert "Medi-Cal" in row["insurance"]
    assert row["source_url"] == "https://www.acphd.org/std"
    assert row["distance_mi"] is None
    empty = clinics_from_pages([{"url": "https://www.acphd.org/about", "text": "We offer programs."}])
    assert empty["clinics"] == []
    assert "invent" in empty["note"]


def test_hrsa_gis_features_become_directory_rows():
    rows = _hrsa_rows_from_gis([{
        "attributes": {
            "SITE_NM": "Davis Community Clinic", "SITE_ADDRESS": "2051 John Jones Rd",
            "SITE_CITY": "Davis", "SITE_STATE_ABBR": "CA", "SITE_ZIP_CD": "95616-9701",
            "SITE_PHONE_NUM": "530-758-2060", "SITE_URL": "", "HCC_OPER_DESC": "Communicare+",
        },
        "geometry": {"x": -121.77, "y": 38.56},
    }], origin=(-121.72, 38.55))
    clinics = clinics_from_hrsa(rows, "95618")
    assert clinics[0]["name"] == "Davis Community Clinic"
    assert "2051 John Jones Rd" in clinics[0]["address"]
    assert clinics[0]["lat"] == 38.56 and clinics[0]["lon"] == -121.77
    assert clinics[0]["distance_mi"] is not None


def test_hrsa_zip_results_do_not_need_a_center_name():
    rows = clinics_from_hrsa([
        {
            "CtrAddress": "386 14th St", "CtrCity": "Oakland", "CtrStateAbbr": "CA",
            "CtrZipCd": "94612-3211", "Distance": 0.17, "Latitude": 37.8, "Longitude": -122.27,
        },
        {
            "CtrNm": "Named Center", "CtrAddress": "1 Main St", "CtrCity": "Oakland",
            "CtrStateAbbr": "CA", "CtrZipCd": "94607", "Distance": 1.2,
            "Latitude": 37.81, "Longitude": -122.26,
        },
    ], "94612")
    assert [row["name"] for row in rows] == ["386 14th St, Oakland, CA 94612", "Named Center"]
    assert rows[0]["address"] == "386 14th St, Oakland, CA 94612"
    assert rows[0]["distance_mi"] == 0.17
    assert rows[0]["tests"] == "" and rows[0]["insurance"] == "" and rows[0]["funding"] == ""
    assert "94612" in rows[0]["source_url"]
    assert "37.8" in rows[0]["maps"] and "-122.27" in rows[0]["maps"]
    assert rows[0]["lat"] == 37.8 and rows[0]["lon"] == -122.27


def test_service_lookup_copies_only_stated_tests_and_insurance():
    page = "https://www.lifelongmedical.org/"
    text = "LifeLong offers HIV testing and STI screening. Medi-Cal and Medicare accepted. Visits are free."
    clinics = [
        {"name": "LifeLong Downtown", "website": page, "tests": "", "insurance": "", "funding": "", "source_url": "https://findahealthcenter.hrsa.gov/?zip=94612"},
        {"name": "Other Clinic", "website": "https://www.example.org/", "tests": "", "insurance": "", "funding": "", "source_url": "https://findahealthcenter.hrsa.gov/?zip=94612"},
    ]
    assert apply_service_page(clinics, page, text) == 1
    assert "HIV" in clinics[0]["tests"]
    assert "Medi-Cal" in clinics[0]["insurance"] and "Medicare" in clinics[0]["insurance"]
    assert clinics[0]["funding"] == "free"
    assert clinics[0]["source_url"] == page
    assert clinics[1]["tests"] == "" and clinics[1]["insurance"] == ""
    quiet = [{"name": "LifeLong Downtown", "website": page, "tests": "", "insurance": "", "funding": "", "source_url": "https://findahealthcenter.hrsa.gov/?zip=94612", "parent": ""}]
    fill_services(quiet, lambda url: "Hours and parking only.")
    assert quiet[0]["tests"] == "" and quiet[0]["insurance"] == ""
    junk = _tests_phrase("Best STD testing. Life](//alamedahealthsystem.org testing for visitors.")
    assert "Best" not in junk and "Life" not in junk and "http" not in junk
    assert "STD" in junk


def test_clinics_keep_only_tests_the_defects_need():
    directory = {"clinics": [
        {"name": "Lead Site", "address": "1 Main St", "tests": "lead", "insurance": "Medi-Cal", "source_url": "https://example.com/a"},
        {"name": "HIV Site", "address": "2 Main St", "tests": "HIV, STD", "source_url": "https://example.com/b"},
        {"name": "Quiet", "address": "3 Main St", "tests": "", "source_url": "https://example.com/c"},
        {"name": "Mold Site", "address": "4 Main St", "tests": "allergy", "source_url": "https://example.com/d"},
    ]}
    out = lookup("94612", [], directory, defects=["dampness_mold", "lead_paint"])
    assert [row["name"] for row in out["clinics"]] == ["Lead Site", "Mold Site"]
    assert "lead" in out["note"] and "mold" in out["note"] and "allergy" in out["note"]
    assert out["clinics"][0]["tests"] == "lead" and out["clinics"][1]["tests"] == "allergy"
    assert lookup("94612", [], directory, defects=["gas_leak"])["clinics"] == []
    hrsa = "https://findahealthcenter.hrsa.gov/?zip=94612"
    usual = lookup("94612", [], {"clinics": [
        {"name": "LifeLong Medical Care - Downtown Oakland Clinic", "address": "1 Main St", "tests": "", "source_url": hrsa},
        {"name": "La Clinica Julian R. Davis Pediatrics", "address": "2 Main St", "tests": "", "source_url": hrsa},
        {"name": "Children's Hospital Oakland Primary Care", "address": "3 Main St", "tests": "", "source_url": hrsa},
        {"name": "LA CLINICA DE LA RAZA - DENTAL", "address": "4 Main St", "tests": "", "parent": "La Clinica de La Raza", "source_url": hrsa},
        {"name": "LA CLINICA DE LA RAZA - WIC PROGRAM", "address": "5 Main St", "tests": "", "source_url": hrsa},
        {"name": "FAMILY OPTICAL", "address": "6 Main St", "tests": "", "source_url": hrsa},
        {"name": "ACHCH StreetHealth Team Mobile Unit", "address": "7 Main St", "tests": "", "source_url": hrsa},
        {"name": "Children's Hospital Oakland Behavioral Health", "address": "8 Main St", "tests": "", "source_url": hrsa},
    ]}, defects=["lead_paint", "dampness_mold"])
    assert [row["name"] for row in usual["clinics"]] == [
        "LifeLong Medical Care - Downtown Oakland Clinic",
        "La Clinica Julian R. Davis Pediatrics",
        "Children's Hospital Oakland Primary Care",
    ]
    assert all(row["assumed"] == "blood lead" and row["tests"] == "" for row in usual["clinics"])
    assert all(row["assumed_insurance"] == "Medi-Cal, Medicare" for row in usual["clinics"])
    assert "enrollment file" in usual["note"] or "health-center" in usual["note"]
    assert "primary care orders a blood lead test" in usual["note"]
    assert "website" not in usual["note"].lower()
    assert lookup("94612", [], {"clinics": [
        {"name": "LifeLong Medical Care - Downtown Oakland Clinic", "address": "1 Main St", "tests": "", "source_url": "https://example.com/pc"},
    ]}, defects=["dampness_mold"])["clinics"] == []
    allergy_named = lookup("94612", [], {"clinics": [
        {"name": "Oakland Allergy Clinic", "address": "9 Main St", "tests": "", "source_url": "https://example.com/allergy"},
    ]}, defects=["dampness_mold"])
    assert allergy_named["clinics"][0]["assumed"] == "allergy"
    stated_mold = lookup("94612", [], {"clinics": [
        {"name": "Quiet Place", "address": "10 Main St", "tests": "mold", "source_url": "https://example.com/mold"},
    ]}, defects=["dampness_mold"])
    assert stated_mold["clinics"][0]["tests"] == "mold" and stated_mold["clinics"][0]["assumed"] == ""
    co = lookup("94612", [], {"clinics": [
        {"name": "Highland Hospital", "address": "11 Main St", "tests": "", "source_url": "https://example.com/hosp"},
        {"name": "LifeLong Medical Care - Downtown Oakland Clinic", "address": "1 Main St", "tests": "", "source_url": "https://example.com/pc2"},
    ]}, defects=["carbon_monoxide"])
    assert [row["name"] for row in co["clinics"]] == ["Highland Hospital"]
    assert co["clinics"][0]["assumed"] == "carboxyhemoglobin"


def _npi(number, street, city, state, zip_code, taxonomy, org="", enum_type="NPI-1", purpose="LOCATION"):
    return {
        "number": number,
        "enumeration_type": enum_type,
        "basic": {"organization_name": org, "first_name": "Ada", "last_name": "Lovelace"},
        "addresses": [{
            "address_purpose": purpose,
            "address_1": street,
            "city": city,
            "state": state,
            "postal_code": zip_code,
            "telephone_number": "213-555-0100",
        }],
        "taxonomies": [{"desc": taxonomy}],
    }


def test_attach_coords_keeps_a_known_point():
    row = {"name": "Davis Clinic", "address": "1 Main St", "lat": 38.55, "lon": -121.74, "maps": ""}
    attach_coords([row])
    assert row["lat"] == 38.55 and row["lon"] == -121.74


def test_npi_offices_follow_the_entered_california_zip():
    pages = {
        "Allergy & Immunology": [
            _npi("1", "100 Market St", "San Francisco", "CA", "94110", "Allergy & Immunology", org="Market Allergy", enum_type="NPI-2"),
            _npi("2", "100 Market St", "San Francisco", "CA", "94103", "Allergy & Immunology", purpose="MAILING"),
            _npi("3", "419 30th St", "Oakland", "CA", "94609", "Allergy & Immunology"),
        ],
        "Otolaryngology": [
            _npi("0000000004", "200 Broadway Ste 10", "Los Angeles", "CA", "90012", "Otolaryngology"),
            _npi("0000000005", "200 Broadway Suite 400", "Los Angeles", "CA", "900121234", "Otolaryngology", org="Broadway ENT", enum_type="NPI-2"),
            _npi("0000000006", "1 Main St", "Oakland", "CA", "94612", "Otolaryngology"),
        ],
    }

    def fetch(params):
        return pages[params["taxonomy_description"]]

    sf = npi_specialists("94110", "San Francisco", "CA", fetch=fetch)
    assert [row["name"] for row in sf] == ["Market Allergy"]
    assert "94110" in sf[0]["address"] and "94609" not in sf[0]["address"]
    assert sf[0]["assumed"] == "allergy"
    la = npi_specialists(
        "90012", "Los Angeles", "CA", fetch=fetch,
        plans=lambda npi: ["Medicare"] if npi == "0000000005" else [],
    )
    assert len(la) == 1
    assert la[0]["name"] == "Broadway ENT"
    assert la[0]["assumed"] == "ent"
    assert la[0]["assumed_insurance"] == "Medicare"
    assert "90012" in la[0]["address"]
    out = lookup("90012", [], None, defects=["dampness_mold"], specialists=la)
    assert [row["name"] for row in out["clinics"]] == ["Broadway ENT"]
    assert "NPI registry" in out["note"] and "this ZIP" in out["note"]
    assert lookup("90012", [], None, defects=["lead_paint"], specialists=la)["clinics"] == []
    near = npi_specialists(
        "94612", "Oakland", "CA", fetch=fetch,
        near=lambda home, other: other in {home, "94609"},
    )
    assert any("94609" in row["address"] and "allergy" in row["assumed"] for row in near)
    assert all("94110" not in row["address"] and "90012" not in row["address"] for row in near)
    clinics = [{
        "name": "La Clinica", "website": "https://laclinica.org/", "tests": "", "insurance": "",
        "funding": "", "source_url": "https://findahealthcenter.hrsa.gov/?zip=94612",
    }]

    def search(query):
        assert "site:laclinica.org" in query and "lead" in query
        return [{"url": "https://laclinica.org/pediatrics", "text": "Blood lead testing for children. HIV program. Medi-Cal accepted."}]

    fill_services(clinics, lambda url: "", needed=["lead", "mold", "allergy"], search=search)
    assert "lead" in clinics[0]["tests"].lower()
    assert "HIV" not in clinics[0]["tests"]
    assert "Medi-Cal" in clinics[0]["insurance"]


def test_name_only_page_keeps_the_center_and_lookup_fills_address():
    text = "# Oakland Testing Site\nFree HIV testing. This page does not give a street address."
    directory = clinics_from_pages([{"url": "https://www.acphd.org/std", "text": text}])
    assert len(directory["clinics"]) == 1
    row = directory["clinics"][0]
    assert row["name"] == "Oakland Testing Site"
    assert row["address"] == ""
    assert row["source_url"] == "https://www.acphd.org/std"
    fill_address(row, "Oakland Testing Site\n700 Adeline St, Oakland, CA 94607\n", "94612")
    assert row["address"] == "700 Adeline St, Oakland, CA 94607"
    assert "700" in row["maps"] and "Adeline" in row["maps"]
    missed = {"name": "Oakland Testing Site", "address": "", "maps": ""}
    fill_address(missed, "No street is listed on this page.", "94612")
    assert missed["address"] == ""


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
        try:
            res = urlopen(req)
        except HTTPError as err:  # 4xx replies carry a JSON error body
            res = err
        with res:
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


def test_clinics_payload_does_not_call_npi_on_the_request(monkeypatch):
    monkeypatch.setattr("tenant.view.npi_cached", lambda *args, **kwargs: None)
    monkeypatch.setattr("tenant.view.npi_specialists", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("npi blocked the request")))
    monkeypatch.setattr("tenant.view.cited_directory", lambda zip_code: None)
    monkeypatch.setattr("tenant.view.hrsa_cached", lambda zip_code: {"clinics": [
        {"name": "Davis Health Center", "address": "1 Main St, Davis, CA 95618", "tests": "", "parent": "",
         "source_url": "https://findahealthcenter.hrsa.gov/", "insurance": "", "funding": "", "maps": "", "distance_mi": 1},
    ]})
    monkeypatch.setattr("tenant.view.hrsa_directory", lambda zip_code, timeout=5: (_ for _ in ()).throw(RuntimeError("hrsa blocked the request")))
    body = clinics_payload(case(), "lead_paint|bedroom sill below the sliding door")
    lead = next(group for group in body["groups"] if group["tag"] == "lead_paint")
    assert [row["name"] for row in lead["clinics"]] == ["Davis Health Center"]
    assert body["warming"] is False


def test_clinics_payload_does_not_block_when_the_cache_is_empty(monkeypatch):
    monkeypatch.setattr("tenant.view.cited_directory", lambda zip_code: None)
    monkeypatch.setattr("tenant.view.hrsa_cached", lambda zip_code: None)
    monkeypatch.setattr("tenant.view.hrsa_directory", lambda zip_code, timeout=5: (_ for _ in ()).throw(RuntimeError("hrsa blocked the request")))
    monkeypatch.setattr("tenant.view.npi_specialists", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("npi blocked the request")))
    monkeypatch.setattr("tenant.view.npi_cached", lambda *args, **kwargs: None)
    monkeypatch.setattr("tenant.view.warm_clinics", lambda case: None)
    body = clinics_payload(case(), "lead_paint|bedroom sill below the sliding door")
    assert body["warming"] is True
    assert body["clinics"] == []
    assert "94612" in body["search_url"]


def test_clinics_use_hrsa_when_no_cited_file(monkeypatch):
    monkeypatch.setattr("tenant.view.cited_directory", lambda zip_code: None)
    monkeypatch.setattr("tenant.view.npi_specialists", lambda *args, **kwargs: [])
    monkeypatch.setattr("tenant.view.hrsa_cached", lambda zip_code: {"clinics": [
        {"name": "Davis Health Center", "address": "1 Main St, Davis, CA 95618", "tests": "", "parent": "",
         "source_url": "https://findahealthcenter.hrsa.gov/", "insurance": "", "funding": "", "maps": "", "distance_mi": 1},
    ]})
    item = case()
    body = clinics_payload(item, "lead_paint|bedroom sill below the sliding door")
    lead = next(group for group in body["groups"] if group["tag"] == "lead_paint")
    assert [row["name"] for row in lead["clinics"]] == ["Davis Health Center"]
    assert lead["clinics"][0]["assumed"] == "blood lead"


def test_clinics_before_confirm_stays_offline(api, monkeypatch):
    monkeypatch.setenv("HOME_CLINICS_LIVE", "0")
    monkeypatch.setattr("tenant.view.npi_specialists", lambda *args, **kwargs: [])
    monkeypatch.setattr("tenant.view.hrsa_directory", lambda zip_code, timeout=5: {"clinics": [], "note": ""})
    client, _state = api
    client.call("POST", "/api/dev/login", b"{}")
    status, home = _onboard(client)
    assert status == 200
    hid = "dampness_mold|bedroom wall beside the window"
    assert any(h["hazard_id"] == hid for h in home["hazards"])
    status, detail = client.call("GET", "/api/hazards/" + quote(hid, safe=""))
    assert status == 200
    assert detail["blocks"]["health"]["status"] == "waiting"
    status, body = client.call("GET", "/api/hazards/" + quote(hid, safe="") + "/clinics")
    assert status == 200
    assert "94612" in body["search_url"]
    assert isinstance(body["clinics"], list)


def test_clinics_payload_groups_by_hazard():
    item = case()
    directory = {"clinics": [
        {"name": "Lead Site", "address": "1 Main St", "tests": "lead", "insurance": "Medi-Cal", "source_url": "https://example.com/a"},
        {"name": "Mold Site", "address": "4 Main St", "tests": "allergy", "source_url": "https://example.com/d"},
        {"name": "HIV Site", "address": "2 Main St", "tests": "HIV", "source_url": "https://example.com/b"},
    ]}
    body = clinics_payload(item, "dampness_mold|bedroom wall beside the window", directory)
    tags = [group["tag"] for group in body["groups"]]
    assert tags[0] == "dampness_mold"
    assert "lead_paint" in tags
    mold = next(group for group in body["groups"] if group["tag"] == "dampness_mold")
    lead = next(group for group in body["groups"] if group["tag"] == "lead_paint")
    assert [row["name"] for row in mold["clinics"]] == ["Mold Site"]
    assert [row["name"] for row in lead["clinics"]] == ["Lead Site"]
    assert {row["location"] for row in mold["hazards"]} == {
        "bedroom wall beside the window",
        "kitchen counter at the faucet",
    }
    assert "cough" in mold["reason"]
    assert "lead test" in lead["reason"]
    unconfirmed = case(confirmed=False)
    quiet = clinics_payload(unconfirmed, "dampness_mold|bedroom wall beside the window", directory)
    assert all(group["reason"] == "" for group in quiet["groups"])


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


# Google sign-in. Tokens below are signed with a throwaway key made at test time; only the
# Google cert fetch is stubbed, so audience, issuer, expiry and email_verified are checked for real.
CLIENT_ID = "test-client.apps.googleusercontent.com"


@pytest.fixture(scope="module")
def google_keys():
    from cryptography.hazmat.primitives import serialization  # a google-auth dependency
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    priv = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    pub = key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
    return priv.decode(), pub.decode()


def _stub_certs(google_keys, monkeypatch):
    from google.oauth2 import id_token

    monkeypatch.setattr(id_token, "_fetch_certs", lambda _request, _url: {"kid-test": google_keys[1]})


def _google_token(google_keys, monkeypatch, **claims):
    import time
    from google.auth import crypt, jwt

    priv = google_keys[0]
    _stub_certs(google_keys, monkeypatch)
    now = int(time.time())
    payload = {"iss": "https://accounts.google.com", "aud": CLIENT_ID, "sub": "1234567890", "email": "tenant@example.com",
               "email_verified": True, "name": "Test Tenant", "iat": now, "exp": now + 300}
    payload.update(claims)
    return jwt.encode(crypt.RSASigner.from_string(priv, "kid-test"), payload).decode()


def test_google_verifier_accepts_a_good_token_and_rejects_bad_ones(google_keys, monkeypatch):
    from tenant.auth import verify_google_token

    info = verify_google_token(_google_token(google_keys, monkeypatch), CLIENT_ID, request=object())
    assert info == {"sub": "1234567890", "email": "tenant@example.com", "name": "Test Tenant"}
    with pytest.raises(ValueError, match="audience"):
        verify_google_token(_google_token(google_keys, monkeypatch, aud="other-client"), CLIENT_ID, request=object())
    with pytest.raises(ValueError, match="issuer"):
        verify_google_token(_google_token(google_keys, monkeypatch, iss="https://evil.example"), CLIENT_ID, request=object())
    with pytest.raises(ValueError, match="expired"):
        verify_google_token(_google_token(google_keys, monkeypatch, exp=1), CLIENT_ID, request=object())
    with pytest.raises(ValueError, match="not verified"):
        verify_google_token(_google_token(google_keys, monkeypatch, email_verified=False), CLIENT_ID, request=object())
    with pytest.raises(ValueError):
        verify_google_token("not.a.token", CLIENT_ID, request=object())


@pytest.fixture
def google_api(google_keys, monkeypatch):
    from tenant.auth import verify_google_token

    def verify(credential, client_id):
        return verify_google_token(credential, client_id, request=object())

    _stub_certs(google_keys, monkeypatch)
    state = AppState(mailer=MemoryMailer(), google_client_id=CLIENT_ID, verify_google=verify, dev_login=False)
    httpd = serve(port=0, state=state)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    host, port = httpd.server_address
    yield Client(f"http://{host}:{port}"), state
    httpd.shutdown()


def _google_login(client, token):
    return client.call("POST", "/api/auth/google", json.dumps({"credential": token}).encode(), "application/json")


def test_google_login_creates_a_session_keyed_by_sub(google_api, google_keys, monkeypatch):
    client, state = google_api
    assert state.dev_login is False
    status, me = client.call("GET", "/api/me")
    assert (me["signed_in"], me["google"], me["dev_login"]) == (False, True, False)
    status, body = _google_login(client, _google_token(google_keys, monkeypatch))
    assert status == 200 and body["name"] == "Test Tenant"
    _status, me = client.call("GET", "/api/me")
    assert me["signed_in"] is True and me["email"] == "tenant@example.com"
    assert state.users["1234567890"]["sub"] == "1234567890"
    _status, home = _onboard(client)
    assert home["hazards"]
    case_id = state.users["1234567890"]["case_id"]
    assert case_id == new_case("1234567890", "", "", "x, Oakland, CA")["case_id"]
    status, _body = client.call("POST", "/api/dev/login", b"{}")
    assert status == 404


def test_google_login_rejects_fake_and_wrong_audience_tokens(google_api, google_keys, monkeypatch):
    client, state = google_api
    status, body = _google_login(client, "fake.token.value")
    assert status == 401 and "Invalid" in body["error"]
    status, _body = _google_login(client, _google_token(google_keys, monkeypatch, aud="other-client"))
    assert status == 401
    status, _body = client.call("POST", "/api/auth/google", b"{}", "application/json")
    assert status == 401
    assert state.sessions == {}
    _status, me = client.call("GET", "/api/me")
    assert me["signed_in"] is False


def test_logout_ends_the_session(google_api, google_keys, monkeypatch):
    client, state = google_api
    _google_login(client, _google_token(google_keys, monkeypatch))
    assert len(state.sessions) == 1
    status, _body = client.call("POST", "/api/auth/logout", b"")
    assert status == 200
    assert state.sessions == {}
    _status, me = client.call("GET", "/api/me")
    assert me["signed_in"] is False


def test_dev_login_flag_decides_the_fallback(monkeypatch):
    monkeypatch.delenv("HOME_DEV_LOGIN", raising=False)
    assert AppState(google_client_id="").dev_login is True
    assert AppState(google_client_id=CLIENT_ID).dev_login is False
    monkeypatch.setenv("HOME_DEV_LOGIN", "1")
    assert AppState(google_client_id=CLIENT_ID).dev_login is True
    monkeypatch.setenv("HOME_DEV_LOGIN", "0")
    assert AppState(google_client_id="").dev_login is False


def _raw(client, method, path, headers=None):
    req = Request(client.base + path, headers=headers or {}, method=method)
    try:
        res = urlopen(req)
    except HTTPError as err:
        res = err
    with res:
        return res.status, dict(res.headers), res.read()


def test_session_cookie_is_secure_only_behind_https(api):
    client, _state = api
    _status, headers, _body = _raw(client, "POST", "/api/dev/login")
    assert "Secure" not in headers["Set-Cookie"]
    assert "HttpOnly" in headers["Set-Cookie"]
    _status, headers, _body = _raw(client, "POST", "/api/dev/login", {"X-Forwarded-Proto": "https"})
    assert "; Secure" in headers["Set-Cookie"]
    _status, headers, _body = _raw(client, "POST", "/api/auth/logout", {"X-Forwarded-Proto": "https", "Cookie": headers["Set-Cookie"].split(";", 1)[0]})
    assert "Max-Age=0" in headers["Set-Cookie"]
    assert "; Secure" in headers["Set-Cookie"]


def test_built_web_app_is_served_with_index_fallback(api, tmp_path, monkeypatch):
    import tenant.server as server
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><title>Goldilocks</title>")
    (dist / "assets" / "app.js").write_text("console.log(1)")
    monkeypatch.setattr(server, "DIST", dist)
    client, _state = api
    status, headers, body = _raw(client, "GET", "/")
    assert status == 200
    assert b"Goldilocks" in body
    assert headers["Content-Type"].startswith("text/html")
    status, headers, body = _raw(client, "GET", "/assets/app.js")
    assert status == 200
    assert b"console" in body
    assert "immutable" in headers["Cache-Control"]
    status, _headers, body = _raw(client, "GET", "/tracking")
    assert status == 200
    assert b"Goldilocks" in body
    status, _headers, body = _raw(client, "GET", "/../../etc/passwd")
    assert status == 200
    assert b"Goldilocks" in body
    status, _headers, _body = _raw(client, "GET", "/api/nope")
    assert status == 401


def test_api_only_when_no_dist(api, tmp_path, monkeypatch):
    import tenant.server as server
    monkeypatch.setattr(server, "DIST", tmp_path / "missing")
    client, _state = api
    status, _headers, _body = _raw(client, "GET", "/")
    assert status == 401


def test_each_confirmed_hazard_keeps_its_own_health_and_legal_status(monkeypatch):
    """One finished hazard must not mark the others done, and the case stays running until the last one ends."""
    from tenant import live

    monkeypatch.setenv("HOME_LIVE", "1")
    item = new_case("live", "live@localhost", "Live", "123 Example St, Oakland, CA 94612")
    mold = "dampness_mold|bedroom wall beside the window"
    lead = "lead_paint|bedroom sill below the sliding door"
    for hid in (mold, lead):
        item["confirmed"][hid] = True
        live._set_run(item, "health", "running", hid=hid)
        live._set_run(item, "legal", "running", hid=hid)
    assert home_payload(item)["runs"]["health"] == "running"

    item["health"]["dampness_mold"] = {"health_risks": [{"text": "Damp rooms are linked with coughing."}]}
    live._set_run(item, "health", "done", hid=mold)
    live._set_run(item, "legal", "failed", "no assessment", hid=mold)
    assert hazard_payload(item, mold)["blocks"]["health"]["status"] == "done"
    assert hazard_payload(item, mold)["blocks"]["law"]["status"] == "failed"
    assert hazard_payload(item, mold)["blocks"]["law"]["error"] == "no assessment"
    # The lead-paint sheet is still loading, and the browser keeps polling because the case is still running.
    assert hazard_payload(item, lead)["blocks"]["health"]["status"] == "running"
    assert hazard_payload(item, lead)["blocks"]["law"]["status"] == "running"
    assert hazard_payload(item, lead)["blocks"]["law"]["error"] == ""
    assert home_payload(item)["runs"] == {**home_payload(item)["runs"], "health": "running", "legal": "running"}

    item["health"]["lead_paint"] = {"health_risks": [{"text": "Lead dust harms children."}]}
    live._set_run(item, "health", "done", hid=lead)
    live._set_run(item, "legal", "done", hid=lead)
    assert hazard_payload(item, lead)["blocks"]["health"]["status"] == "done"
    assert home_payload(item)["runs"]["health"] == "done"
    assert home_payload(item)["runs"]["legal"] == "done"
    assert home_payload(item)["errors"] == {}


def test_plan_placements_for_other_hazards_fall_back_to_the_walk_layout(monkeypatch):
    """The plan agent can finish before vision replaces the saved walkthrough. Its placements then name
    hazards that no longer exist, and the home must still show every current hazard on the model."""
    monkeypatch.setenv("HOME_LIVE", "1")
    monkeypatch.delenv("HOME_PLAN_JSON", raising=False)
    item = new_case("live", "live@localhost", "Live", "123 Example St, Oakland, CA 94612")
    rooms = [{"id": "room_0", "name": "kitchen", "polygon": [[0, 0], [4, 0], [4, 3], [0, 3]], "height": 2.6, "door": {"wall": "south", "width": 0.9, "center": 0.5}}]
    item["plan"] = {"shell": "plan", "scale": "bar", "source_url": "https://example.com/plan", "square_feet": 700, "rooms": rooms,
                    "placements": [{"room_id": "room_0", "hazard_id": "dampness_mold|bedroom wall beside the window", "icon": [1, 1, 1]}]}
    home = home_payload(item)
    assert home["shell"]["kind"] == "plan"
    assert home["layout"]["rooms"][0]["hazards"][0]["hazard_id"] == "dampness_mold|bedroom wall beside the window"

    item["vision"] = {"hazards": [{"hazard": "water_leak", "label": "Water leak", "location": "under the sink", "room": "kitchen",
                                   "severity": "high", "confidence": 0.8, "evidence_ids": ["video_1_t10s"], "first_seen_s": 10}]}
    home = home_payload(item)
    assert home["shell"]["kind"] == "plan"
    placed = [h["hazard_id"] for room in home["layout"]["rooms"] for h in room["hazards"]]
    assert placed == ["water_leak|under the sink"]


def test_reply_is_classified_on_the_landlord_words_only():
    from tenant.packet import own_text

    quoted = (
        "On it. On Sun, Oct 25, 2026 at 7:05 AM Rabah Babaci <tenant@example.com> wrote: "
        "I request that you: 1. Inspect the condition; send photographs; we will come by on Tuesday"
    )
    assert own_text(quoted) == "On it."
    assert classify_reply({"subject": "Re: notice", "body": quoted}) == "unclear"
    assert classify_reply({"subject": "Re: notice", "body": "We will come by on Tuesday. " + quoted[6:]}) == "schedule"
    assert own_text("Yes.") == "Yes."
    assert classify_reply({"subject": "Re: notice", "body": "Yes."}) == "unclear"
