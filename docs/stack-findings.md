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
- Agents: hazard, health, property, legal, packet, action, plan. Use `""` as hazard for case-level agents.
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

## Vision agent (Divit)

Agent #1, computer vision hazard detection. Code in `agents/vision/`. Built Sat 6:30 to 7:00 PM PT.

**Status:** 78/78 unit tests pass with no API calls. 6/6 behavioral evals pass, replaying for free from fixtures (first recording $0.187, re-recorded after the speed changes for $0.135). A 5-minute video runs end to end in 26 seconds for about $0.15. `--save` writes to the live hazard and agent_run tables (see Saving to SpacetimeDB).

### How to use it

```python
import sys; sys.path.insert(0, "agents/vision")
from agent import analyze, ResponseCache
out = analyze(case, [{"type": "video", "path": "walkthrough.mp4", "start": 270, "end": 330}],
              cache=ResponseCache("agents/vision/out/cache"), live=True, save=False)
out["case_input"]     # contracts.md section 3, pass straight to agents/health assess_case()
out["vision_report"]  # contracts.md section 8
```

- CLI: `data/.venv/bin/python agents/vision/agent.py CASE.json --video V.mp4 --segment 270-330 [--segment ...] [--photo P.jpg] [--dry-run] [--live] [--save]`
- `--dry-run` extracts frames and prints the estimated cost, no API call. Without `--live`, only cached responses in `agents/vision/out/cache/` are used, so a rerun never spends credits by accident.
- Needs ffmpeg (`brew install ffmpeg`), and `anthropic`, `Pillow`, `pytest`, `yt-dlp` in `data/.venv`. For a user-scoped key (`sk-ant-usr-...`), also set `ANTHROPIC_WORKSPACE_ID=wrkspc_...` in `.env`; the agent sends it as the `anthropic-workspace-id` header.

Tests and evals (repo root):

```
data/.venv/bin/python -m pytest agents/vision/tests -q          # unit, zero API calls
data/.venv/bin/python agents/vision/evals.py                     # replay all 6 from fixtures, free
data/.venv/bin/python agents/vision/evals.py --dry-run           # frames + estimated cost, no API call
data/.venv/bin/python agents/vision/evals.py --live --budget-usd 0.50   # calls Claude only where a fixture is missing or stale
```

Replay works without the media too, from the frames recorded in each fixture. To rebuild the media locally (gitignored, never committed), see the header of `agents/vision/evals.py`.

### Design

1. Frames, no LLM (`frames.py`): ffmpeg samples 1 frame per second within the segment and scales to a long edge of 768px in the same pass, skipping audio. A 64-bit difference hash written with Pillow drops near-duplicates (Hamming distance 6 or less). The cap is 12 frames per segment, picked evenly. IDs are `video_1_t285s`, with seconds counted from the start of the source video, or `photo_1`. There is no face filter; the prompt says never to cite face or title frames, and the evals check it.
2. One Claude call per segment (`agent.py`), on `claude-sonnet-5-5` with all frames in a single message. Segments run in parallel (up to 6 threads, one shared client), so total time is the slowest segment, not the sum. There is one tool, `report_hazards`, whose `hazard` field is an enum of the 9 tags. Anything that fits no tag goes in `unlabeled_observations`. The model writes only hazards and unlabeled observations, with at most 2 signs per hazard. Code derives `rooms` and `not_visible_from_video`, since output tokens are what set latency.
3. Validator in code, after the model. A hazard is dropped if it has an unknown tag, an evidence_id that wasn't sent, empty observed_signs, a bbox outside 0-1, any pack `never_say` phrase in any text field, or (for lead_paint) wording that says lead is present. Confidence is clamped to 0-1. `first_seen_s` is recomputed from the cited frames, not taken from the model. The same tag in the same room is merged across frames and segments, and `confirmed_by_tenant` is always false.
4. Rubric in code (`rules.py`). Code never lowers urgent tags: exposed_wiring, gas_leak and carbon_monoxide are always urgent and are reported at any confidence. dampness_mold on a ceiling or in a bedroom is at least high. lead_paint is always moderate with needs_professional_check.
5. Legal in code: each hazard gets its `data/packs/ca_oakland.json` entry (cockroaches resolves to rodents via `same_as`), with citations, landlord_duty and cautions. The model never writes legal text. Urgent hazards also carry the health agent's fixed `urgent_message`.
6. Record/replay. The cache key is a hash of the model, system prompt, tool schema, prompt text and frame hashes. Fixtures hold the response and frame metadata, with no image bytes. A prompt or frame change shows up as a stale fixture instead of a silent rerun. `--budget-usd` aborts before a call would pass the budget. The system prompt uses prompt caching: about 2.7k tokens read from cache on every call after the first.

### Real-video test

