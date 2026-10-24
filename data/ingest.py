"""Crawl validated health sources with Apify Website Content Crawler and load them into SpacetimeDB.

Usage (repo root):
  data/.venv/bin/python data/ingest.py --dry-run                # crawl all valid rows, cache raw output, show chunks, write nothing
  data/.venv/bin/python data/ingest.py --from-cache --dry-run   # re-chunk the cache, write nothing, no Apify call
  data/.venv/bin/python data/ingest.py --from-cache             # load data/crawl_raw.jsonl into SpacetimeDB
  data/.venv/bin/python data/ingest.py                          # crawl and write in one go
Options: --limit N (first N rows only; cache is not written)

Cleaning: drops site boilerplate (review footers, date-only lines, "On this page" tables of contents,
language links) and drops chunks with no keyword hit for the row's hazard, so multi-topic pages only
contribute their on-topic parts.
"""
import argparse
import csv
import json
import re
import sys
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

from stdb import StdbError, call_reducer, sql
from validate_urls import KEYWORDS

ROOT = Path(__file__).resolve().parent.parent
VALID_CSV = ROOT / "data" / "urls_valid.csv"
CACHE = ROOT / "data" / "crawl_raw.jsonl"
ACTOR = "apify/website-content-crawler"
MAX_CHARS = 1000
MIN_TAIL = 200
MIN_CHUNK = 150
GATE_HAZARDS = set(KEYWORDS) - {"gas_leak"}

MONTH = r"(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?"
DROP_FULL = [re.compile(p, re.I) for p in [
    r"on this page:?",
    r"información disponible en español",
    r"available in other languages",
    r"this guide is also available as a downloadable pdf and en español",
    r"sources and page info( print share)?",
    r"print share",
    r"content source:?",
    r"about this page",
    r"for everyone",
    r"national center for environmental health",
    r"(published|updated|reviewed):?\s.{0,40}",
    r"last (updated|reviewed)( on)?:?\s.{0,40}",
    MONTH + r"\s\d{1,2},\s\d{4}",
]]
DROP_CONTAINS = [
    "the information on this page was last reviewed",
    "or other modifications to page content",
]
LAST_UPDATED_RE = re.compile(r"last updated on (" + MONTH + r"\s\d{1,2},\s\d{4})", re.I)


def load_token():
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        if line.startswith("APIFY_TOKEN="):
            return line.split("=", 1)[1].strip()
    sys.exit("APIFY_TOKEN not found in .env")


def norm(url):
    return (url or "").strip().rstrip("/").lower()


def crawl(urls):
    from apify_client import ApifyClient

    client = ApifyClient(token=load_token())
    run_input = {
        "startUrls": [{"url": u} for u in urls],
        "crawlerType": "cheerio",
        "maxCrawlDepth": 0,
        "maxCrawlPages": len(urls),
        "saveMarkdown": False,
    }
    started = time.time()
    run = client.actor(ACTOR).call(run_input=run_input)
    items = list(client.dataset(run.default_dataset_id).iterate_items())
    usd = getattr(run, "usage_total_usd", None)
    print(f"apify run {run.id}: status {run.status}, {len(items)} items, "
          f"{time.time() - started:.0f}s, usage_total_usd={usd}")
    return items


def is_boilerplate(p):
    low = p.lower()
    if any(s in low for s in DROP_CONTAINS):
        return True
    return any(rx.fullmatch(p) for rx in DROP_FULL)


def paragraphs(text):
    text = text.replace("\r\n", "\n")
    parts = re.split(r"\n\s*\n", text)
    if len(parts) <= 1:
        parts = text.split("\n")
    out, in_toc = [], False
    for p in parts:
        p = re.sub(r"\s+", " ", p).strip()
        if not p:
            continue
        if re.fullmatch(r"on this page:?", p, re.I):
            in_toc = True
            continue
        if in_toc:
            if len(p) < 80 and not p.endswith("."):
                continue
            in_toc = False
        if is_boilerplate(p):
            continue
        out.append(p)
    return out


def split_long(p):
    if len(p) <= MAX_CHARS:
        return [p]
    out, cur = [], ""
    for s in re.split(r"(?<=[.!?])\s+", p):
        while len(s) > MAX_CHARS:
            if cur:
                out.append(cur)
                cur = ""
            out.append(s[:MAX_CHARS])
            s = s[MAX_CHARS:]
        if cur and len(cur) + 1 + len(s) > MAX_CHARS:
            out.append(cur)
            cur = s
        else:
            cur = f"{cur} {s}".strip()
    if cur:
        out.append(cur)
    return out


