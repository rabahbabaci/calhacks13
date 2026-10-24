# Stack findings: data layer (Rabah)

Running log for the SpacetimeDB module and the health corpus ingest. Updated with every commit from this workstream.

## Status (Sat Oct 24, 9:52 AM PT)

| Item | State |
|---|---|
| SpacetimeDB module `home` | Live on Rabah's laptop, schema matches docs/contracts.md section 4 |
| data/stdb.py (Python client) | Working: reads, writes, error handling tested |
| urls.csv validation | Done: 16 of 17 valid, 8 hazards covered (no gas_leak) |
| Corpus ingest (Apify) | Done: 15 sources, 110 chunks, all 8 gate hazards |
| Persistence after restart | PASS: server stopped and restarted, identical row counts and hashes |

Gate by 11:30 AM: corpus loaded for every hazard except gas_leak, persistence confirmed, stdb.py working, all pushed.

**Gate: PASS at 9:50 AM.**

## How to use it

```python
import json, sys
sys.path.insert(0, "data")
from stdb import sql, call_reducer, StdbError

rows = sql("SELECT * FROM health_chunk WHERE hazard = 'dampness_mold'")
call_reducer("save_assessment", ["case_001", "dampness_mold", json.dumps(payload)])
```

- Server: http://127.0.0.1:3000, database `home`. Override with env vars STDB_URL and STDB_DB.
- No token needed locally. SpacetimeDB 2.0 allows anonymous HTTP access to public tables and reducers. If STDB_TOKEN is set it gets sent as a Bearer token.
- Reducer arguments are positional, in contract order.
- Any failure raises StdbError. Reducer validation failures come back as HTTP 530 with the error message in the body.
- CLI equivalent: `spacetime sql --server local --no-config home "SELECT ..."`

## Module behavior beyond the contract

Same tables, columns and reducer signatures as the contract. Added on top:

- add_source upserts by url. add_chunk upserts by (source_url, ordinal). Rerunning ingest does not duplicate rows.
- Reducers reject: unknown hazard tag, unknown publisher, non-https url, a chunk whose source does not exist or has a different hazard, empty chunk text, payload_json that is not valid JSON, empty case_id.
- Indexes: health_chunk.hazard, health_chunk (source_url, ordinal), health_assessment.case_id.

## Open question for team sync

The server only listens on 127.0.0.1 on Rabah's laptop, so other laptops can't reach it. Options: listen on the LAN (depends on venue Wi-Fi) or publish `home` to SpacetimeDB maincloud. Needs a decision before other agents integrate.

## Log

### Step 1: module (8:50 to 9:00)
Worked:
- CLI install script, version 2.13.0. Build and publish take seconds.
- `spacetime init --lang typescript --server-only` ships `.cursor/rules/typescript-server.mdc`, the most accurate API reference for the installed version. Better than the web docs.

Broke or surprised us:
- `spacetime init` drops files at the project root (spacetime.json, CLAUDE.md, AGENTS.md, .cursor/, .github/). We ran it in /tmp and copied only spacetimedb/ into the repo.
- After install the default server is maincloud. Ran `spacetime server set-default local` so nothing gets published to the cloud by accident.
- A spacetime.json in the current directory silently overrides CLI arguments. Use `--no-config`.
- zsh does not split a variable like `$FLAGS` into separate arguments. Write flags out.
- `spacetime call` parses JSON-looking arguments as JSON. To pass a JSON string, quote it twice: `'"{\"ok\":true}"'`. The HTTP API is not affected.
- Reducer errors return HTTP 530, not 4xx.

### Step 2: data/stdb.py (9:00 to 9:12)
- Standard library only (urllib), no dependencies.
- Smoke rows from testing use https://test.invalid/smoke and get wiped before the real ingest.