Source: [The WORST APARTMENT in Jakarta!](https://www.youtube.com/watch?v=wFxmYq9wCWM) (11:40). Segments were picked from 10-second and 2-second contact sheets reviewed by eye, with no API calls. The case uses Oakland as a demo stand-in, with notes "demo footage from Jakarta; legal grounding is illustrative", and the report carries that note.

| Eval | Input | Result |
|---|---|---|
| a | kitchen, 4:30 to 5:30 | dampness_mold low (0.40) at the faucet sealant, flagged as a wet surface. Cockroaches correctly not reported: they are only in the caption, and the model wrote "no live or dead insects ... visible in any frame" |
| b | balcony, 8:40 to 9:40 | lead_paint moderate + professional check (peeling sill paint), dampness_mold low (0.30). Rust and cracks went to unlabeled |
| c | bathroom, 9:50 to 10:50 | dampness_mold high (ceiling and wall joint), water_leak low (ceiling tide-line stain). Cracks went to unlabeled |
| d | clean hotel room photo, CC0 ([source](https://commons.wikimedia.org/wiki/File:Room_at_Hotel_Mercosur_Universitas.jpg)) | zero hazards |
| e | open switch box photo, public domain ([source](https://commons.wikimedia.org/wiki/File:Opened_light_switch.JPG)) | exposed_wiring urgent (0.85), 1941.1(a)(5) attached |
| f | shower walls, 10:05 to 10:17 | dampness_mold low with the wet-surface flag |

No hazard cited any of the 4 face or title frames that were sent.

Unlabeled observations from the real-video test (for the tag decision):

| Observation | Frames | Model's suggested tag | Possible tag |
|---|---|---|---|
| Brown rust-like staining around the kitchen sink drain ring | video_1_t292s | rust_sink_drain | rust_corrosion |
| Counter and backsplash sealant gapped, dirty and deteriorated near the faucet | video_1_t285s, t289s | deteriorated_sealant | none (precursor to water_leak) |
| Plug adapter and loose cords near the sink; outlets have covers, no bare wires | video_1_t296s, t306s | cords_near_sink | none |
| Cracked concrete at the base of the balcony railing, rust staining on the posts and ledge | video_1_t556s | balcony_crack_and_rust | structural_damage, rust_corrosion |
| Yellow-brown stains on balcony tiles, dirty scuffed walls and floor | video_1_t536s, t556s, t560s, t565s | balcony_dirt_and_stains | none (cleanliness) |
| Crack down the bathroom wall near the shower head | video_1_t620s | wall_crack | structural_damage |
| Faint crack and stain streak near the towel rack | video_1_t636s | wall_crack | structural_damage |
| Shower drain frame dark with grime, drain cover corroded | video_1_t613s, t614s | dirty_drain | rust_corrosion |
| Broken, crumbling drywall around the open switch box (eval e) | photo_1 | damaged_wall_opening | structural_damage |

The two candidates that keep coming up are `rust_corrosion` (3 times) and `structural_damage` (4 times). Neither is a contract tag, and none has been added.

### What worked

- Captions said "cockroaches crawling around" and "covered in mold", and the vlog title says "Cockroaches, Mold, Rust". The model judged only what was visible, and listed cockroaches under not_visible_from_video.
- The clean room came back with zero hazards and no padding.
- No live hazard was dropped by the validator. The `unlabeled_observations` list soaked up everything off-tag instead of forcing it into the closest tag.
- Room inference held up: kitchen, bathroom and balcony, and "unknown room" for the window sill and the switch-box photo, never a guess.

### What broke, and the fix

- The user-scoped key (`sk-ant-usr-...`) returned 400, "must include the anthropic-workspace-id header". Fix: `ANTHROPIC_WORKSPACE_ID` in `.env`, sent as a default header.
- `claude-sonnet-5-5` returned 400, "tool_choice: type tool and any are not supported for this model". Fix: drop forced tool_choice. The prompt says to call report_hazards once, and code raises if it doesn't.
- The shower grout came back as moderate: the `patch` floor raised it, ignoring the pack caution that surfaces meant to get wet are excluded. Fix in code, not the prompt, so the fixtures stayed valid: dampness_mold whose location mentions shower, tub or grout (and not a ceiling) is capped at low, or moderate if widespread, and gets `wet_surface: true`.
- Eval c first failed with "rust not in unlabeled". The "rusty shower" was only in the caption; at 768px the shower head reads as scale. The check was too strict for the footage, so it now accepts rust or cracks, the same as eval b. Rust did land in unlabeled in a, b and f.
- `data/.venv` did not exist on Divit's laptop. It was created fresh with Python 3.14 and anthropic 2.3.0.
- Homebrew's default ffmpeg may not include drawtext, so the contact sheets are built in Pillow.

### Cost, latency, limits

- Call time follows output tokens, not frames: about 3 seconds fixed plus about 10ms per output token. The first version wrote 1.4k to 1.7k tokens per segment (15 to 20 seconds). Trimming the schema (code derives rooms and not-visible, at most 2 signs, short reasons) cut it to 0.2k to 1k (5 to 13 seconds), and every eval still passes. Re-recording the 6 fixtures cost $0.135.
- **5-minute video test** (Jakarta 4:30 to 9:30, five 60-second segments in parallel, live): **26.0 seconds end to end, $0.152.** Extraction was about 2 seconds per segment, all at once. Calls took 11 to 15 seconds, except one segment that wrote 1.4k tokens and took 22.8 seconds, which set the total. Replaying the same run from cache takes 2.2 seconds, the fixed overhead. Result after merging: dampness_mold high (bedroom), lead_paint moderate (bedroom sill), dampness_mold low (kitchen), plus 8 unlabeled observations.
- The slowest segment sets the total time. To get closer to 15 seconds, split into 30-second segments (about 6 frames each) so no single call writes much. Untested; it would cost about the same, since total frames are unchanged and the system prompt is cached.
- Upload time is not included. For a phone, extract frames on the device and upload about 60 JPEGs (3 to 5 MB) instead of the video (300 to 600 MB at 1080p). `analyze()` already accepts prepared frames.
- The cost estimate is close: about $0.036 estimated per 12-frame segment, against $0.022 to $0.039 actual (the high end is a segment that wrote 1.4k tokens).
- Only 12 frames per segment, so short events can fall between samples. Use more, shorter segments rather than raising the cap.
- No face filter: face frames are still sent, they just can't be cited. That's about 2 wasted frames per vlog segment.
- Confidence is the model's own estimate, not a measured probability. The health agent skips anything under 0.5 that the tenant hasn't confirmed, so the kitchen and balcony dampness (0.40, 0.30) would only be assessed after tenant confirmation.
- Model output is not deterministic. Re-record with `--live` after any prompt change: a stale fixture is reported, never silently reused.

### Saving to SpacetimeDB

- `--save` writes to the live core tables (section 6), with no module changes: `create_case`, then `start_run("hazard", "")`, then `add_hazard` for each new hazard, then `finish_run` with the vision_report, or `fail_run` on error. Details in contracts.md section 8. The earlier `hazard_detection` / `save_detection` proposal is dropped.
- Checked against Maincloud by replaying the cached 5-minute run (no Claude spend) as `case_vision_jakarta`. It wrote the case, 3 unconfirmed hazard rows (ids 8 to 10), and the `hazard` run as `done` with a 15 KB payload. A second run added no rows.
- Those 3 rows are still on Maincloud. Confirming one in the UI makes the orchestrator run health and legal on it, which spends Claude credits.

### Next

- Decide on `rust_corrosion` and `structural_damage` (see the table above) before adding any tag. Adding one touches contracts.md section 1, the module's HAZARDS set and the pack.
- Optional local face filter to save about 2 frames per vlog segment.
- UI: show `label`, `location`, the cited frames and the bbox; let the tenant confirm each hazard before it goes to the health agent.

## App (Divit)

The tenant site is `web/` (Vite + React, mobile first, installable from `manifest.webmanifest`). The same build is the iPhone app: `web/capacitor.config.json` is ready for `npx cap add ios` from `web/` after `npm run build`. The API is `tenant/server.py`. The browser talks only to that API.

Run both, from the repo root:

```
data/.venv/bin/python -m tenant.server
cd web && npm install && npm run dev
```

Open http://127.0.0.1:5173. The API is http://127.0.0.1:8787, and Vite proxies `/api`.

**Fixture mode, which is the default.** Sign-in says Continue with Google. No Google client id is configured, so the button starts a local demo session and does not call Google. Enter an address and, if you want, a video. The file is saved under `tenant/out/media/` (gitignored). Rooms and hazards come from `tenant/fixture.py`, a saved three-hazard walkthrough, so this path does not call Claude, Gmail, Apify, or the Census geocoder. Health and legal blocks stay waiting until "This is in my home". The property block is already filled from the fixture. Preview does not send. Send uses an in-memory mailer. Download returns a one-page PDF with the draft, the Oakland complaint URL, and the pack's filing steps.

**Still needs a Google OAuth client** before real sign-in or Gmail send. Set a client id, then the login button should use Google Identity Services and ask for `gmail.send` only on Send. Until then leave `HOME_DEV_LOGIN=1` (the default). Set `HOME_DEV_LOGIN=0` to hide the demo session.

**Orchestrator.** `integration/orchestrator.py` now launches the property agent once per case that has an address and no property run. Health and legal are unchanged: they still run only for confirmed hazards. Starting the orchestrator for real will call the property agent, which spends Claude and Apify. It was not started for this work.

Tests, with no network: `data/.venv/bin/python -m pytest tenant/tests -q`.
