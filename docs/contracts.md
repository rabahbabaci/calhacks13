# Contracts

Shared data shapes for the stack. Sections 1 to 3 are proposals for the whole team; Divit has the final say. Sections 4 and 5 cover Rab's health agent and the shared SpacetimeDB module.
To add your agent's tables or output shape, send them to Rab and they get added here, so the module stays in one place.

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

## 6. Shared case and agent output tables (PROPOSED, needs Divit's ok)

Lets every agent write to the shared db without per-agent schemas, and lets the UI subscribe to one table for live updates.

**case_input**: case_id (string, primary key), payload_json (string, the section 3 case input), created_at (timestamp)

**agent_output**: id (u32 autoInc primary key), case_id, agent (`hazard` | `health` | `property` | `legal` | `packet` | `action`), hazard (string, empty if not per-hazard), payload_json (string, that agent's output shape), created_at (timestamp)

Reducers:
- `save_case(case_id, payload_json)`
- `save_output(case_id, agent, hazard, payload_json)`

Each agent keeps its own output shape inside `payload_json`. The UI subscribes to `SELECT * FROM agent_output WHERE case_id = '<id>'`.
