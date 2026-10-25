"""Code-enforced rules for the property report. Pure functions, no network.

Records are what the public record shows: {data_id, kind, title, date, status, detail, ref}.
A record is kept only when its data_id is a result the agent actually fetched and that
result's raw text contains the record's reference number or its date.

Findings ({text, record_ids}) must cite kept records. The report is about real people and
companies, so findings that accuse anyone (illegal, slumlord, fraud, ...) are dropped:
the report says what the records show and leaves conclusions to the tenant and legal aid.
The owner and manager must appear, as written, in the result they cite.
"""
import re
from datetime import datetime, timezone

RECORD_KINDS = ["ownership", "violation", "complaint", "inspection", "permit", "enforcement", "litigation", "news", "other"]
FINDING_FIELDS = ["patterns", "unresolved"]
ACCUSATION = re.compile(
    r"\b(illegal(ly)?|slumlord|negligent|negligence|fraud(ulent)?|criminal|crook|scam|bad[- ]faith|harass(ing|ment)?|lying|liar)\b",
    re.I,
)
# Record categories that use those words without accusing anyone ("Illegal Dumping" in 311 data).
CATEGORY_PHRASES = re.compile(
    r"\billegal (dumping|parking|vending|encampments?|units?|construction|occupancy|signs?)\b|\b(tenant )?harassment (complaints?|petitions?)\b",
    re.I,
)


def accuses(text):
    return bool(ACCUSATION.search(CATEGORY_PHRASES.sub(" ", text)))


def date_variants(value):
    """The ways a date can appear in raw data: 2026-09-24, 09/24/2026, 9/24/2026, 20260924,
    and ArcGIS epoch milliseconds at UTC midnight."""
    value = (value or "").strip()
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%Y-%m-%dT%H:%M:%S"):
        try:
            d = datetime.strptime(value[:19] if "T" in value else value[:10], fmt)
            break
        except ValueError:
            d = None
    if d is None:
        return [value] if value else []
    epoch_ms = str(int(d.replace(tzinfo=timezone.utc).timestamp()) * 1000)
    return [d.strftime("%Y-%m-%d"), d.strftime("%m/%d/%Y"), f"{d.month}/{d.day}/{d.year}", d.strftime("%Y%m%d"), epoch_ms]


def check_record(record, results):
    data_id = str(record.get("data_id", "")).strip()
    if data_id not in results:
        return None, f"unknown data_id {data_id!r}"
    if str(record.get("kind")) not in RECORD_KINDS:
        return None, f"unknown kind {record.get('kind')!r}"
    title = str(record.get("title", "")).strip()
    if not title:
        return None, "empty title"
    raw = results[data_id]
    ref = str(record.get("ref", "")).strip()
    grounded = bool(ref) and ref in raw
    if not grounded:
        grounded = any(v and v in raw for v in date_variants(str(record.get("date", ""))))
    if not grounded:
        return None, "neither ref nor date appears in the cited data"
    clean = {k: str(record.get(k, "")).strip() for k in ("kind", "title", "date", "status", "detail", "ref")}
    clean["data_id"] = data_id
    return clean, None


def check_party(party, results):
    """Owner or manager. Its name, or its mailing address when no name is published, must
    appear in the result it cites; anything not found there is cleared."""
    if not party:
        return None
    data_id = str(party.get("data_id", "")).strip()
    if data_id not in results:
        return None
    raw = results[data_id].lower()
    name = str(party.get("name", "")).strip()
    mailing = str(party.get("mailing_address", "")).strip()
    name = name if name and name.lower() in raw else ""
    mailing = mailing if mailing and mailing.lower() in raw else ""
    if not name and not mailing:
        return None
    return {"name": name, "role": str(party.get("role", "")).strip(), "mailing_address": mailing, "data_id": data_id}


def validate(draft, results):
    """Returns (report fields, dropped). Record ids R1.. are assigned in the kept order."""
    dropped = []
    records = []
    id_map = {}
    for i, rec in enumerate(draft.get("records") or [], 1):
        clean, reason = check_record(rec, results)
        if reason:
            dropped.append({"field": "records", "text": str(rec.get("title", "")), "reason": reason})
            continue
        clean["id"] = f"R{len(records) + 1}"
        id_map[str(rec.get("id") or f"R{i}")] = clean["id"]
        records.append(clean)

    findings = {}
    for field in FINDING_FIELDS:
        findings[field] = []
        for claim in draft.get(field) or []:
            if not isinstance(claim, dict):
                dropped.append({"field": field, "text": str(claim), "reason": "claim is not an object"})
                continue
            text = str(claim.get("text", "")).strip()
            ids = [id_map.get(str(x).strip()) for x in claim.get("record_ids") or []]
            reason = None
            if not text:
                reason = "empty text"
            elif not ids or None in ids:
                reason = "cites no kept record"
            elif accuses(text):
                reason = "accusatory language"
            if reason:
                dropped.append({"field": field, "text": text, "reason": reason})
                continue
            findings[field].append({"text": text, "record_ids": sorted(set(ids), key=ids.index)})

    summary = str(draft.get("summary", "")).strip()
    if accuses(summary):
        dropped.append({"field": "summary", "text": summary, "reason": "accusatory language"})
        summary = ""

    return {
        "owner": check_party(draft.get("owner"), results),
        "manager": check_party(draft.get("manager"), results),
        "records": records,
        **findings,
        "summary": summary,
    }, dropped
