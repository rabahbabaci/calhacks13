"""Validate urls.csv before ingest. Writes passing rows to data/urls_valid.csv.

Checks per row: required fields, hazard tag in contract list, publisher in allowed list,
https URL whose host matches the publisher's domain, no duplicate URLs, page fetches with
HTTP 200 and stays on the publisher's domain after redirects, and the page text mentions
the hazard (at least MIN_HITS keyword matches).
"""
import csv
import re
import ssl
import sys
import time
import urllib.error
import urllib.request
from urllib.parse import urlparse

SRC = "data/urls.csv"
OUT = "data/urls_valid.csv"
MIN_HITS = 3
FIELDS = ["url", "title", "publisher", "hazard", "last_updated"]

DOMAINS = {
    "CDC": "cdc.gov",
    "CDPH": "cdph.ca.gov",
    "EPA": "epa.gov",
    "HUD": "hud.gov",
    "WHO": "who.int",
}

KEYWORDS = {
    "dampness_mold": [r"\bmold", r"\bdamp"],
    "water_leak": [r"\bleak", r"\bmoisture\b", r"\bwater\b"],
    "rodents": [r"\brodents?\b", r"\bmice\b", r"\brats?\b"],
    "cockroaches": [r"\bcockroach"],
    "exposed_wiring": [r"\belectric", r"\bwir(e|es|ing)\b"],
    "lead_paint": [r"\blead\b", r"\bpaint"],
    "inadequate_heat": [r"\bhypothermia\b", r"\bcold\b", r"\bheat(ing|er|ers)?\b"],
    "carbon_monoxide": [r"carbon monoxide", r"\bco\b"],
    "gas_leak": [r"gas leak", r"natural gas"],
}

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15"


def host_matches(url, domain):
    host = (urlparse(url).hostname or "").lower()
    return host == domain or host.endswith("." + domain)


def page_text(html):
    html = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", html)
    return re.sub(r"<[^>]+>", " ", html).lower()


TLS_NOTES = {}


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/html"})
    try:
        resp = urllib.request.urlopen(req, timeout=20)
    except urllib.error.URLError as e:
        if "CERTIFICATE_VERIFY_FAILED" not in str(e.reason):
            raise
        # Some servers (cdph.ca.gov) send an incomplete certificate chain. Browsers recover, Python does not.
        # Read-only fetch of a public page, so retry unverified and flag it.
        resp = urllib.request.urlopen(req, timeout=20, context=ssl._create_unverified_context())
        TLS_NOTES[url] = "TLS chain incomplete, fetched without verification"
    with resp:
        charset = resp.headers.get_content_charset() or "utf-8"
        return resp.status, resp.geturl(), resp.read(5_000_000).decode(charset, "replace")


def check(row, seen):
    for f in FIELDS:
        if not (row.get(f) or "").strip():
            return f"missing {f}", None
    url, publisher, hazard = row["url"].strip(), row["publisher"].strip(), row["hazard"].strip()
    if hazard not in KEYWORDS:
        return f"hazard '{hazard}' not in contract list", None
    if publisher not in DOMAINS:
        return f"publisher '{publisher}' not allowed", None
    if not url.startswith("https://"):
        return "url is not https", None
    if not host_matches(url, DOMAINS[publisher]):
        return f"host {urlparse(url).hostname} does not match {publisher} ({DOMAINS[publisher]})", None
    if url in seen:
        return "duplicate url", None
    seen.add(url)
    try:
        status, final_url, html = fetch(url)
    except urllib.error.HTTPError as e:
        return f"HTTP {e.code}", None
    except Exception as e:
        return f"fetch failed: {type(e).__name__}: {e}", None
    if status != 200:
        return f"HTTP {status}", None
    if not host_matches(final_url, DOMAINS[publisher]):
        return f"redirected off-domain to {final_url}", None
    text = page_text(html)
    hits = sum(len(re.findall(p, text)) for p in KEYWORDS[hazard])
    if hits < MIN_HITS:
        return f"off-topic: only {hits} '{hazard}' keyword hits", hits
    return None, hits


def main():
    with open(SRC, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    seen, valid, skipped = set(), [], []
    for i, row in enumerate(rows, start=2):
        reason, hits = check(row, seen)
        label = f"line {i:>2} {row.get('hazard', ''):<16} {row.get('url', '')}"
        if reason:
            skipped.append((label, reason))
            print(f"SKIP {label}\n     -> {reason}")
        else:
            valid.append({k: row[k].strip() for k in FIELDS})
            warn = "  (last_updated unknown)" if row["last_updated"].strip().lower() == "unknown" else ""
            if row["url"].strip() in TLS_NOTES:
                warn += f"  ({TLS_NOTES[row['url'].strip()]})"
            print(f"OK   {label}  [{hits} hits]{warn}")
        time.sleep(0.5)
    with open(OUT, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(valid)
    covered = sorted({r["hazard"] for r in valid})
    missing = sorted(set(KEYWORDS) - set(covered))
    print(f"\n{len(valid)} valid, {len(skipped)} skipped -> {OUT}")
    print("hazards covered:", ", ".join(covered))
    print("hazards with no valid source:", ", ".join(missing) or "none")
    return 0


if __name__ == "__main__":
    sys.exit(main())
