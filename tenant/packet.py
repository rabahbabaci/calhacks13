"""Next steps, from finished runs. No LLM.

Form links and filing steps come from the jurisdiction pack. A URL is shown only when it is
a pack source_url that the legal run cited, or a statute URL that run cited.
"""
import json
import re
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

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


def _norm(text):
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def unique_options(legal):
    """One row per option text. Two rooms that share a tag do not list the same fix twice."""
    seen = set()
    rows = []
    sources = {s["id"]: s for s in (legal or {}).get("sources") or []}
    for claim in (legal or {}).get("tenant_options") or []:
        text = str(claim.get("text") or "").strip()
        key = _norm(text)
        if not text or key in seen:
            continue
        seen.add(key)
        cites = [sources[i]["cite"] for i in claim.get("source_ids") or [] if i in sources and sources[i].get("cite")]
        rows.append({"text": text, "cites": cites, "action": classify_option(text)})
    return rows


def classify_option(text):
    """Map a legal option to a button, or None if it is not something Home can run."""
    blob = _norm(text)
    if re.search(r"\b(retaliat|presumed to have caused|courtesy notice|notice of violation|not blanket|not automatically)\b", blob):
        return None
    if re.search(r"\b(legal aid|lawyer|attorney)\b", blob):
        return "aid"
    if re.search(r"\bphotos?\b|\bphotograph", blob) or re.search(r"\bdocument (with|the|source|droppings)\b", blob):
        return "photos"
    if re.search(r"\b(complaint|inspect|oak311|code enforcement)\b", blob) or re.search(r"\breport (the |a |peeling|the condition)", blob):
        return "form"
    if re.search(r"\b(written notice|in writing|email the landlord|give the landlord)\b", blob):
        return "notice"
    return None


def step_groups(case):
    """Confirmed problems grouped by hazard tag, with that tag's unique fix options."""
    confirmed = case.get("confirmed") or {}
    groups = []
    by_tag = {}
    for h in (case.get("vision") or {}).get("hazards") or []:
        hid = hazard_id(h)
        if not confirmed.get(hid):
            continue
        tag = h["hazard"]
        if tag not in by_tag:
            group = {
                "tag": tag,
                "label": _label(h),
                "hazards": [],
                "options": [row for row in unique_options((case.get("legal") or {}).get(tag)) if row.get("action")],
            }
            by_tag[tag] = group
            groups.append(group)
        by_tag[tag]["hazards"].append({
            "hazard_id": hid,
            "room": h.get("room") or "",
            "location": h.get("location") or "",
            "label": _label(h),
        })
    return groups


def mail_time(row):
    raw = str((row or {}).get("at") or "").strip()
    if not raw or raw == "just now":
        return datetime.now(timezone.utc)
    try:
        parsed = parsedate_to_datetime(raw)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except (TypeError, ValueError, OverflowError):
        pass
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except ValueError:
        return datetime.now(timezone.utc)


def first_landlord_send(messages):
    for item in messages or []:
        if item.get("direction") == "out" and item.get("role") == "landlord" and item.get("status") == "sent":
            return item
    return None


def thread_messages(messages):
    """Landlord mail after the first outbound notice. Earlier mail is omitted."""
    first = first_landlord_send(messages)
    if not first:
        return []
    start = mail_time(first) - timedelta(minutes=2)
    rows = []
    for item in messages or []:
        if item.get("role") and item.get("role") != "landlord":
            continue
        if mail_time(item) < start:
            continue
        rows.append(item)
    rows.sort(key=lambda item: (
        mail_time(item),
        0 if item.get("direction") == "out" else 1,
        str(item.get("id") or item.get("gmail_id") or ""),
    ))
    return rows


def _tenant_close(name):
    who = (name or "").strip()
    return f"{who}\nTenant" if who else "The tenant"


def _email_subject(h, address):
    label = _label(h)
    place = (address or "").strip()
    if place:
        return f"Written notice of needed repair: {label} — {place}"
    return f"Written notice of needed repair: {label}"


