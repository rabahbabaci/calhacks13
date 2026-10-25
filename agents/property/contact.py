"""Company-landlord contact from pages the property agent already fetched or searched.

A company, LLC or apartment community is a valid landlord. This module does not invent a
person. Phone, email and website are kept only when they appear in fetched text.
"""
import re
import urllib.parse

CORPORATE = re.compile(
    r"\b(llc|l\.?l\.?c\.?|inc|incorporated|corp|corporation|ltd|limited|l\.?p\.?|"
    r"llp|plc|\bco\b|company|apartments?|homes|housing|propert(?:y|ies)|management|"
    r"realty|real estate|holdings?|partners|trust|associates|group|ventures|"
    r"investments?|communities|residences|developers?)\b",
    re.I,
)
STOP = {
    "llc", "inc", "incorporated", "corp", "corporation", "ltd", "limited", "lp", "llp",
    "plc", "co", "company", "the", "and", "of", "apartment", "apartments", "homes",
    "housing", "property", "properties", "management", "realty", "real", "estate",
    "holdings", "holding", "partners", "trust", "associates", "group", "ventures",
    "investment", "investments", "communities", "residences", "developers",
}
EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
PAGE_URL = re.compile(r"https?://[^\s<>\"']+")
MAILING_LABELED = re.compile(
    r"(?:mailing(?: address)?|mail(?:ing)? to|office(?: address)?)\s*[:\-]\s*([^\n.]{8,80})",
    re.I,
)
EMAIL_SKIP = re.compile(
    r"(noreply|no-reply|donotreply|do-not-reply|privacy|webmaster|sentry|wixpress|"
    r"example\.|godaddy|wordpress|cloudflare|placeholder|invalid)",
    re.I,
)
PREFERRED_EMAIL = ("leasing", "office", "info", "contact", "resident", "manager", "rent", "hello", "support", "admin")
PHONE = re.compile(r"(?:\+1[\s.-]*)?(?:\(?\d{3}\)?[\s.-]*)\d{3}[\s.-]*\d{4}")
SKIP_HOSTS = {
    "facebook.com", "instagram.com", "twitter.com", "x.com", "linkedin.com", "yelp.com",
    "google.com", "bing.com", "reddit.com", "wikipedia.org", "zillow.com", "apartments.com",
    "apartmentfinder.com", "rent.com", "yellowpages.com", "mapquest.com", "bbb.org",
    "youtube.com", "hotpads.com", "realtor.com", "trulia.com", "craigslist.org",
}


def is_corporate(name):
    """True when the identified landlord is a company, not a person's name."""
    name = str(name or "").strip()
    return bool(name) and bool(CORPORATE.search(name))


def default_role(name):
    if re.search(r"\b(management|manager)\b", name or "", re.I):
        return "management company"
    return "company"


def tokens(name):
    return [t for t in re.findall(r"[a-z0-9]+", (name or "").lower()) if t not in STOP and len(t) > 2]


def digits(value):
    return re.sub(r"\D", "", value or "")


def format_phone(value):
    d = digits(value)
    if len(d) == 11 and d.startswith("1"):
        d = d[1:]
    if len(d) != 10 or d[0] in "01" or d[3] in "01":
        return ""
    if d.startswith("555") and d[3:5] == "01":
        return ""
    return f"({d[:3]}) {d[3:6]}-{d[6:]}"


def phone_in_text(phone, text):
    d = digits(phone)
    if len(d) == 11 and d.startswith("1"):
        d = d[1:]
    return len(d) == 10 and d in digits(text)


def clean_website(url):
    url = (url or "").strip()
    if url.startswith("http://"):
        url = "https://" + url[7:]
    if not url.startswith("https://"):
        return ""
    parts = urllib.parse.urlsplit(url)
    host = (parts.hostname or "").lower()
    if not host or _skipped_host(host):
        return ""
    return f"https://{host}"


def _skipped_host(host):
    host = (host or "").lower()
    return any(host == s or host.endswith("." + s) for s in SKIP_HOSTS)


def _email_ok(addr):
    addr = (addr or "").strip()
    if not addr or EMAIL_SKIP.search(addr):
        return False
    host = addr.split("@")[-1].lower()
    return not _skipped_host(host)