def chunk(text):
    pieces = [s for p in paragraphs(text) for s in split_long(p)]
    chunks, cur = [], ""
    for p in pieces:
        if cur and len(cur) + 2 + len(p) > MAX_CHARS:
            chunks.append(cur)
            cur = p
        else:
            cur = f"{cur}\n\n{p}" if cur else p
    if cur:
        if chunks and len(cur) < MIN_TAIL:
            chunks[-1] += "\n\n" + cur
        else:
            chunks.append(cur)
    return chunks


def on_topic(c, hazard):
    low = c.lower()
    return any(re.search(p, low) for p in KEYWORDS[hazard])


def page_last_updated(text):
    m = LAST_UPDATED_RE.search(text or "")
    if not m:
        return None
    raw = re.sub(r"\s+", " ", m.group(1)).replace("Sept.", "Sep.").replace("Sept ", "Sep ")
    for fmt in ("%B %d, %Y", "%b. %d, %Y", "%b %d, %Y"):
        try:
            return datetime.strptime(raw, fmt).date().isoformat()
        except ValueError:
            pass
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--from-cache", action="store_true")
    args = ap.parse_args()

    with open(VALID_CSV, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    if args.limit:
        rows = rows[: args.limit]

    if args.from_cache:
        items = [json.loads(l) for l in CACHE.read_text(encoding="utf-8").splitlines() if l.strip()]
        print(f"loaded {len(items)} items from {CACHE.relative_to(ROOT)}")
    else:
        items = crawl([r["url"] for r in rows])
        if not args.limit:
            CACHE.write_text("".join(json.dumps(i, ensure_ascii=False) + "\n" for i in items), encoding="utf-8")
            print(f"cached raw output to {CACHE.relative_to(ROOT)}")

    by_url = {}
    for it in items:
        for key in (it.get("url"), (it.get("crawl") or {}).get("loadedUrl")):
            if key:
                by_url[norm(key)] = it

    failed, skipped, written = [], [], Counter()
    for r in rows:
        it = by_url.get(norm(r["url"]))
        if not it:
            failed.append((r["url"], "no crawl result"))
            continue
        status = (it.get("crawl") or {}).get("httpStatusCode")
        if status not in (None, 200):
            failed.append((r["url"], f"HTTP {status}"))
            continue
        text = it.get("text") or ""
        all_chunks = chunk(text)
        chunks = [c for c in all_chunks if len(c) >= MIN_CHUNK and on_topic(c, r["hazard"])]
        if not chunks:
            skipped.append((r["url"], f"no on-topic content after cleaning ({len(all_chunks)} chunks before topic filter)"))
            continue
        last_updated = r["last_updated"]
        if last_updated.lower() == "unknown":
            last_updated = page_last_updated(text) or "unknown"
        sizes = [len(c) for c in chunks]
        print(f"{r['hazard']:<16} kept {len(chunks):>2}/{len(all_chunks):<2} avg {sum(sizes) // len(sizes):>4} "
              f"max {max(sizes):>4} updated {last_updated}  {r['url']}")
        if args.dry_run:
            print("    first:", chunks[0][:160].replace("\n", " | "))
            print("    last: ", chunks[-1][-120:].replace("\n", " | "))
            continue
        try:
            call_reducer("add_source", [r["url"], r["title"], r["publisher"], r["hazard"], last_updated])
            for i, c in enumerate(chunks):
                call_reducer("add_chunk", [r["url"], r["hazard"], i, c])
            written[r["hazard"]] += len(chunks)
        except StdbError as e:
            failed.append((r["url"], str(e)))

    for label, group in (("SKIPPED", skipped), ("FAILED", failed)):
        if group:
            print(f"\n{label}:")
            for url, why in group:
                print(f"  {url}\n    -> {why}")

    if args.dry_run:
        print(f"\ndry run: {len(rows) - len(failed) - len(skipped)} of {len(rows)} rows would load, nothing written")
        return 1 if failed else 0

    db = Counter(x["hazard"] for x in sql("SELECT hazard FROM health_chunk"))
    srcs = Counter(x["hazard"] for x in sql("SELECT hazard FROM health_source"))
    print("\nin database (sources / chunks):")
    for h in sorted(GATE_HAZARDS | set(db)):
        print(f"  {h:<16} {srcs[h]:>2} / {db[h]:>4}")
    missing = sorted(h for h in GATE_HAZARDS if db[h] == 0)
    print("GATE:", "PASS, every hazard except gas_leak has chunks" if not missing else f"FAIL, no chunks for {missing}")
    return 1 if failed or missing else 0


if __name__ == "__main__":
    sys.exit(main())
