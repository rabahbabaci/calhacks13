"""Match one hazard tag to public records from the property report. No LLM.

A fix is shown only when the record is closed or abated and its own detail says the work that was done.
"""
import re

KEYWORDS = {
    "dampness_mold": ("damp", "moisture", "mildew", "mold"),
    "water_leak": ("leak", "plumb", "pipe", "water stain", "flood"),
    "rodents": ("rodent", "rat", "mouse", "mice"),
    "cockroaches": ("roach", "cockroach", "vermin"),
    "exposed_wiring": ("wiring", "electrical", "outlet"),
    "lead_paint": ("lead", "peeling paint", "paint"),
    "inadequate_heat": ("heat", "heating", "furnace"),
    "carbon_monoxide": ("carbon monoxide", "co alarm"),
    "gas_leak": ("gas leak", "gas odor", "natural gas"),
}

KINDS = {"violation", "complaint", "enforcement"}
CLOSED = ("closed", "abated", "resolved", "complied", "corrected")
FIX = re.compile(r"\b(replac\w*|repair\w*|abat\w*|correct\w*|fix\w*|cleaned|removed)\b", re.I)


def _blob(record):
    return " ".join(str(record.get(k) or "") for k in ("title", "detail", "status")).lower()


def _closed(status):
    text = (status or "").lower()
    return any(word in text for word in CLOSED)


def history_for(tag, report):
    keys = KEYWORDS.get(tag, ())
    matched = []
    for record in (report or {}).get("records") or []:
        if record.get("kind") not in KINDS:
            continue
        if keys and not any(k in _blob(record) for k in keys):
            continue
        status = str(record.get("status") or "")
        detail = str(record.get("detail") or "").strip()
        closed = _closed(status)
        fix = detail if closed and FIX.search(detail) else ""
        matched.append({
            "id": record.get("id") or record.get("ref") or "",
            "date": record.get("date") or "",
            "status": status,
            "title": record.get("title") or "",
            "closed": closed,
            "fix": fix,
            "ref": record.get("ref") or "",
        })
    return {
        "open": sum(1 for r in matched if not r["closed"]),
        "closed": sum(1 for r in matched if r["closed"]),
        "records": matched,
    }