def _statute_rows(legal):
    """Statutes the legal run already cited. Agency listings and invented cites are omitted."""
    sources = {
        s.get("id"): s
        for s in (legal or {}).get("sources") or []
        if s.get("id") and s.get("kind") == "statute" and str(s.get("cite") or "").strip()
        and s.get("show_as") not in ASK
    }
    rows = []
    seen = set()
    for claim in (legal or {}).get("landlord_duty") or []:
        text = str(claim.get("text") or "").strip()
        for sid in claim.get("source_ids") or []:
            source = sources.get(sid)
            if not source:
                continue
            cite = str(source.get("cite") or "").strip()
            if not cite or cite in seen:
                continue
            seen.add(cite)
            rows.append({
                "cite": cite,
                "url": str(source.get("url") or "").strip(),
                "text": text,
            })
    if rows:
        return rows
    for source in sources.values():
        cite = str(source.get("cite") or "").strip()
        if not cite or cite in seen:
            continue
        seen.add(cite)
        rows.append({"cite": cite, "url": str(source.get("url") or "").strip(), "text": ""})
    return rows


def _statute_block(legal, heading="Legal basis identified for this condition"):
    rows = _statute_rows(legal)
    if not rows:
        return ""
    lines = [heading]
    for i, row in enumerate(rows, start=1):
        line = f"{i}. {row['cite']}"
        if row.get("text"):
            line += f" — {row['text']}"
        lines.append(line)
        if row.get("url"):
            lines.append(f"   {row['url']}")
    return "\n".join(lines) + "\n\n"


def _email_body(h, address, name="", today=None, legal=None):
    today = today or date.today().isoformat()
    place = ", ".join(part for part in (h.get("room"), h.get("location") or "the dwelling") if part)
    premises = (address or "").strip() or "the leased premises"
    statutes = _statute_block(legal)
    return (
        f"{today}\n\n"
        f"Re: Written notice of needed repair — {premises}\n\n"
        "To the landlord / property manager:\n\n"
        f"I am the tenant at {premises}. This letter is written notice of a condition "
        "that requires inspection and repair under the landlord's duty to maintain the "
        "premises in a habitable condition.\n\n"
        "Condition requiring repair\n"
        f"1. Nature: {_label(h)}\n"
        f"2. Location: {place or 'the dwelling'}\n\n"
        f"{statutes}"
        "I request that you:\n"
        "1. Inspect the condition identified above;\n"
        "2. Complete the repair needed to restore the premises; and\n"
        "3. Reply in writing with the inspection date, the work to be performed, and the "
        "date you expect completion.\n\n"
        "I can provide photographs from a walkthrough of the unit upon request. "
        "Please keep all further communication about this request in writing so there is "
        "a complete record.\n\n"
        "Respectfully,\n"
        f"{_tenant_close(name)}"
    )


def _re_subject(subject):
    text = str(subject or "Written notice of needed repair").strip() or "Written notice of needed repair"
    if text.lower().startswith("re:"):
        return text
    return f"Re: {text}"


def classify_reply(message):
    """Read the landlord's last mail. Do not invent a legal conclusion."""
    blob = _norm(f"{(message or {}).get('subject') or ''} {(message or {}).get('body') or ''}")
    if not blob:
        return "unclear"
    if re.search(r"\b(evict|notice to quit|unlawful detainer|retaliat|raise (your |the )?rent|end (your |the )?lease)\b", blob):
        return "retaliation"
    if re.search(r"\b(not (our|the landlord.?s) (responsibility|problem|duty|job)|tenant.?s? (responsibility|duty)|we (will not|won'?t|cannot|can'?t) (repair|fix|do)|denied|no (code )?violation|not habitab)\b", blob):
        return "refusal"
    if re.search(r"\b(we (fixed|repaired)|has been (fixed|repaired|completed)|completed the (work|repair)|work is (done|complete)|issue (has been )?resolved)\b", blob):
        return "completed"
    if re.search(r"\b(send )?(photos?|pictures?|images?)\b|\bmore (info|information|details)\b|\baccess to (the )?(unit|apartment|home)\b|\bwhen can we (enter|come)\b", blob):
        return "info"
    if re.search(r"\b(monday|tuesday|wednesday|thursday|friday|saturday|sunday|tomorrow|today at|next (week|monday|tuesday)|appointment|scheduled|will come (by|on|at)|between \d{1,2})\b", blob):
        return "schedule"
    if re.search(r"\b(backlog|no (eta|timeline|date)|when we (can|get to it)|as soon as we can|very busy)\b", blob):
        return "delay"
    if re.search(r"\b(received your|thank you for (the |your )?(notice|email|letting)|we will (look|inspect|send|review|have)|we'?ll (look|inspect|send)|work order|maintenance (will|has been))\b", blob):
        return "ack"
    return "unclear"


