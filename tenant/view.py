"""Assemble the screens from a case. Health and legal stay hidden until the tenant confirms that hazard."""
import hashlib
import json
import os
import re
import threading
from pathlib import Path

from tenant import fixture
from tenant.clinics import (
    _assumed_tests, _matching_stated, cited_directory, hrsa_cached, hrsa_directory, lookup,
    matches_defect, npi_cached, npi_specialists, parse_directory, specialty_needed,
    tests_for_defects,
)
from tenant.history import details_from_report, history_for, named_from_summary, summary_for
from tenant.live import analyzing, empty_property, empty_runs, hazard_run, live_enabled
from tenant.layout import hazard_id, layout_from_report
from tenant.snapshot import photo_url
from tenant.packet import build_tiles, first_landlord_send, mail_time, step_groups, suggest_next, thread_messages
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
    live = live_enabled()
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
        "health": {} if live else fixture.health(),
        "legal": {} if live else fixture.legal(),
        "property": empty_property(address.strip()) if live else fixture.property_report(address.strip()),
        "confirmed": {},
        "messages": [],
        "evidence": [],
        "filed": [],
        "media": [],
        "fixture": not live,
        "runs": empty_runs() if live else {
            "hazard": "done",
            "property": "done",
            "plan": "done",
            "health": "waiting",
            "legal": "waiting",
        },
        "errors": {},
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
        "photo": photo_url(case, h),
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


