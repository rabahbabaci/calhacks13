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
LANDLORD_KINDS = KINDS | {"ownership", "inspection", "permit", "litigation", "news", "other"}
CLOSED = ("closed", "abated", "resolved", "complied", "corrected")
FIX = re.compile(r"\b(replac\w*|repair\w*|abat\w*|correct\w*|fix\w*|cleaned|removed)\b", re.I)
NAMED = re.compile(
    r"(?:run under|managed by|management(?: company)?(?: is|:)|owned by|operated by|landlord(?: is|:))\s+"
    r"([A-Z][\w&.,' -]{1,80}?)(?:\.|$)",
    re.I,
)
EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
EMAIL_SKIP = re.compile(r"(noreply|no-reply|donotreply|privacy|webmaster|example\.|placeholder)", re.I)
PHONE = re.compile(r"(?:\+1[\s.-]*)?(?:\(?\d{3}\)?[\s.-]*)\d{3}[\s.-]*\d{4}")
PAGE_URL = re.compile(r"https?://[^\s<>\"']+")
SKIP_HOSTS = (
    "facebook.com", "instagram.com", "twitter.com", "x.com", "linkedin.com", "yelp.com",
    "google.com", "bing.com", "reddit.com", "wikipedia.org", "zillow.com", "apartments.com",
    "youtube.com", "example.com", "example.org",
)
MAILING_LABELED = re.compile(
    r"(?:mailing(?: address)?|mail(?:ing)? to|office(?: address)?)\s*[:\-]\s*([^\n.]{8,80})",
    re.I,
)
MAILING_STREET = re.compile(
    r"\b\d{1,6}\s+[A-Za-z0-9.'#\- ]{2,40}\s+"
    r"(?:St|Street|Ave|Avenue|Blvd|Boulevard|Ct|Court|Dr|Drive|Rd|Road|Way|Ln|Lane|Pl|Place)\b"
    r"(?:[.,]?\s+[A-Za-z .]{2,30})?(?:[.,]?\s+[A-Z]{2})?(?:\s+\d{5})?",
    re.I,
)
DETAIL_KEYS = ("mailing_address", "email", "phone", "website")
DETAIL_ALIASES = {
    "mailing_address": ("mailing_address", "mailing", "mail", "office_address"),
    "email": ("email", "e-mail", "e_mail"),
    "phone": ("phone", "telephone", "tel", "phone_number"),
    "website": ("website", "url", "web", "site", "homepage"),
}


def named_from_summary(summary):
    """A company already named in the report prose, when owner/manager were not saved."""
    match = NAMED.search(summary or "")
    return match.group(1).strip(" .,") if match else ""


def _skipped_host(host):
    host = (host or "").lower()
    return any(host == item or host.endswith("." + item) for item in SKIP_HOSTS)


def _clean_website(url):
    url = (url or "").strip().rstrip(").,]")
    if url.startswith("http://"):
        url = "https://" + url[7:]
    if not url.startswith("https://"):
        return ""
    host = re.sub(r"^https?://", "", url).split("/")[0].split(":")[0].lower()
    if not host or _skipped_host(host):
        return ""
    return "https://" + host


def _format_phone(value):
    digits = re.sub(r"\D", "", value or "")
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    if len(digits) != 10 or digits[0] in "01" or digits[3] in "01":
        return ""
    if digits.startswith("555") and digits[3:5] == "01":
        return ""
    return f"({digits[:3]}) {digits[3:6]}-{digits[6:]}"


def _clean_mailing(value, skip=""):
    text = re.sub(r"\s+", " ", str(value or "")).strip(" .,;:")
    if len(text) < 8:
        return ""
    skip_l = re.sub(r"\s+", " ", skip or "").strip().lower()
    if skip_l and skip_l in text.lower():
        return ""
    return text


def details_from_text(text, skip_address=""):
    """Mailing, email, phone and website that already appear in report prose."""
    blob = str(text or "")
    emails = [m.group(0) for m in EMAIL.finditer(blob) if not EMAIL_SKIP.search(m.group(0))]
    emails = [addr for addr in emails if not _skipped_host(addr.split("@")[-1])]
    phones = []
    seen = set()
    for match in PHONE.finditer(blob):
        phone = _format_phone(match.group(0))
        if phone and phone not in seen:
            seen.add(phone)
            phones.append(phone)
    website = ""
    for match in PAGE_URL.finditer(blob):
        website = _clean_website(match.group(0))
        if website:
            break
    mailing = ""
    labeled = MAILING_LABELED.search(blob)
    if labeled:
        mailing = _clean_mailing(labeled.group(1), skip_address)
    if not mailing:
        for match in MAILING_STREET.finditer(blob):
            mailing = _clean_mailing(match.group(0), skip_address)
            if mailing:
                break
    return {
        "mailing_address": mailing,
        "email": emails[0] if emails else "",
        "phone": phones[0] if phones else "",
        "website": website,
    }


def details_from_party(party):
    party = party if isinstance(party, dict) else {}
    found = {key: "" for key in DETAIL_KEYS}
    for key, aliases in DETAIL_ALIASES.items():
        for alias in aliases:
            value = str(party.get(alias) or "").strip()
            if not value:
                continue
            if key == "phone":
                value = _format_phone(value) or value
            elif key == "website":
                value = _clean_website(value) or value
            found[key] = value
            break
    return found


