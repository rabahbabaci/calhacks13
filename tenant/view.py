"""Assemble the screens from a case. Health and legal stay hidden until the tenant confirms that hazard."""
import hashlib
import re

from tenant import fixture
from tenant.clinics import lookup
from tenant.history import history_for
from tenant.layout import hazard_id, layout_from_report
from tenant.packet import build_tiles
from tenant.refs import URGENT

HOUSEHOLD = {"children": False, "older_adults": False, "pregnancy": False, "breathing_condition": False}


def jurisdiction_from_address(address):
    """Local parse only. The Census geocoder is not called in fixture mode."""
    match = re.search(r",\s*([^,]+),\s*([A-Z]{2})(?:\s+(\d{5}))?\s*$", address or "")
    if not match:
        return {"state": "CA", "city": "Oakland"}, ""
    return {"state": match.group(2), "city": match.group(1).strip()}, match.group(3) or ""


def new_case(sub, email, name, address):
    jurisdiction, zip_code = jurisdiction_from_address(address)
    return {
        "case_id": "case_" + hashlib.sha256(sub.encode()).hexdigest()[:10],
        "sub": sub,
        "email": email,
        "name": name,
        "address": address.strip(),
        "zip": zip_code,
        "jurisdiction": jurisdiction,
        "household": dict(HOUSEHOLD),
        "vision": fixture.vision_report(),
        "health": fixture.health(),
        "legal": fixture.legal(),
        "property": fixture.property_report(address.strip()),
        "confirmed": {},
        "messages": [],
        "evidence": [],
        "filed": [],
        "media": [],
        "fixture": True,
    }


def _public_hazard(case, h):
    hid = hazard_id(h)
    return {
        "hazard_id": hid,
        "label": h.get("label") or h["hazard"],
        "room": h.get("room") or "",
        "location": h.get("location") or "",
        "severity": h.get("severity") or "low",
        "confirmed": bool(case["confirmed"].get(hid)),
        "confidence": h.get("confidence"),
        "evidence_ids": list(h.get("evidence_ids") or []),
        "frame": h.get("bbox_frame") or (h.get("evidence_ids") or [""])[0],
        "urgent_message": URGENT.URGENT.get(h["hazard"]),
    }


def _find(case, hid):
    for h in case["vision"]["hazards"]:
        if hazard_id(h) == hid:
            return h
    return None


def _cites(assessment):
    sources = {s["id"]: s for s in (assessment or {}).get("sources") or []}
    rows = []
    for field in ("landlord_duty", "tenant_options"):
        for claim in (assessment or {}).get(field) or []:
            cites = [sources[i]["cite"] for i in claim.get("source_ids") or [] if i in sources and sources[i].get("cite")]
            rows.append({"field": field, "text": claim.get("text") or "", "cites": cites})
    return rows


def home_payload(case):
    tiles = build_tiles(case)
    return {
        "fixture": True,
        "case_id": case["case_id"],
        "address": case["address"],
        "name": case.get("name") or "",
        "email": case.get("email") or "",
        "layout": layout_from_report(case["vision"]),
        "hazards": [_public_hazard(case, h) for h in case["vision"]["hazards"]],
        "not_visible": case["vision"].get("not_visible_from_video") or [],
        "tiles": [{k: t[k] for k in ("id", "title", "kind", "status", "needs", "changed")} for t in tiles],
        "tracking": {"messages": list(case["messages"]), "evidence": list(case["evidence"])},
        "runs": {
            "hazard": "done",
            "property": "done",
            "health": "done" if any(case["confirmed"].values()) else "waiting",
            "legal": "done" if any(case["confirmed"].values()) else "waiting",
        },
    }


_SKIP_INITIAL = {"llc", "inc", "co", "corp", "ltd", "company"}


def _texts(items):
    out = []
    for item in items or []:
        text = item.get("text") if isinstance(item, dict) else str(item)
        text = str(text or "").strip()
        if text:
            out.append(text)
    return out


def _initials(name):
    words = [w for w in re.split(r"[^A-Za-z0-9]+", name or "") if w and w.lower() not in _SKIP_INITIAL]
    letters = "".join(w[0] for w in words[:2]).upper()
    return letters or (name or "LL")[:2].upper()


def _logo(owner):
    url = str((owner or {}).get("logo_url") or "").strip()
    return url if url.startswith("https://") else ""


def hazard_payload(case, hid):
    h = _find(case, hid)
    if h is None:
        return None
    tag = h["hazard"]
    confirmed = bool(case["confirmed"].get(hid))
    health = case["health"].get(tag) if confirmed else None
    legal = case["legal"].get(tag) if confirmed else None
    see = ""
    risks, precautions = [], []
    if health:
        risks = _texts(health.get("health_risks"))
        precautions = _texts(health.get("precautions"))
        doctors = _texts(health.get("see_a_doctor_if"))
        see = doctors[0] if doctors else ""
    law_rows = _cites(legal) if legal else []
    owner = (case.get("property") or {}).get("owner") or {}
    company = str(owner.get("name") or "Landlord")
    return {
        "hazard": _public_hazard(case, h),
        "blocks": {
            "place": {"status": "done"},
            "health": {
                "status": "done" if health else "waiting",
                "risks": risks,
                "precautions": precautions,
                "see_a_doctor_if": see,
                "disclaimer": (health or {}).get("disclaimer") or "",
            },
            "landlord": {
                "status": "done",
                "company": company,
                "initials": _initials(company),
                "logo_url": _logo(owner),
                **history_for(tag, case.get("property")),
            },
            "law": {
                "status": "done" if legal else "waiting",
                "duties": [r for r in law_rows if r["field"] == "landlord_duty"],
                "options": [r for r in law_rows if r["field"] == "tenant_options"],
                "disclaimer": (legal or {}).get("disclaimer") or "",
            },
        },
    }


def clinics_payload(case, hid, directory=None):
    h = _find(case, hid)
    if h is None:
        return None
    confirmed = bool(case["confirmed"].get(hazard_id(h)))
    health = case["health"].get(h["hazard"]) if confirmed else None
    return lookup(case.get("zip"), (health or {}).get("see_a_doctor_if"), directory)


def tile_by_id(case, tile_id):
    for tile in build_tiles(case):
        if tile["id"] == tile_id:
            return tile
    return None
