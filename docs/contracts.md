# Contracts

Shared data shapes for the stack. Sections 1 to 3 are proposals for the whole team; Divit has the final say. Sections 4 and 5 cover Rab's health agent and the shared SpacetimeDB module. Sections 6 and 7 cover the legal agent (proposed on the `legal-agent` branch).
To add your agent's tables or output shape, send them to Rab and they get added here, so the module stays in one place.

Last updated: Sat Oct 24, 9:10 AM PT

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

## 6. Legal tables in module `home` (proposed, for Rab to review)

Added on the `legal-agent` branch. Same conventions as section 4: public tables, writes through reducers, reads over SQL.

**legal_source**: key (string, primary key, `"<cite> | <url>"`), url, cite, kind (statute|ordinance|regulation|case_law|settlement|agency|legal_aid|other), jurisdiction, excerpt, verified (bool: true for jurisdiction-pack sources), fetched_at (timestamp)

**legal_assessment**: id (u32 autoInc primary key), case_id, hazard, payload_json (string, section 7 object), created_at (timestamp)

Reducers:
- `add_legal_source(url, cite, kind, jurisdiction, excerpt, verified)`: upserts by cite + url, since several sections of one statute share a url. Rejects non-https urls, unknown kinds, empty cite.
- `save_legal_assessment(case_id, hazard, payload_json)`: same checks as `save_assessment` (known hazard tag, non-empty case_id, valid JSON).

## 7. Legal agent output

`agents/legal/agent.py`. Same input as the health agent (section 3) and the same confidence rule: hazards below 0.5 that the tenant hasn't confirmed go to `not_assessed`.

Sources are, in order of trust: the verified jurisdiction pack in `data/packs/` (ids `P#` for the hazard's citations, `P0` for the pack's summary of that hazard, `G#` general rules, `C#` city procedures), then pages the agent read through Apify's RAG Web Browser or CourtListener (`R#`). A researched source is only saved if its excerpt is found in the page the agent actually read.

One object per hazard:

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

Rules, enforced in `agents/legal/validate.py` (tests: `data/.venv/bin/python -m unittest agents/legal/test_validate.py`):
- Every claim cites known source ids or gets dropped.
- The pack's `never_say` list is enforced: no rent withholding advice, no promised outcome or money (amounts only inside `similar_cases`), no "court-ready", nothing stated as "mold".
- A number of days in a claim must appear in one of its cited sources.
- `similar_cases` must cite a court opinion or settlement.
- A tenant option that rests on a pack rule marked `legal_aid_question` or `eligibility_check_needed` is moved to `ask_legal_aid`.
- `urgent` and `urgent_message` reuse `agents/health/urgent.py`, so the safety message is identical across agents.

The agent returns one wrapper per case:

```json
{
  "case_id": "case_001",
  "pack_id": "ca-oakland",
  "assessments": [ /* section 7 objects */ ],
  "not_assessed": [{"hazard": "rodents", "reason": "low confidence and not confirmed by tenant"}],
  "legal_aid": [ /* the pack's legal_aid list, unchanged */ ],
  "dropped": [ /* claims removed by the validator, for audit only */ ],
  "usage": [ /* token counts and timing per hazard */ ]
}
```

Show `assessments` and `legal_aid` to the tenant, and list `not_assessed` as "not assessed, and why". `dropped` and `usage` are internal.

Env vars for the legal agent (repo-root `.env`, see `agents/legal/.env.example`): `ANTHROPIC_API_KEY_Legal`, `APIFY_TOKEN_Legal`, optional `COURTLISTENER_TOKEN_Legal`. Optional `STDB_URL_Legal`, `STDB_DB_Legal`, `STDB_TOKEN_Legal` override the shared `STDB_*` settings for the legal agent only.
