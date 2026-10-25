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

**agent_run**: id (u32 autoInc primary key), case_id, agent (`hazard` | `health` | `property` | `legal` | `packet` | `action`), hazard (tag, or "" for case-level agents), status (`pending` | `running` | `done` | `failed`), payload_json (string, that agent's own output shape, "" until done), error (string, "" unless failed), updated_at (timestamp). One row per (case_id, agent, hazard), updated in place.

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
