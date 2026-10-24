# Stack findings: data layer (Rabah)

Running log for the SpacetimeDB module and the health corpus ingest. Updated with every commit from this workstream.

## Status (Sat Oct 24, 9:12 AM PT)

| Item | State |
|---|---|
| SpacetimeDB module `home` | Live on Rabah's laptop, schema matches docs/contracts.md section 4 |
| data/stdb.py (Python client) | Working: reads, writes, error handling tested |
| urls.csv validation | Done: 15 of 16 valid, 8 hazards covered (no gas_leak) |
| Corpus ingest (Apify) | Not started |
| Persistence after restart | Not tested |

Gate by 11:30 AM: corpus loaded for every hazard except gas_leak, persistence confirmed, stdb.py working, all pushed.

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

## Limits, cost, time

- Time so far: about 25 minutes.
- Cost so far: $0, everything is local.

### Step 3: validate urls.csv (9:13 to 9:17)
- Moved urls.csv to data/urls.csv (repo root is outside this workstream's folders).
- data/validate_urls.py checks: required fields, hazard tag in contract list, publisher allowed, https, host matches publisher domain (cdc.gov, cdph.ca.gov, epa.gov, hud.gov, who.int), no duplicates, HTTP 200 without leaving the domain, and a relevance check (at least 3 hazard keyword hits in the page text).
- Result: 15 of 16 valid. Skipped CDC "Controlling Asthma" tagged cockroaches: only 2 cockroach mentions, real page but wrong topic. Passing rows go to data/urls_valid.csv.
- Coverage: every hazard except gas_leak (expected, urgent-only). Cockroaches has just one source (EPA asthma triggers). No CDPH, HUD or WHO sources yet, so nothing California-specific.
- Gov sites can block Python's default user agent, so the validator sends a browser user agent.