REPLY = {
    "ack": {
        "title": "Ask for a written schedule",
        "blurb": "The landlord acknowledged the notice. Ask for the inspection date, the work, and the completion date.",
        "next": "email-followup",
        "ask": (
            "Thank you for acknowledging my written notice. Please confirm in writing:\n"
            "1. The date you will inspect the unit;\n"
            "2. The work you will perform; and\n"
            "3. The date you expect completion."
        ),
    },
    "schedule": {
        "title": "Confirm the visit in writing",
        "blurb": "The landlord offered a time. Confirm the date, time, and work in writing so the record is clear.",
        "next": "email-followup",
        "ask": (
            "Thank you for proposing a visit. Please confirm in writing:\n"
            "1. The date and time you will be at the unit;\n"
            "2. The work you will perform; and\n"
            "3. The date you expect completion."
        ),
    },
    "info": {
        "title": "Reply with the requested information",
        "blurb": "The landlord asked for more information. Reply in writing. Photos stay on Tracking.",
        "next": "email-followup",
        "ask": (
            "I can provide photographs from a walkthrough of the unit. "
            "After you have what you need, please confirm in writing:\n"
            "1. The date you will inspect the unit;\n"
            "2. The work you will perform; and\n"
            "3. The date you expect completion."
        ),
    },
    "completed": {
        "title": "Confirm the repair in writing",
        "blurb": "The landlord says the work is done. Ask them to confirm in writing what was repaired and when.",
        "next": "email-followup",
        "ask": (
            "Please confirm in writing:\n"
            "1. What repair was completed;\n"
            "2. The date the work was finished; and\n"
            "3. Whether any follow-up work remains.\n\n"
            "I will inspect the unit and keep this correspondence on the record."
        ),
    },
    "delay": {
        "title": "Send a written demand",
        "blurb": "The landlord did not give a date. Send a written demand for a schedule. If they still do not answer, call legal aid before you file.",
        "next": "email-followup",
        "escalate": "aid",
        "ask": (
            "Please provide a written schedule without further delay:\n"
            "1. The date you will inspect the unit;\n"
            "2. The work you will perform; and\n"
            "3. The date you expect completion.\n\n"
            "If I do not receive a written schedule, I will file a complaint with the city."
        ),
    },
    "refusal": {
        "title": "Put the refusal on the record",
        "blurb": "The landlord refused or denied responsibility. Call legal aid next. File under the cited statute only after that call, if you decide to.",
        "next": "phone-legal-aid",
        "escalate": "aid",
        "ask": (
            "I write to keep your reply on the record. The condition remains and still requires repair. "
            "I am contacting legal aid before I file.\n\n"
            "If you will repair it, please confirm in writing the inspection date, the work, and the completion date."
        ),
    },
    "retaliation": {
        "title": "Put the reply on the record",
        "blurb": "The reply mentions eviction or a similar threat. Call legal aid next. File under the cited statute only after that call, if you decide to.",
        "next": "phone-legal-aid",
        "escalate": "aid",
        "ask": (
            "I write to keep this correspondence on the record. I asked in writing for a needed repair. "
            "Please confirm the inspection date, the work, and the completion date.\n\n"
            "I am also contacting legal aid."
        ),
    },
    "unclear": {
        "title": "Ask for a written plan",
        "blurb": "The landlord replied. Ask for a written inspection date, the work, and the completion date.",
        "next": "email-followup",
        "ask": (
            "Please confirm in writing:\n"
            "1. Whether the condition has been inspected;\n"
            "2. What repair will be performed; and\n"
            "3. The date you expect the work to be completed."
        ),
    },
}


def _followup_body(address, last, name="", today=None, legal=None, kind="unclear"):
    today = today or date.today().isoformat()
    premises = (address or "").strip() or "the leased premises"
    prior = str((last or {}).get("subject") or "my earlier written notice").strip()
    statutes = _statute_block(legal, "This remains a written request under")
    ask = (REPLY.get(kind) or REPLY["unclear"])["ask"]
    return (
        f"{today}\n\n"
        f"Re: {prior}\n\n"
        "To the landlord / property manager:\n\n"
        f"I write regarding my earlier written notice for {premises}. "
        "Please continue this matter in writing.\n\n"
        f"{statutes}"
        f"{ask}\n\n"
        "Respectfully,\n"
        f"{_tenant_close(name)}"
    )