def merge_details(base, extra):
    out = dict(base or {key: "" for key in DETAIL_KEYS})
    for key in DETAIL_KEYS:
        if not out.get(key) and extra.get(key):
            out[key] = extra[key]
    return out


def details_from_report(report):
    """Pull the four landlord contact fields from anywhere the report already has them."""
    report = report or {}
    found = {key: "" for key in DETAIL_KEYS}
    for key in ("owner", "manager", "contact"):
        found = merge_details(found, details_from_party(report.get(key)))
    chunks = [report.get("summary"), report.get("disclaimer")]
    for field in ("records", "unresolved", "patterns", "coverage"):
        for item in report.get(field) or []:
            if isinstance(item, dict):
                found = merge_details(found, details_from_party(item))
                chunks.append(" ".join(str(item.get(k) or "") for k in (*DETAIL_KEYS, "title", "detail", "text", "source", "result", "url")))
            else:
                chunks.append(item)
    found = merge_details(found, details_from_text(" ".join(str(chunk or "") for chunk in chunks), report.get("address") or ""))
    return {key: found.get(key) or "" for key in DETAIL_KEYS}


def _blob(record):
    return " ".join(str(record.get(k) or "") for k in ("title", "detail", "status")).lower()


def _closed(status):
    text = (status or "").lower()
    return any(word in text for word in CLOSED)


def _row(record, fallback_id=""):
    status = str(record.get("status") or "")
    detail = str(record.get("detail") or "").strip()
    closed = _closed(status)
    return {
        "id": str(record.get("id") or record.get("ref") or fallback_id or ""),
        "date": record.get("date") or "",
        "status": status,
        "title": record.get("title") or "",
        "closed": closed,
        "fix": detail if closed and FIX.search(detail) else "",
        "ref": record.get("ref") or "",
    }


def history_for(tag, report):
    keys = KEYWORDS.get(tag, ())
    matched = []
    for record in (report or {}).get("records") or []:
        if record.get("kind") not in KINDS:
            continue
        if keys and not any(k in _blob(record) for k in keys):
            continue
        matched.append(_row(record))
    return {
        "open": sum(1 for r in matched if not r["closed"]),
        "closed": sum(1 for r in matched if r["closed"]),
        "records": matched,
    }


def _has_party(report):
    for key in ("owner", "manager"):
        party = (report or {}).get(key)
        if isinstance(party, dict) and str(party.get("name") or "").strip():
            return True
    return False


def _finding_rows(report):
    rows = []
    for field, closed in (("unresolved", False), ("patterns", True)):
        for i, item in enumerate((report or {}).get(field) or []):
            text = item.get("text") if isinstance(item, dict) else str(item)
            text = str(text or "").strip()
            if not text:
                continue
            rows.append({
                "id": f"{field}-{i + 1}",
                "date": "",
                "status": "closed" if closed else "open",
                "title": text,
                "closed": closed,
                "fix": "",
                "ref": "",
            })
    return rows


def _party_rows(report):
    rows = []
    seen = set()
    for key in ("manager", "owner"):
        party = (report or {}).get(key) or {}
        if not isinstance(party, dict):
            continue
        name = str(party.get("name") or "").strip()
        if not name or name.lower() in seen:
            continue
        seen.add(name.lower())
        contact = " · ".join(part for part in (
            str(party.get("mailing_address") or "").strip(),
            str(party.get("email") or "").strip(),
            str(party.get("phone") or "").strip(),
            str(party.get("website") or "").strip(),
        ) if part)
        rows.append({
            "id": f"party-{key}",
            "date": "",
            "status": str(party.get("role") or key).strip(),
            "title": name,
            "closed": False,
            "fix": contact,
            "ref": "",
        })
    return rows


def summary_for(report):
    """Every public record on the property, not just one hazard."""
    matched = []
    for i, record in enumerate((report or {}).get("records") or []):
        title = str(record.get("title") or "").strip()
        if not title:
            continue
        matched.append(_row(record, f"rec-{i + 1}"))
    if not matched:
        matched = _finding_rows(report)
    if not _has_party(report):
        lifted = named_from_summary((report or {}).get("summary"))
        if lifted:
            extra = dict(report or {})
            extra["owner"] = {**(extra.get("owner") if isinstance(extra.get("owner"), dict) else {}), "name": lifted, "role": "company"}
            matched = matched + [row for row in _party_rows(extra) if row["title"] not in {r["title"] for r in matched}]
    if not matched:
        matched = _party_rows(report)
    if not matched and str((report or {}).get("summary") or "").strip():
        matched.append({
            "id": "summary",
            "date": "",
            "status": "note",
            "title": str(report.get("summary") or "").strip(),
            "closed": False,
            "fix": "",
            "ref": "",
        })
    seen = set()
    unique = []
    for row in matched:
        key = row["id"] or row["title"]
        if key in seen:
            continue
        seen.add(key)
        if not row["id"]:
            row["id"] = f"rec-{len(unique) + 1}"
        unique.append(row)
    return {
        "open": sum(1 for r in unique if not r["closed"]),
        "closed": sum(1 for r in unique if r["closed"]),
        "records": unique,
    }
