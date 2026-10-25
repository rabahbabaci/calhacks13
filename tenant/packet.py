"""Next steps, from finished runs. No LLM.

Form links and filing steps come from the jurisdiction pack. A URL is shown only when it is
a pack source_url that the legal run cited, or a statute URL that run cited.
"""
import re
from datetime import date

from tenant.history import history_for
from tenant.layout import hazard_id
from tenant.refs import RULES, URGENT

ASK = {"legal_aid_question", "eligibility_check_needed"}


def _procedures(pack):
    return [{**p, "source_id": f"C{i}"} for i, p in enumerate((pack or {}).get("city_procedures") or [], start=1)]


def _steps(proc, pack):
    if proc.get("show_as") in ASK:
        aid = ((pack or {}).get("legal_aid") or [{}])[0]
        who = " ".join(bit for bit in (aid.get("name"), aid.get("phone"), aid.get("source_url")) if bit)
        return [f"Ask legal aid first. {who}".strip()]
    parts = [p.strip() for p in re.split(r"(?<=\.)\s+", proc.get("how") or "") if p.strip()]
    send = []
    if proc.get("email"):
        send.append(f"email {proc['email']}")
    if proc.get("phone"):
        send.append(f"call {proc['phone']}")
    if proc.get("source_url"):
        send.append(f"open {proc['source_url']}")
    if send:
        parts.append("Where to send it: " + ", ".join(send) + ".")
    return parts


def _cited_urls(legal):
    return {s.get("url") for s in (legal or {}).get("sources") or [] if s.get("url")}


def _form_for(legal, pack):
    """The complaint procedure whose URL the legal run cited, else the first cited statute."""
    urls = _cited_urls(legal)
    chosen = None
    for proc in _procedures(pack):
        if proc.get("source_url") not in urls:
            continue
        if proc.get("show_as") in ASK:
            continue
        if chosen is None or "complaint" in proc.get("name", "").lower():
            chosen = proc
            if "complaint" in proc.get("name", "").lower():
                break
    if chosen:
        return {
            "name": chosen["name"],
            "url": chosen["source_url"],
            "phone": chosen.get("phone") or "",
            "email": chosen.get("email") or "",
            "steps": _steps(chosen, pack),
            "official_form": True,
        }
    for source in (legal or {}).get("sources") or []:
        if source.get("kind") == "statute" and source.get("url"):
            return {
                "name": source.get("cite") or "Statute",
                "url": source["url"],
                "phone": "",
                "email": "",
                "steps": ["No separate official form is listed for this action.", f"Read {source.get('cite') or 'the statute'}: {source['url']}"],
                "official_form": False,
            }
    return None


def _label(h):
    return h.get("label") or RULES.LABELS.get(h["hazard"], h["hazard"])


def _email_body(h, address):
    return (
        f"I live at {address}.\n\n"
        f"Please repair the following condition: {_label(h)}.\n"
        f"Place: {h.get('location') or 'the home'}.\n"
        "I can share photos from my walkthrough.\n\n"
        "Please reply in writing with what you will do and when."
    )


def _landlord_to(prop):
    for who in (prop.get("manager"), prop.get("owner")):
        if isinstance(who, dict) and who.get("email"):
            return who["email"]
    return ""


def _sent_landlord(messages, tile_id):
    return any(
        m.get("tile_id") == tile_id and m.get("direction") == "out" and m.get("role") == "landlord" and m.get("status") == "sent"
        for m in messages
    )


def _inbound(messages):
    return any(m.get("direction") == "in" and m.get("role") == "landlord" for m in messages)