def _pack_for(jurisdiction):
    """City pack if we have one, else the statewide pack. Do not use another city's pack."""
    pack = RULES.load_pack(jurisdiction)
    if pack:
        return pack
    state = ((jurisdiction or {}).get("state") or "").lower()
    path = RULES.PACKS / f"{state}.json"
    if state and path.is_file():
        return json.loads(path.read_text(encoding="utf-8"))
    return {}


def _aid_score(row, city, county, zip_code):
    cities = [str(item).strip().lower() for item in (row.get("cities") or ([row["city"]] if row.get("city") else []))]
    counties = [str(item).strip().lower() for item in (row.get("counties") or ([row["county"]] if row.get("county") else []))]
    zips = [str(item).strip() for item in (row.get("zips") or [])]
    if city and city.lower() in cities:
        return 3
    if zip_code and zip_code in zips:
        return 2
    if county and county.lower() in counties:
        return 2
    if not cities and not counties and not zips:
        return 1
    return 0


def _aid_listing(pack, jurisdiction=None, zip_code=""):
    rows = [
        row for row in (pack or {}).get("legal_aid") or []
        if isinstance(row, dict) and (row.get("name") or row.get("phone"))
    ]
    city = str((jurisdiction or {}).get("city") or "").strip()
    county = str((jurisdiction or {}).get("county") or "").strip()
    zip_code = str(zip_code or (jurisdiction or {}).get("zip") or "").strip()
    ranked = []
    for row in rows:
        score = _aid_score(row, city, county, zip_code)
        if not score:
            continue
        ranked.append((score, 1 if str(row.get("phone") or "").strip() else 0, row))
    if not ranked:
        return {}
    ranked.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return ranked[0][2]


def _aid_title(aid):
    name = str((aid or {}).get("name") or "legal aid").strip() or "legal aid"
    phone = str((aid or {}).get("phone") or "").strip()
    if phone:
        return f"Call {name} at {phone}", phone
    return f"Call {name}", ""


def suggest_next(tiles, thread, filed=None):
    filed = set(filed or [])
    ids = {tile["id"] for tile in tiles}
    form = next((tile for tile in tiles if tile["id"] == "form-complaint"), None)
    if "phone-legal-aid" in filed and form and form.get("status") != "done":
        return {
            "tile_id": "form-complaint",
            "kind": "file",
            "blurb": "You called legal aid. If you decide to file, use the cited statute or city complaint.",
        }
    last = thread[-1] if thread else None
    if last and last.get("direction") == "out":
        return {"tile_id": "", "kind": "waiting", "blurb": "Your notice was sent. Wait for a written reply. The thread is on Tracking."}
    if last and last.get("direction") == "in":
        kind = classify_reply(last)
        plan = REPLY[kind]
        tile_id = plan["next"]
        if tile_id not in ids:
            tile_id = "phone-legal-aid" if "phone-legal-aid" in ids else ("email-followup" if "email-followup" in ids else "")
        return {"tile_id": tile_id, "kind": kind, "blurb": plan["blurb"]}
    ready = next((tile for tile in tiles if str(tile.get("id") or "").startswith("email-landlord|") and tile["status"] != "done"), None)
    if ready:
        return {"tile_id": ready["id"], "kind": "notice", "blurb": "Send written notice of the confirmed condition. Nothing is sent until you press Send."}
    return {"tile_id": "", "kind": "", "blurb": ""}


def _legal_for(case, hazard=None, tile_id=""):
    legal = case.get("legal") or {}
    tag = ""
    if hazard:
        tag = hazard.get("hazard") or ""
    elif tile_id:
        hid = tile_id.split("|", 1)[-1]
        tag = hid.split("|")[0]
    return legal.get(tag) if tag else None


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