def _email_rank(addr):
    local = addr.split("@")[0].lower()
    for i, word in enumerate(PREFERRED_EMAIL):
        if word in local:
            return i
    return len(PREFERRED_EMAIL)


def extract_contact(text, url=""):
    """Phone, email and website that actually appear in this page."""
    text = text or ""
    emails = sorted({m.group(0) for m in EMAIL.finditer(text) if _email_ok(m.group(0))}, key=_email_rank)
    phones = []
    seen = set()
    for match in PHONE.finditer(text):
        formatted = format_phone(match.group(0))
        if formatted and formatted not in seen:
            seen.add(formatted)
            phones.append(formatted)
    website = clean_website(url)
    if not website:
        for match in PAGE_URL.finditer(text):
            website = clean_website(match.group(0))
            if website:
                break
    mailing = ""
    labeled = MAILING_LABELED.search(text)
    if labeled:
        mailing = re.sub(r"\s+", " ", labeled.group(1)).strip(" .,;:")
    return {
        "mailing_address": mailing,
        "email": emails[0] if emails else "",
        "phone": phones[0] if phones else "",
        "website": website,
    }


def extract_from_results(name, results):
    """Use already-fetched public-record or web text that names this company."""
    bits = tokens(name)
    found = {"mailing_address": "", "email": "", "phone": "", "website": ""}
    for raw in (results or {}).values():
        blob = str(raw)
        if bits and not any(bit in blob.lower() for bit in bits):
            continue
        got = extract_contact(blob)
        found = merge_contact(found, got)
        if found["email"] and found["phone"]:
            break
    return found


def merge_contact(base, extra):
    out = dict(base or {})
    for key in ("mailing_address", "email", "phone", "website"):
        if not out.get(key) and extra.get(key):
            out[key] = extra[key]
    return out


def _needs_lookup(party, found):
    return not ((party.get("email") or found.get("email")) and (party.get("phone") or found.get("phone")))


def lookup_company(sources, name, geo=None):
    city = str((geo or {}).get("city") or "").strip()
    state = str((geo or {}).get("state") or "").strip()
    query = " ".join(part for part in (name, city, state, "official contact phone email leasing office") if part)
    web = getattr(sources, "web", None)
    if web is None or not hasattr(web, "search"):
        return {}
    try:
        pages = web.search(query, 2) or []
    except Exception:
        return {}
    if not pages:
        return {}
    sources.keep(f"company contact {name}", pages)
    ranked = sorted(pages, key=lambda p: _official_score(p.get("url") or "", name), reverse=True)
    found = {"mailing_address": "", "email": "", "phone": "", "website": ""}
    for page in ranked:
        blob = " ".join(str(page.get(k) or "") for k in ("title", "url", "text"))
        got = extract_contact(blob, page.get("url") or "")
        if _official_score(page.get("url") or "", name) == 0:
            got["website"] = ""
        found = merge_contact(found, got)
        if found["email"] and found["phone"] and found["website"]:
            break
    return {k: v for k, v in found.items() if v}


def _official_score(url, name):
    host = (urllib.parse.urlsplit(url).hostname or "").lower()
    if not host or _skipped_host(host):
        return 0
    score = 1
    for bit in tokens(name):
        if bit in host:
            score += 3
    return score


def apply_found(party, found):
    if not found:
        return party
    if not party.get("mailing_address") and found.get("mailing_address"):
        party["mailing_address"] = found["mailing_address"]
    if not party.get("email") and found.get("email"):
        party["email"] = found["email"]
    if not party.get("phone") and found.get("phone"):
        party["phone"] = found["phone"]
    if not party.get("website") and found.get("website"):
        party["website"] = found["website"]
    if not party.get("role") and party.get("name"):
        party["role"] = default_role(party["name"])
    return party


def fill_company_contact(report, sources, geo=None):
    """Add official company phone, email and website when the landlord is a company."""
    results = getattr(sources, "results", {}) or {}
    for key in ("owner", "manager"):
        party = report.get(key)
        if not isinstance(party, dict):
            continue
        name = str(party.get("name") or "").strip()
        if not is_corporate(name):
            continue
        if not party.get("role"):
            party["role"] = default_role(name)
        found = extract_from_results(name, results)
        if _needs_lookup(party, found):
            found = merge_contact(found, lookup_company(sources, name, geo))
        apply_found(party, found)
    return report
