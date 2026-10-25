# Contracts

Shared data shapes for the stack. Sections 1 to 3 are proposals for the whole team; Divit has the final say. Sections 4 and 5 cover Rabah's health agent and the shared SpacetimeDB module.
To add your agent's tables or output shape, send them to Rabah and they get added here, so the module stays in one place.

Last updated: Sat Oct 24, 6:10 PM PT

## 1. Hazard tags (proposed)

```
dampness_mold, water_leak, rodents, cockroaches, exposed_wiring,
lead_paint, inadequate_heat, carbon_monoxide, gas_leak
```

Tags are internal. User-facing labels never say "mold" as a finding; use "visible dampness or possible mold growth".

## 2. Hazard object (proposed)

```json
{
  "hazard": "dampness_mold",
  "location": "bathroom ceiling",
  "confidence": 0.82,
  "confirmed_by_tenant": true,
  "evidence_ids": ["photo_3"]
}
```

## 3. Case input (proposed)

```json
{
  "case_id": "case_001",
  "address": "123 Example St, Oakland, CA 94612",
  "jurisdiction": {"state": "CA", "city": "Oakland"},
  "hazards": [ /* hazard objects */ ],
  "household": {
    "children": false,
    "older_adults": false,
    "pregnancy": false,
    "breathing_condition": false
  },
  "notes": "optional free text from the tenant"
}
```

`notes` is optional. Agents may read it for context but never treat it as a finding or connect it to a hazard.

## 4. SpacetimeDB module `home`

**Shared db:** `https://maincloud.spacetimedb.com`, database `home`. No `Authorization` header needed for SQL reads or reducer calls on these public tables. Setup, Python and curl examples: `docs/stack-findings.md`, "How to connect".

All tables public. Writes go through reducers only. Reads use SQL over HTTP.

**health_source**: url (string, primary key), title, publisher (CDC|CDPH|EPA|HUD|WHO), hazard, last_updated (string), fetched_at (timestamp)

**health_chunk**: id (u32 autoInc primary key), source_url, hazard, ordinal (u32), text

**health_assessment**: id (u32 autoInc primary key), case_id, hazard, payload_json (string), created_at (timestamp)

Reducers:
- `add_source(url, title, publisher, hazard, last_updated)`
- `add_chunk(source_url, hazard, ordinal, text)`
- `save_assessment(case_id, hazard, payload_json)`

Notes for readers:
- Timestamps (`fetched_at`, `created_at`) come back from SQL over HTTP as a one-element array of microseconds since epoch, e.g. `[1792862222185738]`. Divide by 1,000,000 for Unix seconds.
- Reducer errors return HTTP 530. `data/stdb.py` raises `StdbError`.
- There is no delete reducer. Never run `spacetime publish --delete-data=always`: it wipes every table, including saved assessments.

## 5. Health agent output

One object per hazard.

```json
{
  "hazard": "dampness_mold",
  "urgent": false,
  "urgent_message": null,
  "exposure_pathways": [{"text": "...", "chunk_ids": [12, 14]}],
  "health_risks": [{"text": "...", "chunk_ids": [15]}],
  "vulnerable_groups": [{"text": "...", "chunk_ids": [16]}],
  "precautions": [{"text": "...", "chunk_ids": [21]}],
  "see_a_doctor_if": [{"text": "...", "chunk_ids": [22]}],
  "sources": [{"url": "...", "title": "...", "publisher": "CDC"}],
  "disclaimer": "General public health information, not medical advice or a diagnosis."
}
```

Rules: every claim cites retrieved chunk IDs or gets dropped. `exposed_wiring`, `carbon_monoxide`, `gas_leak` set `urgent: true` with a fixed safety message before any LLM call. Urgent hazards use the same shape with empty claim lists.

The agent returns one wrapper per case:

```json
{
  "assessments": [ /* section 5 objects */ ],
  "not_assessed": [{"hazard": "rodents", "reason": "unconfirmed, confidence below 0.5"}],
  "dropped": [ /* claims removed by the validator, for audit only */ ],
  "usage": [ /* token counts per hazard */ ]
}
```

For downstream agents and the packet: show `assessments` to the tenant. List `not_assessed` as "not assessed, and why". `dropped` and `usage` are internal and never shown to the tenant.

## 6. Shared case, hazard and agent run tables

The live core of the app. Every agent writes here, and the UI subscribes so the pipeline fills in on screen as agents finish. (`case` is a SQL keyword, so the table is `case_file`.)

**case_file**: case_id (string, primary key), address (string), payload_json (string, the section 3 case input), created_at (timestamp)

