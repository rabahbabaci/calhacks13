# Stack findings: data layer (Rabah)

Running log for the SpacetimeDB module and the health corpus ingest. Updated with every commit from this workstream.

## Status (Sat Oct 24, 6:35 PM PT)

| Item | State |
|---|---|
| Shared team db (Maincloud) | Live: https://maincloud.spacetimedb.com, database `home`, 15 sources / 110 chunks |
| Live core tables (section 6) | Live: case_file, hazard, agent_run + 6 reducers, 11/11 tests, health agent records runs |
| Legal tables (section 7) | Live: legal_source + add_legal_source (Ansh, PR #1) |
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

- Default is local http://127.0.0.1:3000. The team uses Maincloud, see How to connect.
- No token needed locally. SpacetimeDB 2.0 allows anonymous HTTP access to public tables and reducers. If STDB_TOKEN is set it gets sent as a Bearer token.
- Reducer arguments are positional, in contract order.
- Any failure raises StdbError. Reducer validation failures come back as HTTP 530 with the error message in the body.
- CLI equivalent: `spacetime sql --server local --no-config home "SELECT ..."`

## Module behavior beyond the contract

Same tables, columns and reducer signatures as the contract. Added on top:

- add_source upserts by url. add_chunk upserts by (source_url, ordinal). Rerunning ingest does not duplicate rows.
- Reducers reject: unknown hazard tag, unknown publisher, non-https url, a chunk whose source does not exist or has a different hazard, empty chunk text, payload_json that is not valid JSON, empty case_id.
- Indexes: health_chunk.hazard, health_chunk (source_url, ordinal), health_assessment.case_id.

## How to connect (shared team db)

- URL: https://maincloud.spacetimedb.com
- Database: `home`
- No token needed. Requests without an Authorization header get an anonymous identity that can run SQL on public tables and call reducers.

### Python

1. Add this line to your own `.env` in the repo root. Check the file ends with a newline first, and never commit `.env`.

```
STDB_URL=https://maincloud.spacetimedb.com
```

2. Use data/stdb.py:

```python
import json, sys
sys.path.insert(0, "data")
from stdb import sql, call_reducer, StdbError

rows = sql("SELECT * FROM health_chunk WHERE hazard = 'dampness_mold'")
call_reducer("save_assessment", ["case_001", "dampness_mold", json.dumps(payload)])
```

stdb.py reads STDB_URL, STDB_DB and STDB_TOKEN from real environment variables first, then from STDB_* lines in `.env`. With neither, it uses local http://127.0.0.1:3000.

### curl

Read. The body is the raw SQL string:

```
curl -s -X POST https://maincloud.spacetimedb.com/v1/database/home/sql -H 'Content-Type: text/plain' --data "SELECT url, title FROM health_source WHERE hazard = 'cockroaches'"
```

The response is a JSON array with one object per statement: `schema.elements` holds the column names and `rows` holds arrays in column order.

Write. The body is a JSON array of reducer arguments in contract order. This writes a real row and there is no delete, so only run it with a case_id you mean:

```
curl -s -w '\nHTTP %{http_code}\n' -X POST https://maincloud.spacetimedb.com/v1/database/home/call/save_assessment -H 'Content-Type: application/json' --data '["case_001","dampness_mold","{\"hazard\":\"dampness_mold\"}"]'
```

HTTP 200 means committed. HTTP 530 means the reducer rejected it, with the reason in the body.

### Live core: cases, hazards, agent runs (contracts.md section 6)

Reducers (all positional JSON arrays, in this order):
- `create_case(case_id, address, payload_json)`: upsert
- `add_hazard(case_id, hazard, location, confidence, evidence_ids_json)`: case must exist, tag must be valid, confidence 0 to 1, evidence_ids_json a JSON array
- `confirm_hazard(id, confirmed)`: the tenant's yes/no
- `start_run(case_id, agent, hazard)`: creates the row or resets it to running (payload and error cleared)
- `finish_run(case_id, agent, hazard, payload_json)`: running to done
- `fail_run(case_id, agent, hazard, error)`: running to failed
- Agents: hazard, health, property, legal, packet, action. Use `""` as hazard for case-level agents.
- Also live: `add_legal_source(url, cite, kind, jurisdiction, excerpt, verified)` (Ansh, section 7), plus the health reducers above.

Start and finish a run. These use `case_example` so a copy-paste never resets a real case:

```
curl -s -w '\nHTTP %{http_code}\n' -X POST https://maincloud.spacetimedb.com/v1/database/home/call/create_case -H 'Content-Type: application/json' --data '["case_example","123 Example St, Oakland, CA 94612","{\"case_id\":\"case_example\"}"]'
```

```
curl -s -w '\nHTTP %{http_code}\n' -X POST https://maincloud.spacetimedb.com/v1/database/home/call/start_run -H 'Content-Type: application/json' --data '["case_example","property",""]'
```

```
curl -s -w '\nHTTP %{http_code}\n' -X POST https://maincloud.spacetimedb.com/v1/database/home/call/finish_run -H 'Content-Type: application/json' --data '["case_example","property","","{\"owner_records\":[]}"]'
```

From Python: `call_reducer("start_run", [case_id, "property", ""])`, then `call_reducer("finish_run", [case_id, "property", "", json.dumps(output)])`, or `fail_run` with the error string.

UI subscriptions:

```
SELECT * FROM hazard WHERE case_id = 'case_demo_001'
```

```
SELECT * FROM agent_run WHERE case_id = 'case_demo_001'
```

Same queries work as one-off reads over `/sql`.

Known gaps:
- `pending` is a valid status, but no reducer writes it. A run row only appears at `start_run`.
- Hazard rows only appear when someone calls `add_hazard` (the hazard detection agent, #1). The health agent reads hazards from the case input, so case_demo_001 has agent_run rows but no hazard rows yet.

Test everything end to end: `bash data/test_core.sh https://maincloud.spacetimedb.com home` (11 checks, writes to `case_core_test` only).

### Rules

- Writes go through reducers only (add_source, add_chunk, save_assessment).
- New tables or reducers: send the shape to Rabah. One module, one owner, one publish.
- Never republish with `--delete-data`. It wipes every table, assessments included, with no undo.
- No tokens in the repo. STDB_TOKEN is only for Rabah's owner operations (publish, logs).
- Demo data only. Every table is public (see Production items).
- Timestamps come back as `[microseconds since epoch]` (contracts.md section 4).

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

### Step 7: move to Maincloud (5:47 to 6:00 PM)
- `spacetime logout` then `spacetime login` (GitHub). The morning's local publish had auto-created a server-only identity, so the CLI was not linked to the web account.
- `GET /v1/database/home` returned 404, so published as `home` with `--yes=remote` and no `--delete-data`.
- Rebuilt with `STDB_URL=https://maincloud.spacetimedb.com ingest.py --from-cache`: 15 sources / 110 chunks, same per-hazard counts as local.
- curl with no token: SQL read OK, reducer upsert HTTP 200, bad hazard HTTP 530 with the module's error, counts unchanged.
- agents/health/evals.py against Maincloud: 9/9 pass. The evals read the corpus but save nothing (`save=False`).
- Gotcha: appending to `.env` with printf glued text onto ANTHROPIC_API_KEY because `.env` had no trailing newline. Repaired and verified. Check the trailing newline before appending.
- Gotcha: zsh globs an unquoted `--include=*.py`. Quote it.
- After the re-login the CLI may no longer own the local `home`. Local reads still work. To republish locally, use a new local name and `--from-cache`.

### Step 8: live core tables + legal merge (6:11 to 6:35 PM)
- Section 6 was not on main at first (the planning chat drafted it but never committed). Committed it as d730d66 before writing code.
- Added case_file, hazard, agent_run and the 6 reducers. Tested on a fresh local db `home-dev` first, then published to Maincloud without `--delete-data`. The migration plan showed only created tables; corpus stayed at 15 / 110.
- data/test_core.sh: 11 curl checks (happy path, reset and fail lifecycle, 4 rejections). 11/11 on local and on Maincloud.
- Ansh's PR #1 (legal agent) conflicted on the module and contracts. He merged main and redesigned to use agent_run for results, keeping only `legal_source`. Tested his branch in a temporary worktree (30/30 unit tests, typecheck, section 6 regression 11/11, 4 add_legal_source checks), merged, published.
- Health agent: start_run / finish_run / fail_run per assessed hazard, creates case_file if missing, still saves health_assessment. Evals 9/9 with save=False (no writes). Real run on case_demo_001: 3 hazards, 3 runs done, 0 dropped.
- Only the module owner can publish `home`, so a teammate's branch can never change Maincloud by accident.

## Production items (not this weekend)
- Open writes: every reducer accepts anonymous callers, so anyone with the URL can add sources, chunks or assessments.
- Open reads: health_assessment is public, so tenant case data would be readable by anyone with the URL.
- Fix: private assessment table, auth on the HTTP API, reducers check `ctx.sender` against an allowlist, client visibility filters for per-tenant rows.

## Next (after the gate)
- Done: shared database on Maincloud (see How to connect).
- Add a dedicated cockroach source and an indoor electrical safety source. Each of those hazards rests on one multi-topic page right now.
- More California sources (CDPH, HUD Healthy Homes) for the Oakland + California pack.

## Limits, cost, time

- Time: about 1 hour (8:50 to 9:52), all six steps. Maincloud move: about 15 minutes (5:47 to 6:00 PM).
- `spacetime call`, `sql` and `describe` print an UNSTABLE warning on every run. They work, but the CLI output format could change.
- There is no delete reducer (none in the contract). The only way to wipe is `spacetime publish --delete-data=always`, which clears all three tables, including health_assessment. Do not run it once other agents are saving assessments.
- Local is now only a fallback. If Maincloud is unreachable, set `STDB_URL=http://127.0.0.1:3000` and run `ingest.py --from-cache` for a local copy in about 10 seconds.
- Apify: 2 crawl runs of 15 to 16 pages, $0.0048 and $0.0046, free plan. Reloads from cache cost nothing.
- SpacetimeDB: $0, local.

### Step 3: validate urls.csv (9:13 to 9:17)
- Moved urls.csv to data/urls.csv (repo root is outside this workstream's folders).
- data/validate_urls.py checks: required fields, hazard tag in contract list, publisher allowed, https, host matches publisher domain (cdc.gov, cdph.ca.gov, epa.gov, hud.gov, who.int), no duplicates, HTTP 200 without leaving the domain, and a relevance check (at least 3 hazard keyword hits in the page text).
- Result: 15 of 16 valid. Skipped CDC "Controlling Asthma" tagged cockroaches: only 2 cockroach mentions, real page but wrong topic. Passing rows go to data/urls_valid.csv.
- Coverage: every hazard except gas_leak (expected, urgent-only). Cockroaches has just one source (EPA asthma triggers). No CDPH, HUD or WHO sources yet, so nothing California-specific.
- Gov sites can block Python's default user agent, so the validator sends a browser user agent.

## Health agent (Rabah)

Agent #2, Medical & Environmental Health. Code in `agents/health/`. Built Sat 10:10 to 10:30 AM PT.

**Gate (12:30 PM): PASS at 10:30 AM.** 9/9 evals pass against the live corpus, one real assessment saved to `health_assessment` and read back with the full payload, everything pushed (see git log, commit "health: prompt fix and findings").

### How to use it

```python
import sys; sys.path.insert(0, "agents/health")
from agent import assess_case
result = assess_case(case, save=True)   # case = contracts.md section 3 object, optional "notes" string
```

- `result["assessments"]`: one section 5 object per assessed hazard. Urgent hazards have the same shape with empty claim lists.
- `result["not_assessed"]`: `[{"hazard", "reason"}]` for hazards skipped (confidence under 0.5 and not confirmed by tenant, or no corpus).
- `result["dropped"]`: every claim the validator removed, with the reason. Useful for debugging, do not show to tenants.
- `result["usage"]`: tokens, call count and seconds per hazard.
- CLI: `data/.venv/bin/python agents/health/agent.py agents/health/cases/mold_basic.json [--no-save]`
- Evals: `data/.venv/bin/python agents/health/evals.py [a b ...]`. Writes `agents/health/out/evals.json`. No rows saved.
- Needs `ANTHROPIC_API_KEY` in the root `.env` and the `anthropic` package (2.3.0) in `data/.venv`. SpacetimeDB must be up (`spacetime start`).

### Design

1. Urgent check first, no LLM. `exposed_wiring`, `carbon_monoxide`, `gas_leak` return a fixed message (leave, call 911, PG&E gas line 1-877-660-6789 from pge.com). 0.0s.
2. Confidence under 0.5 and not confirmed by tenant is skipped before any retrieval.
3. Tool-use loop on `claude-sonnet-5-5` with two tools: `search_sources(hazard)` returns every chunk for the hazard (corpus is 110 chunks, no vector DB needed), `save_assessment` receives the draft. Two API calls per hazard, every time.
4. Validator in code, after the model: a claim is dropped if its `chunk_ids` are missing, empty, or not in the retrieved set, or if it uses a banned term for the hazard (rodents: hantavirus). `sources` is rebuilt from the chunks actually cited, so a link can only come from a retrieved source. The reducer save happens only after validation.
5. Household flags go into the prompt as emphasis only. Case notes are passed but the prompt forbids mentioning the tenant, household or notes in any claim.

### What worked

- First real run: 19 claims, all cited, 0 dropped, 3 sources (CDC, EPA, CDPH) on dampness_mold. 13.4s, 9.8k in / 1.8k out.
- `health_assessment` persistence confirmed with a real row (id 1, case_health_001). Note for #5: `created_at` comes back from SQL as a one-element array of microseconds, e.g. `[1792862222185738]`.
- The code validator caught a real one: on rodents the model wrote "If you suspect hantavirus disease, see a physician..." cited to a real chunk. Dropped as a banned term; the general care-seeking line survived. Prompt rules alone would not have guaranteed this.
- Lead paint with children=true leads vulnerable_groups with children under 6, cited.
- Thin corpora behave: cockroaches gives 6 claims from 3 chunks, all cited to the same chunk.

### What broke, and the fix

- Eval b failed first: the model put its refusal inside a claim ("cannot say what is causing any symptom in your child") and cited chunk 8, which says nothing like that. Fix: prompt rule that claims never mention the tenant, household, children or notes, and never contain a refusal or disclaimer. Pass after.
- Eval h failed first: 11 claims from a 3-chunk corpus, all honestly cited to chunk 44, but padded (five one-line precautions from one paragraph). Fix: prompt rule capping output at 6 claims when fewer than 5 chunks come back. Pass after.
- `data/stdb.py` raises `urllib.error.URLError` when the server is down, not `StdbError`. The agent wraps both into `DataLayerDown`.
- SpacetimeDB SQL has no GROUP BY. Count in Python.
- `git pull --rebase` refuses with unstaged edits, and `git stash pop` restores modified files unstaged, so a commit after it can silently skip them (that happened to agent.py at 3e3fac7, fixed in the next commit). Use `git pull --rebase --autostash` instead, then add, commit, push.

### Cost, latency, limits

- Full eval run (6 LLM hazards, 10 API calls): 46.0k input tokens, 7.1k output tokens, about 9 to 13 seconds per LLM hazard, 0.0s for urgent and skipped. One hazard is roughly 8 to 12k input tokens because the whole hazard corpus goes into context; lead_paint (27 chunks) is the largest at 12.5k. At Sonnet list pricing that is in the range of 5 cents per hazard; check the Console usage page for the exact figure.
- A full case with 3 non-urgent hazards is about 30 to 40 seconds sequential. If the UI needs it faster, run hazards in parallel threads; nothing in the agent is shared state.
- Eval e uses an invented tag `asbestos` to hit the no-corpus path, because every contract hazard either has a corpus or is urgent. The reducer would reject that tag on save, which is fine since evals never save.
- Model output is not deterministic. 9/9 passed twice in a row after the prompt fix; rerun evals after any prompt change.
- The hantavirus ban is a per-hazard term list in `agent.BANNED_TERMS`. Extend it if other hazards need the same guard.
- Not covered yet: a `notes` field is not in contracts.md section 3. If the team wants it, Divit should add it to the contract. The agent treats it as optional.

### Next

- Team sync: decide whether `notes` enters the contract, and how #5 consumes `not_assessed` and `dropped`.
- Parallelize hazards if latency matters for the demo.
- Rerun evals after the data layer adds the dedicated cockroach and electrical sources.