def build_tiles(case, today=None):
    """case keys: address, jurisdiction, vision, health, legal, property, confirmed, messages, evidence, filed.

    health and legal are {tag: assessment}. A tag is ready only when that hazard is confirmed.
    """
    today = today or date.today().isoformat()
    pack = RULES.load_pack(case.get("jurisdiction"))
    prop = case.get("property") or {}
    messages = case.get("messages") or []
    confirmed = case.get("confirmed") or {}
    filed = set(case.get("filed") or [])
    notice = "New evidence added." if case.get("evidence") else ""
    tiles = []
    confirmed_hazards = []

    for h in (case.get("vision") or {}).get("hazards") or []:
        hid = hazard_id(h)
        tag = h["hazard"]
        is_confirmed = bool(confirmed.get(hid))
        if tag in URGENT.URGENT:
            tiles.append({
                "id": f"phone-911|{hid}",
                "title": "Call emergency services",
                "kind": "phone",
                "status": "ready",
                "needs": [],
                "changed": notice,
                "preview": {
                    "phone": "911",
                    "subject": "Call 911",
                    "body": URGENT.URGENT[tag],
                    "to": "",
                    "filled": [],
                    "still_needed": [],
                    "form": None,
                    "steps": [],
                    "note": "This only dials. It does not send a message.",
                },
            })
            continue
        if not is_confirmed:
            continue
        confirmed_hazards.append(h)
        tile_id = f"email-landlord|{hid}"
        sent = _sent_landlord(messages, tile_id)
        to = _landlord_to(prop)
        tiles.append({
            "id": tile_id,
            "title": "Email the landlord",
            "kind": "email",
            "status": "done" if sent else "ready",
            "needs": [],
            "changed": "Sent to the landlord." if sent else notice,
            "preview": {
                "phone": "",
                "to": to,
                "subject": f"Repair request: {_label(h)}",
                "body": _email_body(h, case.get("address") or ""),
                "filled": [],
                "still_needed": [] if to else ["Landlord email address"],
                "form": None,
                "steps": [],
                "note": "Nothing is sent until you press Send.",
            },
        })

    legal_ready = None
    for h in confirmed_hazards:
        assessment = (case.get("legal") or {}).get(h["hazard"])
        if assessment:
            legal_ready = assessment
            break
    form = _form_for(legal_ready, pack) if confirmed_hazards and legal_ready else None
    if form:
        labels = "; ".join(_label(h) for h in confirmed_hazards)
        photos = []
        for h in confirmed_hazards:
            photos.extend(h.get("evidence_ids") or [])
        tile_id = "form-complaint"
        done = tile_id in filed
        tiles.append({
            "id": tile_id,
            "title": form["name"] if form["official_form"] else "Read the statute, then file yourself",
            "kind": "form",
            "status": "done" if done else "ready",
            "needs": [],
            "changed": "You marked this filed." if done else notice,
            "preview": {
                "phone": form["phone"],
                "to": form["email"],
                "subject": "DRAFT",
                "body": "",
                "filled": [
                    {"label": "Address", "value": case.get("address") or ""},
                    {"label": "Condition", "value": labels},
                    {"label": "Date", "value": today},
                    {"label": "Photos", "value": ", ".join(photos) or "None attached yet"},
                ],
                "still_needed": ["Your phone number", "A short description in your own words", "Your signature"],
                "form": {"name": form["name"], "url": form["url"], "phone": form["phone"], "email": form["email"],
                         "official_form": form["official_form"]},
                "steps": form["steps"],
                "note": "Opening the form does not mark this done. You mark it filed after you submit it yourself.",
            },
        })

    for record in (prop.get("records") or []):
        if record.get("kind") != "violation":
            continue
        status = str(record.get("status") or "").lower()
        if any(word in status for word in ("closed", "abated", "resolved", "complied")):
            continue
        rid = record.get("id") or record.get("ref") or "record"
        tiles.append({
            "id": f"record|{rid}",
            "title": "Attach the public record",
            "kind": "form",
            "status": "done" if f"record|{rid}" in filed else "ready",
            "needs": [],
            "changed": notice,
            "preview": {
                "phone": "",
                "to": "",
                "subject": record.get("title") or "Public record",
                "body": f"Public record {record.get('ref') or rid}: {record.get('title') or ''} ({record.get('date') or ''}).",
                "filled": [{"label": "Record", "value": f"{record.get('ref') or rid} {record.get('title') or ''}"}],
                "still_needed": ["Attach the city's own record when you file"],
                "form": {"name": form["name"], "url": form["url"], "phone": "", "email": "", "official_form": True} if form else None,
                "steps": form["steps"] if form else ["Keep the record number with your complaint."],
                "note": "This is the city's record. Home does not file it.",
            },
        })

    if any(_sent_landlord(messages, t["id"]) for t in tiles if t["kind"] == "email") and not _inbound(messages):
        tiles.append({
            "id": "log-reply",
            "title": "Log their reply",
            "kind": "email",
            "status": "ready",
            "needs": [],
            "changed": "The landlord email was sent. Log their reply here.",
            "preview": {
                "phone": "",
                "to": "",
                "subject": "Reply you received",
                "body": "",
                "filled": [],
                "still_needed": ["Who wrote back", "What they said"],
                "form": None,
                "steps": [],
                "note": "This records a reply you received. It does not send email.",
            },
        })
    return tiles