**hazard**: id (u32 autoInc primary key), case_id, hazard (tag from section 1), location (string), confidence (f32), confirmed_by_tenant (bool), evidence_ids_json (string), created_at (timestamp)

**agent_run**: id (u32 autoInc primary key), case_id, agent (`hazard` | `health` | `property` | `legal` | `packet` | `action` | `plan`), hazard (tag, or "" for case-level agents), status (`pending` | `running` | `done` | `failed`), payload_json (string, that agent's own output shape, "" until done), error (string, "" unless failed), updated_at (timestamp). One row per (case_id, agent, hazard), updated in place.

Reducers (the module enforces the rules):
- `create_case(case_id, address, payload_json)`: upsert
- `add_hazard(case_id, hazard, location, confidence, evidence_ids_json)`: case must exist, tag must be valid
- `confirm_hazard(id, confirmed)`: the tenant's yes/no from the UI
- `start_run(case_id, agent, hazard)`: creates the row or resets it to `running`
- `finish_run(case_id, agent, hazard, payload_json)`: `running` to `done`
- `fail_run(case_id, agent, hazard, error)`: `running` to `failed`

Rejected with an error: unknown agent or hazard tag, unknown case, finishing a run that isn't `running`.

UI subscriptions:
- `SELECT * FROM hazard WHERE case_id = '<id>'`
- `SELECT * FROM agent_run WHERE case_id = '<id>'`

Each agent keeps its own output shape (sections 5, 7 and so on) inside `payload_json`. Agents only need two calls: `start_run` when they begin, `finish_run` or `fail_run` when they end.

## 7. Legal agent

`agents/legal/agent.py`. Same input as the health agent (section 3) and the same confidence rule: hazards below 0.5 that the tenant hasn't confirmed go to `not_assessed`.

**Default: verified pack only.** Each hazard the pack covers gets one Claude call that writes from the pack's `P#`, `G#` and `C#` sources only (no web research, about 10 s per hazard). `similar_cases` is empty. A hazard with no pack for the area, or one the pack doesn't cover, goes to `not_assessed` with reason `"not verified for your area"`; with no pack, `legal_aid` is empty. Set `LEGAL_RESEARCH=1` to turn on the research path described next (slower; it adds Apify research, CourtListener and `L#` prior sources, and sends back once a submit with no similar cases).

**Draft packs.** A pack whose `status` is anything other than `"verified"` (packs with no `status` count as verified) is skipped, so its law is never shown as verified. Mark a new pack `"status": "draft_needs_verification"` until a person checks every excerpt against its `source_url`, then set `status` to `"verified"` and fill `verified_on`. `data/packs/ca.json`, the California state pack, is verified (2026-10-25) and covers California addresses outside cities with their own pack. `LEGAL_DRAFT_PACKS=1` uses draft packs for local testing, with their sources marked unverified.

**With `LEGAL_RESEARCH=1`, any US address.** If the case's `jurisdiction` lacks a state or city, the agent fills it (and the county) from the Census geocoder (`data/geocode.py`). Pack lookup is a city pack, else a state-wide pack (`"city": ""`), else none. Without a pack the agent researches the state's landlord-tenant statute and the local housing code itself, and gets the state's CourtListener court id. Sources it verified in earlier cases in the same state are read back from `legal_source` and offered as `L#` ids (state statutes, regulations and cases from anywhere in the state; ordinances and agency pages only for the same city).

**Runs.** One `agent_run` row per hazard with agent `legal`: `start_run(case_id, "legal", hazard)` when it begins, then `finish_run` with the object below as `payload_json`, or `fail_run` with the error. If the case isn't in `case_file` yet, the agent creates it from its input with `create_case`.

**Sources**, in order of trust: the verified jurisdiction pack in `data/packs/` (ids `P#` for the hazard's citations, `P0` for the pack's summary of that hazard, `G#` general rules, `C#` city procedures), then pages the agent read through Apify's RAG Web Browser or CourtListener (`R#`). A researched source is only kept if its excerpt is found on the page the agent actually read.

**legal_source** (new table, the only module change): key (string, primary key, `"<cite> | <url>"`), url, cite, kind (statute|ordinance|regulation|case_law|settlement|agency|legal_aid|other), jurisdiction, excerpt, verified (bool: true for jurisdiction-pack sources), fetched_at (timestamp). Every source cited in a finished run is upserted here, so the UI can show the laws and pages behind a case and other agents can reuse them.

- `add_legal_source(url, cite, kind, jurisdiction, excerpt, verified)`: upserts by cite + url, since several sections of one statute share a url. Rejects non-https urls, unknown kinds and an empty cite.

**payload_json**, one object per hazard:

```json
{
  "hazard": "dampness_mold",
  "urgent": false,
  "urgent_message": null,
  "jurisdiction": {"state": "CA", "city": "Oakland"},
  "landlord_duty": [{"text": "...", "source_ids": ["P1", "P0"]}],
  "tenant_options": [{"text": "...", "source_ids": ["P0", "C1"]}],
  "deadlines": [{"text": "...", "source_ids": ["G2"]}],
  "ask_legal_aid": [{"text": "...", "source_ids": ["G2"]}],
  "similar_cases": [{"text": "...", "source_ids": ["R2"]}],
  "cautions": ["A photo or AI label is never an officer's determination"],
  "sources": [{"id": "P1", "cite": "Cal. Health & Safety Code 17920.3(a)(11)", "url": "https://...", "kind": "statute", "verified": true}],
  "disclaimer": "General legal information, not legal advice. ..."
}
```

Rules, enforced in `agents/legal/validate.py`. Tests for the validator and the agent run offline, with no keys or database: `data/.venv/bin/python -m unittest discover -s agents/legal -p "test_*.py"`.
- Every claim cites known source ids or gets dropped.
- The pack's `never_say` list is enforced: no rent withholding advice, no promised outcome or money (amounts only inside `similar_cases`), no "court-ready", nothing stated as "mold".
- A number of days in a claim must appear in one of its cited sources.
- `similar_cases` must cite a court opinion or settlement.
- A tenant option that rests on a pack rule marked `legal_aid_question` or `eligibility_check_needed` is moved to `ask_legal_aid`.
- `urgent` and `urgent_message` reuse `agents/health/urgent.py`, so the safety message is identical across agents.

The CLI also prints a wrapper per case: `{case_id, pack_id, assessments, not_assessed, legal_aid, failed, dropped, usage}`. Show `assessments` and the pack's `legal_aid` list to the tenant, and `not_assessed` as "not assessed, and why". `dropped` and `usage` are internal.

**Env vars** (repo-root `.env`, see `agents/legal/.env.example`): `ANTHROPIC_API_KEY_Legal`, `APIFY_TOKEN_Legal`, optional `COURTLISTENER_TOKEN_Legal`. Optional `STDB_URL_Legal`, `STDB_DB_Legal`, `STDB_TOKEN_Legal` override the shared `STDB_*` settings for the legal agent only.

## 8. Vision agent output (proposed)

The vision agent (`agents/vision/`) returns two objects per case.

**case_input**: a section 3 object whose `hazards` are exactly the section 2 shape. Pass it straight to the health agent's `assess_case()`. `confirmed_by_tenant` is always `false` from the vision agent; the tenant confirms in the UI.

**vision_report**: the full detail, one object per case.

```json
{
  "case_id": "case_001",
  "notes": "optional case notes, carried through",
  "source": {"type": "video", "url_or_path": "...", "segments": [{"start": 270, "end": 330, "frames": ["video_1_t282s"]}]},
  "frames": [{"evidence_id": "video_1_t282s", "t_s": 282.0, "path": "agents/vision/out/frames/case_001/video_1_t282s.jpg"}],
  "hazards": [{
    "hazard": "dampness_mold",
    "label": "Visible dampness or possible mold growth",
    "location": "kitchen counter, behind the faucet",
    "room": "kitchen",
    "bbox": [0.3, 0.4, 0.7, 0.7],
    "bbox_frame": "video_1_t285s",
    "evidence_ids": ["video_1_t285s"],
    "first_seen_s": 285.0,
    "observed_signs": ["dark residue along the backsplash joint"],
    "severity": "moderate",
    "severity_reason": "...",
    "extent": "patch",
    "confidence": 0.7,
    "needs_professional_check": true,
    "check_reason": "...",
    "confirmed_by_tenant": false,
    "legal": {"pack_id": "ca-oakland", "resolved_from": null, "citations": [{"cite": "...", "excerpt": "...", "source_url": "https://..."}],
              "landlord_duty": "...", "cautions": ["..."], "disclaimer": "..."},
    "urgent_message": "only for exposed_wiring, gas_leak, carbon_monoxide (same text as the health agent)"
  }],
  "unlabeled_observations": [{"description": "rust on the balcony railing", "evidence_ids": ["video_1_t556s"], "suggested_tag": "rust_corrosion"}],
  "rooms": [{"room": "kitchen", "evidence_ids": ["video_1_t285s"]}],
  "not_visible_from_video": ["carbon_monoxide: CO is invisible; check for a working CO alarm"],
  "dropped": [ /* hazards removed by the validator, internal only */ ],
  "usage": [ /* tokens, cost, cached flag, extract_s and call_s per segment, internal only */ ],
  "wall_seconds": 25.7
}
```

- `rooms` and `not_visible_from_video` are derived in code from the merged hazards: every tag with no finding is listed, with a fixed reason for carbon_monoxide, gas_leak and inadequate_heat.

- `severity` is one of `low`, `moderate`, `high`, `urgent`. `exposed_wiring`, `gas_leak`, `carbon_monoxide` are always `urgent`.
- `evidence_ids` are `video_<n>_t<seconds>s` (seconds from the start of the source video) or `photo_<n>`.
- `legal` comes from `data/packs/<state>_<city>.json` in code; `{"status": "no_pack_for_jurisdiction"}` when no pack exists. The model never writes it.
- `unlabeled_observations` are things that fit none of the section 1 tags. They are not hazards and are never saved as detections.
- Show the tenant `hazards` (using `label`, never the raw tag), `unlabeled_observations` and `not_visible_from_video`. `dropped` and `usage` are internal.

**Saving** (`--save`, or `analyze(..., save=True)`): writes to the live section 6 tables, in this order.

1. `create_case(case_id, address, case stub JSON)`
2. `start_run(case_id, "hazard", "")`, one case-level run per analysis
3. `add_hazard(case_id, hazard, location, confidence, evidence_ids_json)` for each hazard not already on the case. A row with the same tag and the same location, or a shared evidence frame, counts as already saved, so reruns don't duplicate. Rows start with `confirmed_by_tenant = false`.
4. `finish_run(case_id, "hazard", "", vision_report)`. Frames carry only `evidence_id` and `t_s`, with no local paths. On any error after `start_run`, it calls `fail_run` with the error instead.

The `hazard` row has no severity, label or legal field, so the UI joins each row to `agent_run.payload_json.hazards` on (hazard, location) to get them. After the tenant confirms a row, the orchestrator runs health and legal on it.

## 9. Property agent

`agents/property/agent.py`. The public history of a building from its address alone, for any US address. It doesn't use hazards. Input is a case object (section 3; only `case_id` and `address` are read), or `--address "..."` on the command line.

**Run.** One `agent_run` row: agent `property`, hazard `""`. `start_run`, then `finish_run` with the report below, or `fail_run`. It creates the case with `create_case` if it isn't there yet. No new tables.

**Sources.** Which ones exist depends on the city, so the agent finds them itself and starts from verified hints where there are some (`agents/property/known.py`: NYC, Chicago, Oakland, Cincinnati).

| Source | What it gives | Coverage |
|---|---|---|
| Census Geocoder (`data/geocode.py`) | Normalized address, city, county, state, ZIP | Every US address |
| HUD Multifamily Properties - Assisted (ArcGIS) | Federal inspection score and date, troubled status, management company and contact | HUD-assisted buildings nationwide |
| Socrata catalog + SoQL | Code violations, complaints, 311 requests | Hundreds of city and county portals |
| ArcGIS Online search + layer query | Parcels: APN, owner name or mailing address; some cities' code cases | Most counties |
| Accela Citizen Access (Apify Playwright) | Code-enforcement cases and permits by address | Cities on Accela, e.g. `OAKLAND` |
| Apify RAG Web Browser | City lookups, assessor pages, local news | Anywhere |
| CourtListener | Lawsuits naming the owner or building | Federal and state courts |

Not covered: owner names where the county doesn't publish them (California parcels give a mailing address only). Paid parcel APIs (Regrid, ATTOM) would fill that gap.

**payload_json:**

```json
{
  "address": "1510 Webster St, Oakland, CA 94612",
  "matched_address": "1510 WEBSTER ST, OAKLAND, CA, 94612",
  "jurisdiction": {"city": "Oakland", "county": "Alameda County", "state": "CA", "zip": "94612"},
  "owner": {"name": "...", "role": "owner", "mailing_address": "...", "data_id": "D4"},
  "manager": null,
  "records": [{"id": "R1", "kind": "violation", "title": "...", "date": "2026-09-24", "status": "open", "detail": "...", "ref": "2606350", "data_id": "D3"}],
  "patterns": [{"text": "...", "record_ids": ["R1", "R4"]}],
  "unresolved": [{"text": "...", "record_ids": ["R2"]}],
  "summary": "At most 3 plain sentences.",
  "coverage": [{"source": "OAK311 service requests", "result": "6 code enforcement requests since 2024"}],
  "disclaimer": "Compiled from public records, which can be incomplete, ..."
}
```

`kind` is one of ownership, violation, complaint, inspection, permit, enforcement, litigation, news, other.

Rules, enforced in `agents/property/validate.py`:
- Every fetched result gets a data id (`D#`). A record is kept only if it cites one and that result's raw text contains the record's `ref` or its date (ISO, US style or ArcGIS epoch milliseconds).
- Findings (`patterns`, `unresolved`) must cite kept records.
- The report is about real people and companies, so findings or a summary that accuse anyone ("illegal", "negligent", "slumlord", "bad faith", ...) are dropped.
- `owner` and `manager` are kept only if the name appears in the data they cite.
- Queries go only to public https hostnames: Socrata dataset ids and ArcGIS layer paths are checked, and IP addresses and localhost are refused.

The CLI prints `{case_id, report, dropped, usage}`. Show `report` to the tenant; `dropped` and `usage` are internal.

**Env vars** (repo-root `.env`, see `agents/property/.env.example`): `ANTHROPIC_API_KEY_Property`, `APIFY_TOKEN_Property`, optional `COURTLISTENER_TOKEN_Property`, `SOCRATA_APP_TOKEN_Property` (higher rate limits), and `STDB_URL_Property`, `STDB_DB_Property`, `STDB_TOKEN_Property` overrides.

Tests: `data/.venv/bin/python -m unittest discover -s agents/property -p "test_*.py"` (offline).

## 10. Tenant app (proposed)

The website and iPhone shell (`web/`, `tenant/`). No new SpacetimeDB table: `packet` and `action` are already agent names, and the screens below are computed in code. Fixture mode does not write to the database. Live saves stay on the section 6 reducers once a run is allowed.

**layout**, from the vision report, stored with the case only in the app for now:

```json
{"rooms": [{"id": "room_0", "name": "kitchen", "polygon": [[0, 0], [4, 0], [4, 3.5], [0, 3.5]], "height": 2.6,
  "door": {"wall": "south", "center": 0.5, "width": 0.9},
  "hazards": [{"hazard_id": "dampness_mold|kitchen counter", "icon": [2.0, 1.4, 3.3]}]}]}
```

Rooms follow the walk, by `first_seen_s`. `hazard_id` is the tag, a bar, and the location, which is how a hazard row joins the vision report.

**packet tile** (agent `packet`, hazard `""`). One list, recomputed when a hazard is confirmed, a message is logged, or evidence is added.

```json
{"id": "form-complaint", "title": "Oakland property complaint (code enforcement)", "kind": "form",
 "status": "ready", "needs": [], "changed": "",
 "preview": {
   "filled": [{"label": "Address", "value": "123 Example St, Oakland, CA 94612"}],
   "still_needed": ["Your phone number", "A short description in your own words", "Your signature"],
   "form": {"name": "Oakland property complaint (code enforcement)", "url": "https://www.oaklandca.gov/...", "official_form": true},
   "steps": ["OAK311 app or online form, with address, description and optional photos."],
   "note": "Opening the form does not mark this done."
 }}
```

`kind` is `email`, `form`, or `phone`. The form URL is a pack `source_url` the legal run cited, or a statute URL that run cited. The steps are the pack procedure's `how` text. A procedure marked `legal_aid_question` or `eligibility_check_needed` is replaced with "Ask legal aid first" and the pack's legal-aid phone. Opening the link does not mark the tile done. The tenant marks it filed.

**action draft** (agent `action`, hazard `""`), only when a letter is drafted by the model. The app's repair email and the form packet are built in code and do not use this until a live draft is allowed.

```json
{"subject": "Repair request", "sections": [{"text": "...", "source_ids": ["P1"]}]}
```

A section with no known source id is dropped. The legal pack's never_say rules apply, including a banned subject. The draft does not contain the form URL or the steps.

**correspondence**, kept with the case and shown on Tracking:

```json
{"messages": [{"id": "fake-1", "direction": "out", "counterparty": "owner@example.com", "role": "landlord",
  "subject": "Repair request", "status": "sent", "gmail_id": "fake-1", "at": "just now", "tile_id": "email-landlord|..."}],
 "evidence": [{"evidence_id": "photo_1", "kind": "photo", "at": "just now"}]}
```

`role` is `landlord`, `legal_aid`, or `agency`. A sent landlord email marks that tile done and adds "Log their reply" until an inbound landlord message exists.