def build_tiles(case, today=None):
    """case keys: address, jurisdiction, vision, health, legal, property, confirmed, messages, evidence, filed.

    health and legal are {tag: assessment}. A tag is ready only when that hazard is confirmed.
    """
    today = today or date.today().isoformat()
    pack = _pack_for(case.get("jurisdiction"))
    prop = case.get("property") or {}
    messages = case.get("messages") or []
    thread = thread_messages(messages)
    last_mail = thread[-1] if thread else None
    reply_kind = classify_reply(last_mail) if last_mail and last_mail.get("direction") == "in" else ""
    reply_plan = REPLY.get(reply_kind) or {}
    confirmed = case.get("confirmed") or {}
    filed = set(case.get("filed") or [])
    notice = "New evidence added." if case.get("evidence") else ""
    tiles = []
    confirmed_hazards = []
    tenant_name = case.get("name") or ""
    address = case.get("address") or ""

    for h in (case.get("vision") or {}).get("hazards") or []:
        hid = hazard_id(h)
        tag = h["hazard"]
        is_confirmed = bool(confirmed.get(hid))
        if tag in URGENT.URGENT:
            tiles.append({
                "id": f"phone-911|{hid}",
                "hazard_id": hid,
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
        if sent and last_mail and last_mail.get("direction") == "in":
            changed = reply_plan.get("blurb") or "Sent. The landlord replied. The thread is on Tracking."
        elif sent:
            changed = "Sent. Waiting for a written reply. See Tracking."
        else:
            changed = notice
        tiles.append({
            "id": tile_id,
            "hazard_id": hid,
            "title": "Send written notice",
            "kind": "email",
            "status": "done" if sent else "ready",
            "needs": [],
            "changed": changed,
            "preview": {
                "phone": "",
                "to": to,
                "subject": _email_subject(h, address),
                "body": _email_body(h, address, tenant_name, today, _legal_for(case, h)),
                "filled": [],
                "still_needed": [] if to else ["Landlord email address"],
                "form": None,
                "steps": (
                    ["This notice was sent. Waiting for a written reply. The thread is on Tracking."]
                    if sent else
                    ["Review the written notice.", "Send it to the landlord."]
                ),
                "note": (
                    "This notice was sent. Replies after this send appear on Tracking."
                    if sent else
                    "This is a written notice. Nothing is sent until you press Send."
                ),
            },
        })
    if last_mail and last_mail.get("direction") == "in":
        follow_to = _landlord_to(prop) or str(last_mail.get("counterparty") or "")
        prior = next(
            (item for item in reversed(thread) if item.get("direction") == "out" and str(item.get("tile_id") or "").startswith("email-landlord|")),
            last_mail,
        )
        tiles.append({
            "id": "email-followup",
            "title": reply_plan.get("title") or "Ask for a written plan",
            "kind": "email",
            "status": "ready",
            "needs": [],
            "changed": reply_plan.get("blurb") or "",
            "why": reply_plan.get("blurb") or "",
            "preview": {
                "phone": "",
                "to": follow_to,
                "subject": _re_subject(last_mail.get("subject")),
                "body": _followup_body(
                    address, last_mail, tenant_name, today,
                    _legal_for(case, tile_id=str((prior or {}).get("tile_id") or "")),
                    reply_kind or "unclear",
                ),
                "filled": [],
                "still_needed": [] if follow_to else ["Landlord email address"],
                "form": None,
                "steps": [
                    "Read the landlord reply on Tracking.",
                    "Send a written reply.",
                    "Wait for the next reply on Tracking.",
                ],
                "note": "This continues the written record. Nothing is sent until you press Send.",
            },
        })

    legal_ready = None
    for h in confirmed_hazards:
        assessment = (case.get("legal") or {}).get(h["hazard"])
        if assessment:
            legal_ready = assessment
            break
    form = _form_for(legal_ready, pack) if confirmed_hazards and legal_ready else None
    open_records = []
    for record in (prop.get("records") or []):
        if record.get("kind") != "violation":
            continue
        status = str(record.get("status") or "").lower()
        if any(word in status for word in ("closed", "abated", "resolved", "complied")):
            continue
        open_records.append(record)
    if form:
        labels = "; ".join(_label(h) for h in confirmed_hazards)
        photos = []
        for h in confirmed_hazards:
            photos.extend(h.get("evidence_ids") or [])
        tile_id = "form-complaint"
        done = tile_id in filed
        filled = [
            {"label": "Address", "value": case.get("address") or ""},
            {"label": "Condition", "value": labels},
            {"label": "Date", "value": today},
            {"label": "Photos", "value": ", ".join(photos) or "None attached yet"},
        ]
        for record in open_records:
            rid = record.get("ref") or record.get("id") or "record"
            filled.append({
                "label": "City record",
                "value": f"{record.get('date') or ''} {record.get('title') or rid} ({rid})".strip(),
            })
        still_needed = ["Your phone number", "A short description in your own words", "Your signature"]
        if open_records:
            still_needed.append("The city record number, when you file")
        note = "Opening the form does not mark this done. You mark it filed after you submit it yourself."
        if open_records:
            note = (
                "The city already has an open record for this address. Include that record when you file. "
                + note
            )
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
                "filled": filled,
                "still_needed": still_needed,
                "form": {"name": form["name"], "url": form["url"], "phone": form["phone"], "email": form["email"],
                         "official_form": form["official_form"]},
                "steps": form["steps"],
                "note": note,
            },
        })
    else:
        for record in open_records:
            rid = record.get("id") or record.get("ref") or "record"
            tiles.append({
                "id": f"record|{rid}",
                "title": record.get("title") or "City record",
                "kind": "form",
                "status": "ready",
                "needs": [],
                "changed": notice,
                "preview": {
                    "phone": "",
                    "to": "",
                    "subject": record.get("title") or "Public record",
                    "body": f"Public record {record.get('ref') or rid}: {record.get('title') or ''} ({record.get('date') or ''}).",
                    "filled": [{"label": "Record", "value": f"{record.get('ref') or rid} {record.get('title') or ''}"}],
                    "still_needed": [],
                    "form": None,
                    "steps": ["Confirm a problem on Case. This record is included when you file the city complaint."],
                    "note": "This is the city's record. It is not a second form to file.",
                },
            })

    wanted = {}
    for h in confirmed_hazards:
        for row in unique_options((case.get("legal") or {}).get(h["hazard"])):
            action = row.get("action")
            if action and action not in wanted:
                wanted[action] = row
    if wanted.get("notice"):
        for tile in tiles:
            if str(tile.get("id") or "").startswith("email-landlord|"):
                tile["why"] = wanted["notice"]["text"]
                break
    if wanted.get("form"):
        for tile in tiles:
            if tile["id"] == "form-complaint":
                tile["why"] = wanted["form"]["text"]
                break
    aid = _aid_listing(pack, case.get("jurisdiction"), case.get("zip") or "")
    aid_title, phone = _aid_title(aid)
    aid_called = "phone-legal-aid" in filed
    need_aid = bool(
        aid.get("name")
        and (
            wanted.get("aid")
            or reply_plan.get("escalate") == "aid"
            or any(tile["id"] == "form-complaint" for tile in tiles)
        )
    )
    if need_aid and not any(tile["id"] == "phone-legal-aid" for tile in tiles):
        why = (
            f"{aid_title}. This only dials. Confirm eligibility at intake."
            if phone else
            ((wanted.get("aid") or {}).get("text") or reply_plan.get("blurb") or f"{aid_title}. Confirm eligibility at intake.")
        )
        if reply_plan.get("escalate") == "aid":
            why = f"{reply_plan['blurb']} {aid_title}."
        tiles.append({
            "id": "phone-legal-aid",
            "title": aid_title,
            "kind": "phone",
            "status": "done" if aid_called else "ready",
            "needs": [],
            "changed": "You marked this called." if aid_called else (reply_plan.get("blurb") if reply_plan.get("escalate") == "aid" else notice),
            "why": why,
            "preview": {
                "phone": phone,
                "to": aid.get("email") or "",
                "subject": aid.get("name") or "Legal aid",
                "body": (aid.get("eligibility") or why) + (f"\n\nPhone: {phone}" if phone else ""),
                "filled": [{"label": "Phone", "value": phone}] if phone else [],
                "still_needed": [] if phone else ["A legal aid phone number for this city"],
                "form": {"name": aid.get("name") or "Legal aid", "url": aid.get("source_url") or "", "phone": phone,
                         "email": aid.get("email") or "", "official_form": False} if aid.get("source_url") else None,
                "steps": [f"Call {phone}."] if phone else ["Open the listing for a local number."],
                "note": "This only dials. Confirm eligibility at intake. Filing comes after this call, if you decide to file.",
            },
        })
    if aid_called:
        for tile in tiles:
            if tile["id"] == "form-complaint" and tile["status"] != "done":
                tile["changed"] = "You called legal aid. If you decide to file, use the cited statute or city complaint."
                tile["why"] = tile["changed"]
                break
    elif reply_plan.get("escalate") == "aid":
        for tile in tiles:
            if tile["id"] == "phone-legal-aid" and tile["status"] != "done":
                tile["changed"] = reply_plan["blurb"]
                if phone:
                    tile["why"] = f"{reply_plan['blurb']} {aid_title}."
                break
    return tiles