def _plan_overlay():
    path = os.environ.get("HOME_PLAN_JSON")
    if not path:
        return None
    file = Path(path)
    if not file.is_file():
        return None
    try:
        return json.loads(file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _plan_matches_hazards(report, vision):
    """The plan agent can finish before the walkthrough does, so its placements may name the
    saved-walkthrough hazards instead of the ones the vision agent found. A plan that places none of
    the current hazards would draw rooms with no icons, so the schematic layout is used instead."""
    placed = {row.get("hazard_id") for row in report.get("placements") or []}
    current = {hazard_id(h) for h in (vision or {}).get("hazards") or []}
    return not current or bool(placed & current)


def _layout_from_plan(report):
    by_room = {}
    for row in report.get("placements") or []:
        by_room.setdefault(row["room_id"], []).append({"hazard_id": row["hazard_id"], "icon": row["icon"]})
    return {"rooms": [{**room, "hazards": by_room.get(room["id"], [])} for room in report.get("rooms") or []]}


def _shell_of(report):
    if not report:
        return {"kind": "schematic", "scale": "fixed", "source_url": "", "square_feet": None}
    kind = report.get("shell") or "schematic"
    return {
        "kind": kind,
        "scale": report.get("scale") or "fixed",
        "source_url": report.get("source_url") or "",
        "square_feet": report.get("square_feet"),
    }


def home_payload(case):
    tiles = build_tiles(case)
    saved = _plan_overlay()
    report = (saved or {}).get("report") or saved or case.get("plan")
    layout = layout_from_report(case["vision"])
    shell = _shell_of(None)
    if report and report.get("rooms") and report.get("shell") in {"plan", "listing"}:
        shell = _shell_of(report)
        if _plan_matches_hazards(report, case["vision"]):
            layout = _layout_from_plan(report)
    elif report and report.get("shell") == "schematic":
        shell = _shell_of(report)
    return {
        "fixture": bool(case.get("fixture", True)),
        "live": live_enabled(),
        "case_id": case["case_id"],
        "address": case["address"],
        "name": case.get("name") or "",
        "email": case.get("email") or "",
        "layout": layout,
        "shell": shell,
        "hazards": [_public_hazard(case, h) for h in case["vision"]["hazards"]],
        "not_visible": case["vision"].get("not_visible_from_video") or [],
        "tiles": [{k: t[k] for k in ("id", "title", "kind", "status", "needs", "changed", "hazard_id", "why") if k in t} for t in tiles],
        "next": suggest_next(tiles, thread_messages(case.get("messages") or []), case.get("filed") or []),
        "groups": step_groups(case),
        "landlord": landlord_profile(case),
        "tracking": {"messages": thread_messages(case.get("messages") or []), "evidence": list(case["evidence"])},
        "runs": _runs_of(case),
        "analyzing": analyzing(case),
        "errors": dict(case.get("errors") or {}),
    }


def ingest_mail(case, rows):
    """Keep landlord Gmail after the first notice. Do not invent messages."""
    first = first_landlord_send(case.get("messages"))
    if not first:
        return 0
    start = mail_time(first)
    seen = {str(item.get("gmail_id") or item.get("id") or "") for item in case.get("messages") or []}
    added = 0
    for row in rows or []:
        gid = str(row.get("gmail_id") or row.get("id") or "").strip()
        if not gid or gid in seen:
            continue
        if mail_time(row) < start:
            continue
        seen.add(gid)
        incoming = str(row.get("direction") or "") == "in"
        case["messages"].append({
            "id": gid,
            "gmail_id": gid,
            "direction": "in" if incoming else "out",
            "counterparty": str(row.get("counterparty") or "").strip(),
            "role": "landlord",
            "subject": str(row.get("subject") or "").strip(),
            "status": "received" if incoming else "sent",
            "at": str(row.get("at") or "just now"),
            "tile_id": str(row.get("tile_id") or ""),
            "body": str(row.get("body") or "").strip()[:2000],
        })
        added += 1
    return added


def _runs_of(case):
    runs = dict(case.get("runs") or {})
    if case.get("fixture", True):
        confirmed = any((case.get("confirmed") or {}).values())
        runs["hazard"] = "done"
        runs["property"] = "done"
        runs["plan"] = "done"
        runs["health"] = "done" if confirmed else "waiting"
        runs["legal"] = "done" if confirmed else "waiting"
    return runs


_SKIP_INITIAL = {"llc", "inc", "co", "corp", "ltd", "company"}


def _texts(items):
    out = []
    for item in items or []:
        text = item.get("text") if isinstance(item, dict) else str(item)
        text = str(text or "").strip()
        if text:
            out.append(text)
    return out


def _unique_texts(items):
    """Drop exact and shorter duplicate sentences so the sheet stays readable."""
    rows, keys = [], []
    for text in _texts(items):
        key = re.sub(r"\s+", " ", text.lower())
        replaced = False
        for i, old in enumerate(keys):
            if key == old or key in old:
                replaced = True
                break
            if old in key:
                rows[i] = text
                keys[i] = key
                replaced = True
                break
        if not replaced:
            rows.append(text)
            keys.append(key)
    return rows


def _initials(name):
    words = [w for w in re.split(r"[^A-Za-z0-9]+", name or "") if w and w.lower() not in _SKIP_INITIAL]
    letters = "".join(w[0] for w in words[:2]).upper()
    return letters or (name or "LL")[:2].upper()


def _contact(person):
    person = person or {}
    return {
        "name": str(person.get("name") or "").strip(),
        "role": str(person.get("role") or "").strip(),
        "mailing_address": str(person.get("mailing_address") or "").strip(),
        "email": str(person.get("email") or "").strip(),
        "phone": str(person.get("phone") or "").strip(),
        "website": str(person.get("website") or "").strip(),
    }


def _reachable(person):
    return bool(person.get("email") or person.get("phone") or person.get("website"))


def _is_manager(person):
    blob = f"{person.get('role') or ''} {person.get('name') or ''}"
    return bool(re.search(r"\bmanagement\b", blob, re.I))


def _primary_landlord(owner, manager):
    """The company a tenant can actually reach. FPI-style managers beat a silent owner."""
    if manager.get("name") and _is_manager(manager) and (_reachable(manager) or not _reachable(owner)):
        return manager
    if owner.get("name"):
        return owner
    return manager


def landlord_profile(case):
    """Contact and every public record. The hazard sheet still filters to one problem."""
    prop = case.get("property") or {}
    owner = _contact(prop.get("owner"))
    manager = _contact(prop.get("manager"))
    primary = _primary_landlord(owner, manager)
    company = primary.get("name") or owner["name"] or manager["name"] or named_from_summary(prop.get("summary")) or "Landlord"
    if company != "Landlord" and not owner["name"] and not manager["name"]:
        owner = {**owner, "name": company, "role": owner["role"] or "company"}
        primary = owner
    details = details_from_report(prop)
    for person in (primary, owner, manager):
        for key in ("mailing_address", "email", "phone", "website"):
            if not person.get(key) and details.get(key):
                person[key] = details[key]
    return {
        "company": company,
        "initials": _initials(company),
        "logo_url": _logo(primary) or _logo(owner) or _logo(manager),
        "contact": primary,
        "owner": owner,
        "manager": manager,
        "disclaimer": str(prop.get("disclaimer") or ""),
        "summary": str(prop.get("summary") or ""),
        **summary_for(prop),
    }


def _logo(owner):
    url = str((owner or {}).get("logo_url") or "").strip()
    return url if url.startswith("https://") else ""


def hazard_payload(case, hid):
    h = _find(case, hid)
    if h is None:
        return None
    tag = h["hazard"]
    confirmed = bool(case["confirmed"].get(hid))
    health = (case.get("health") or {}).get(tag) if confirmed else None
    legal = (case.get("legal") or {}).get(tag) if confirmed else None
    runs = _runs_of(case)
    errors = case.get("errors") or {}

    def run_status(agent, found):
        # This hazard's own run first (live mode), then the case-level flag (fixture mode).
        if found:
            return "done", ""
        if not confirmed:
            return "waiting", ""
        own = hazard_run(case, agent, hid)
        if own:
            return own["status"], own.get("error") or ""
        return runs.get(agent) or "waiting", errors.get(agent) or ""

    health_status, health_error = run_status("health", bool(health))
    legal_status, legal_error = run_status("legal", bool(legal))
    see = ""
    risks, precautions, doctors = [], [], []
    if health:
        risks = _unique_texts(health.get("health_risks"))
        precautions = _unique_texts(health.get("precautions"))
        doctors = _unique_texts(health.get("see_a_doctor_if"))
        see = doctors[0] if doctors else ""
    law_rows = _cites(legal) if legal else []
    owner = (case.get("property") or {}).get("owner") or {}
    company = str(owner.get("name") or "Landlord")
    return {
        "hazard": _public_hazard(case, h),
        "blocks": {
            "place": {"status": "done"},
            "health": {
                "status": health_status,
                "error": health_error,
                "risks": risks,
                "precautions": precautions,
                "see_a_doctor_if": see,
                "see_a_doctor": doctors,
                "disclaimer": (health or {}).get("disclaimer") or "",
            },
            "landlord": {
                "status": runs.get("property") or "done",
                "company": company,
                "initials": _initials(company),
                "logo_url": _logo(owner),
                "error": errors.get("property") or "",
                **history_for(tag, case.get("property")),
            },
            "law": {
                "status": legal_status,
                "error": legal_error,
                "duties": [r for r in law_rows if r["field"] == "landlord_duty"],
                "options": [],
                "disclaimer": (legal or {}).get("disclaimer") or "",
            },
        },
    }


def _see_reason(health):
    for line in (health or {}).get("see_a_doctor_if") or []:
        text = line.get("text") if isinstance(line, dict) else str(line)
        if text:
            return text
    return ""


def _clinic_place(case):
    jurisdiction = case.get("jurisdiction") or {}
    return case.get("zip") or "", jurisdiction.get("city") or "", jurisdiction.get("state") or "CA"


def warm_clinics(case):
    """Fill HRSA and NPI caches in the background so Find testing nearby is a short read."""
    zip_code, city, state = _clinic_place(case)
    if len(re.sub(r"\D", "", zip_code)) != 5:
        return
    threading.Thread(target=_warm_clinics, args=(zip_code, city, state), daemon=True).start()


def _warm_clinics(zip_code, city, state):
    try:
        hrsa_directory(zip_code)
    except Exception:
        pass
    try:
        npi_specialists(zip_code, city, state)
    except Exception:
        pass


def _specialists_ready(case, defects):
    """Use a finished NPI lookup. Never wait on the registry during this request."""
    if not specialty_needed(defects):
        return []
    return list(npi_cached(*_clinic_place(case)) or [])


def clinics_payload(case, hid, directory=None):
    h = _find(case, hid)
    if h is None:
        return None
    confirmed = bool(case["confirmed"].get(hazard_id(h)))
    see = None
    if confirmed:
        health = case["health"].get(h["hazard"]) or {}
        see = health.get("see_a_doctor_if")
    if directory is None:
        directory = cited_directory(case.get("zip") or "")
    if directory is None:
        directory = hrsa_cached(case.get("zip") or "")
    if directory is None:
        try:
            directory = hrsa_directory(case.get("zip") or "", timeout=5)
            if not (directory or {}).get("clinics"):
                directory = None
        except Exception:
            directory = None
    warming = directory is None
    if warming:
        warm_clinics(case)
    defects = []
    for item in (case.get("vision") or {}).get("hazards") or []:
        tag = item.get("hazard")
        if tag and tag not in defects:
            defects.append(tag)
    if h["hazard"] not in defects:
        defects.append(h["hazard"])
    specialists = _specialists_ready(case, defects)
    payload = lookup(case.get("zip"), see, directory, defects, specialists)
    raw = parse_directory(directory) if directory else []
    needed = tests_for_defects(defects)
    for row in raw:
        if row.get("assumed"):
            continue
        stated = _matching_stated(row.get("tests"), needed)
        row["assumed"] = ", ".join(_assumed_tests(row.get("name"), row.get("parent") or "", needed, stated))
        row["assumed_insurance"] = row.get("assumed_insurance") or ""
    if not payload.get("clinics") and raw:
        payload["clinics"] = raw
        if isinstance(directory, dict) and directory.get("note"):
            payload["note"] = directory["note"]
    payload["groups"] = _clinic_groups(case, hid, payload.get("clinics") or [])
    leftover = [row for row in raw if not any(matches_defect(row, group["tag"]) for group in payload["groups"])]
    if leftover and not any(group["clinics"] for group in payload["groups"]):
        payload["groups"].append({
            "tag": "health_centers",
            "label": "Community health centers",
            "reason": "These are the HRSA sites near this ZIP.",
            "hazards": [],
            "clinics": leftover,
        })
    if warming and not payload.get("clinics") and not specialists:
        payload["note"] = "Looking up health centers near this ZIP. The official HRSA search is also linked below."
    payload["warming"] = bool(warming and not payload.get("clinics") and not specialists)
    return payload


def _clinic_groups(case, hid, clinics):
    """One group per hazard tag. The opened problem is first. A site can appear twice."""
    confirmed = case.get("confirmed") or {}
    health = case.get("health") or {}
    groups = []
    by_tag = {}
    focus = ""
    for item in (case.get("vision") or {}).get("hazards") or []:
        tag = item.get("hazard")
        if not tag:
            continue
        if hazard_id(item) == hid:
            focus = tag
        if tag not in by_tag:
            group = {
                "tag": tag,
                "label": item.get("label") or tag,
                "hazards": [],
                "reason": "",
                "clinics": [],
            }
            by_tag[tag] = group
            groups.append(group)
        by_tag[tag]["hazards"].append({
            "hazard_id": hazard_id(item),
            "room": item.get("room") or "",
            "location": item.get("location") or "",
            "label": item.get("label") or tag,
        })
    for tag, group in by_tag.items():
        if any(confirmed.get(row["hazard_id"]) for row in group["hazards"]):
            group["reason"] = _see_reason(health.get(tag))
        group["clinics"] = [row for row in clinics if matches_defect(row, tag)]
    kept = [group for group in groups if group["clinics"] or tests_for_defects([group["tag"]])]
    kept.sort(key=lambda group: (0 if group["tag"] == focus else 1))
    return kept


def tile_by_id(case, tile_id):
    for tile in build_tiles(case):
        if tile["id"] == tile_id:
            return tile
    return None