### Step 4: Apify ingest (9:17 to 9:47)
- Apify free plan. Token in the root .env (gitignored). apify-client 3.3.0 in data/.venv.
- apify-client v3 returns Pydantic models, so it is `run.default_dataset_id`, not `run['defaultDatasetId']`. Dataset items are still dicts.
- Actor apify/website-content-crawler with crawlerType cheerio (the default is a headless browser, slower and more expensive), maxCrawlDepth 0, saveMarkdown false. 16 pages in about 20 seconds.
- Raw crawl output is committed as data/crawl_raw.jsonl. `ingest.py --from-cache` rebuilds the database with no Apify call, so a Wi-Fi outage or a missing token does not block anyone.
- Cleaning: drops CDC review footers, date-only lines, "On this page" tables of contents, and language links. Chunks split by paragraph, packed to about 1,000 characters (a few reach about 1,090 when a short tail merges). Then chunks under 150 characters or with no keyword for the row's hazard are dropped, so multi-topic pages only contribute their on-topic parts. Example: the asthma triggers page kept 3 of 13 chunks for cockroaches.
- When urls.csv says last_updated is unknown, ingest fills it from the page's "Last updated on" text if present.
- Broke: the CDC electrical hazards page returns only its footer through the crawler (content probably sits in collapsed sections), so it is skipped.
- Broke: the CDC carbon monoxide page first kept 1 of 5 chunks because it writes "CO" after the first line. Added `\bco\b` as a keyword.
- cdph.ca.gov sends an incomplete TLS certificate chain. Python fails verification, so the validator retries that read-only fetch without verification and flags the row. Apify fetched it fine.
- Perplexity: suggested 8 rows, kept 1 (CDPH Air Quality Section "Mold and Dampness", California-specific, covers the 2016 substandard housing rule and renter steps). Dropped: 2 EPA pest pages (only 1 or 2 cockroach mentions), CDPH EHIB mold page (video list only), CDPH lead page (one-paragraph landing page), 2 gas_leak pages about tornadoes and tsunamis (wrong scenario, and gas_leak is urgent-only per contracts.md section 5), and 1 row with no URL.
- Wiped the smoke test rows with `spacetime publish --delete-data=always` before loading.

Result in the database (sources / chunks):

| Hazard | Sources | Chunks |
|---|---|---|
| carbon_monoxide | 2 | 9 |
| cockroaches | 1 | 3 |
| dampness_mold | 3 | 20 |
| exposed_wiring | 1 | 2 |
| inadequate_heat | 2 | 15 |
| lead_paint | 2 | 27 |
| rodents | 2 | 14 |
| water_leak | 2 | 20 |
| gas_leak | 0 | 0 (urgent-only, fixed message) |

Thin spots: cockroaches and exposed_wiring each rest on one multi-topic page. Fix later with a dedicated cockroach page and an indoor electrical safety page (or a browser crawl of the CDC electrical page). Only one California source so far.

### Step 5: persistence (9:48 to 9:51)
- `python3 data/fingerprint.py` prints row counts plus a hash of every row for all three tables.
- Fingerprinted, stopped the server with Ctrl+C (ping: connection refused), ran `spacetime start`, fingerprinted again. Identical: 15 sources, 110 chunks, 0 assessments, same hashes.
- Data lives in ~/.local/share/spacetime/data (15 MB). Caveat: health_assessment was empty during the test. Same database and storage, but only sources and chunks were proven with real rows.

## Next (after the gate)
- Team sync decision: how other laptops reach the database (LAN listen or publish to maincloud). See the open question above.
- Add a dedicated cockroach source and an indoor electrical safety source. Each of those hazards rests on one multi-topic page right now.
- More California sources (CDPH, HUD Healthy Homes) for the Oakland + California pack.

## Limits, cost, time

- Time: about 1 hour (8:50 to 9:52), all six steps.
- `spacetime call`, `sql` and `describe` print an UNSTABLE warning on every run. They work, but the CLI output format could change.
- There is no delete reducer (none in the contract). The only way to wipe is `spacetime publish --delete-data=always`, which clears all three tables, including health_assessment. Do not run it once other agents are saving assessments.
- The database runs on one laptop. If that laptop sleeps or the server tab closes, everyone loses reads until it restarts. The data survives (step 5), and `ingest.py --from-cache` rebuilds it anywhere in about 10 seconds.
- Apify: 2 crawl runs of 15 to 16 pages, $0.0048 and $0.0046, free plan. Reloads from cache cost nothing.
- SpacetimeDB: $0, local.

### Step 3: validate urls.csv (9:13 to 9:17)
- Moved urls.csv to data/urls.csv (repo root is outside this workstream's folders).
- data/validate_urls.py checks: required fields, hazard tag in contract list, publisher allowed, https, host matches publisher domain (cdc.gov, cdph.ca.gov, epa.gov, hud.gov, who.int), no duplicates, HTTP 200 without leaving the domain, and a relevance check (at least 3 hazard keyword hits in the page text).
- Result: 15 of 16 valid. Skipped CDC "Controlling Asthma" tagged cockroaches: only 2 cockroach mentions, real page but wrong topic. Passing rows go to data/urls_valid.csv.
- Coverage: every hazard except gas_leak (expected, urgent-only). Cockroaches has just one source (EPA asthma triggers). No CDPH, HUD or WHO sources yet, so nothing California-specific.
- Gov sites can block Python's default user agent, so the validator sends a browser user agent.
