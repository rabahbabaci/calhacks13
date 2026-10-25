import { schema, table, t, SenderError, type InferSchema, type ReducerCtx } from 'spacetimedb/server';

const HAZARDS = new Set([
  'dampness_mold',
  'water_leak',
  'rodents',
  'cockroaches',
  'exposed_wiring',
  'lead_paint',
  'inadequate_heat',
  'carbon_monoxide',
  'gas_leak',
]);

const PUBLISHERS = new Set(['CDC', 'CDPH', 'EPA', 'HUD', 'WHO']);

const AGENTS = new Set(['hazard', 'health', 'property', 'legal', 'packet', 'action', 'plan']);

function checkHazard(hazard: string) {
  if (!HAZARDS.has(hazard)) throw new SenderError(`unknown hazard: ${hazard}`);
}

function checkRunHazard(hazard: string) {
  if (hazard !== '' && !HAZARDS.has(hazard)) throw new SenderError(`unknown hazard: ${hazard}`);
}

function checkPublisher(publisher: string) {
  if (!PUBLISHERS.has(publisher)) throw new SenderError(`unknown publisher: ${publisher}`);
}

function checkAgent(agent: string) {
  if (!AGENTS.has(agent)) throw new SenderError(`unknown agent: ${agent}`);
}

function checkJson(value: string, field: string) {
  try {
    JSON.parse(value);
  } catch {
    throw new SenderError(`${field} is not valid JSON`);
  }
}

// ---------- health corpus (contracts.md section 4) ----------

const health_source = table(
  { name: 'health_source', public: true },
  {
    url: t.string().primaryKey(),
    title: t.string(),
    publisher: t.string(),
    hazard: t.string(),
    last_updated: t.string(),
    fetched_at: t.timestamp(),
  }
);

const health_chunk = table(
  {
    name: 'health_chunk',
    public: true,
    indexes: [
      { accessor: 'by_source_ordinal', algorithm: 'btree', columns: ['source_url', 'ordinal'] },
    ],
  },
  {
    id: t.u32().primaryKey().autoInc(),
    source_url: t.string(),
    hazard: t.string().index('btree'),
    ordinal: t.u32(),
    text: t.string(),
  }
);

const health_assessment = table(
  { name: 'health_assessment', public: true },
  {
    id: t.u32().primaryKey().autoInc(),
    case_id: t.string().index('btree'),
    hazard: t.string(),
    payload_json: t.string(),
    created_at: t.timestamp(),
  }
);

// ---------- live core (contracts.md section 6) ----------

const case_file = table(
  { name: 'case_file', public: true },
  {
    case_id: t.string().primaryKey(),
    address: t.string(),
    payload_json: t.string(),
    created_at: t.timestamp(),
  }
);

const hazard_row = table(
  { name: 'hazard', public: true },
  {
    id: t.u32().primaryKey().autoInc(),
    case_id: t.string().index('btree'),
    hazard: t.string(),
    location: t.string(),
    confidence: t.f32(),
    confirmed_by_tenant: t.bool(),
    evidence_ids_json: t.string(),
    created_at: t.timestamp(),
  }
);

const agent_run = table(
  {
    name: 'agent_run',
    public: true,
    indexes: [
      { accessor: 'by_case_agent_hazard', algorithm: 'btree', columns: ['case_id', 'agent', 'hazard'] },
    ],
  },
  {
    id: t.u32().primaryKey().autoInc(),
    case_id: t.string(),
    agent: t.string(),
    hazard: t.string(),
    status: t.string(),
    payload_json: t.string(),
    error: t.string(),
    updated_at: t.timestamp(),
  }
);

// Legal agent (agents/legal): every law, ordinance, opinion or page it cited,
// shared across cases. Its per-hazard output goes in agent_run like every
// other agent. See docs/contracts.md section 7.
const LEGAL_SOURCE_KINDS = new Set([
  'statute',
  'ordinance',
  'regulation',
  'case_law',
  'settlement',
  'agency',
  'legal_aid',
  'other',
]);

const legal_source = table(
  { name: 'legal_source', public: true },
  {
    key: t.string().primaryKey(),
    url: t.string(),
    cite: t.string(),
    kind: t.string(),
    jurisdiction: t.string(),
    excerpt: t.string(),
    verified: t.bool(),
    fetched_at: t.timestamp(),
  }
);

const spacetimedb = schema({
  health_source,
  health_chunk,
  health_assessment,
  case_file,
  hazard: hazard_row,
  agent_run,
  legal_source,
});
export default spacetimedb;

type Ctx = ReducerCtx<InferSchema<typeof spacetimedb>>;

function requireCase(ctx: Ctx, case_id: string) {
  if (!ctx.db.case_file.case_id.find(case_id)) throw new SenderError(`unknown case: ${case_id}`);
}

function findRun(ctx: Ctx, case_id: string, agent: string, hazard: string) {
  return [...ctx.db.agent_run.by_case_agent_hazard.filter([case_id, agent, hazard])][0];
}

// ---------- health corpus reducers ----------

export const add_source = spacetimedb.reducer(
  {
    url: t.string(),
    title: t.string(),
    publisher: t.string(),
    hazard: t.string(),
    last_updated: t.string(),
  },
  (ctx, { url, title, publisher, hazard, last_updated }) => {
    if (!url.startsWith('https://')) throw new SenderError(`url must be https: ${url}`);
    checkPublisher(publisher);
    checkHazard(hazard);
    const row = { url, title, publisher, hazard, last_updated, fetched_at: ctx.timestamp };
    if (ctx.db.health_source.url.find(url)) {
      ctx.db.health_source.url.update(row);
    } else {
      ctx.db.health_source.insert(row);
    }
  }
);

export const add_chunk = spacetimedb.reducer(
  { source_url: t.string(), hazard: t.string(), ordinal: t.u32(), text: t.string() },
  (ctx, { source_url, hazard, ordinal, text }) => {
    checkHazard(hazard);
    const source = ctx.db.health_source.url.find(source_url);
    if (!source) throw new SenderError(`no source for chunk: ${source_url}`);
    if (source.hazard !== hazard) {
      throw new SenderError(`hazard ${hazard} does not match source hazard ${source.hazard}`);
    }
    if (text.trim().length === 0) throw new SenderError('empty chunk text');
    const existing = [...ctx.db.health_chunk.by_source_ordinal.filter([source_url, ordinal])][0];
    if (existing) {
      ctx.db.health_chunk.id.update({ ...existing, hazard, text });
    } else {
      ctx.db.health_chunk.insert({ id: 0, source_url, hazard, ordinal, text });
    }
  }
);

export const save_assessment = spacetimedb.reducer(
  { case_id: t.string(), hazard: t.string(), payload_json: t.string() },
  (ctx, { case_id, hazard, payload_json }) => {
    checkHazard(hazard);
    if (case_id.trim().length === 0) throw new SenderError('empty case_id');
    checkJson(payload_json, 'payload_json');
    ctx.db.health_assessment.insert({
      id: 0,
      case_id,
      hazard,
      payload_json,
      created_at: ctx.timestamp,
    });
  }
);

// ---------- live core reducers ----------

export const create_case = spacetimedb.reducer(
  { case_id: t.string(), address: t.string(), payload_json: t.string() },
  (ctx, { case_id, address, payload_json }) => {
    if (case_id.trim().length === 0) throw new SenderError('empty case_id');
    checkJson(payload_json, 'payload_json');
    const existing = ctx.db.case_file.case_id.find(case_id);
    if (existing) {
      ctx.db.case_file.case_id.update({ ...existing, address, payload_json });
    } else {
      ctx.db.case_file.insert({ case_id, address, payload_json, created_at: ctx.timestamp });
    }
  }
);

export const add_hazard = spacetimedb.reducer(
  {
    case_id: t.string(),
    hazard: t.string(),
    location: t.string(),
    confidence: t.f32(),
    evidence_ids_json: t.string(),
  },
  (ctx, { case_id, hazard, location, confidence, evidence_ids_json }) => {
    requireCase(ctx, case_id);
    checkHazard(hazard);
    if (!(confidence >= 0 && confidence <= 1)) {
      throw new SenderError(`confidence must be between 0 and 1: ${confidence}`);
    }
    let ids: unknown;
    try {
      ids = JSON.parse(evidence_ids_json);
    } catch {
      throw new SenderError('evidence_ids_json is not valid JSON');
    }
    if (!Array.isArray(ids)) throw new SenderError('evidence_ids_json must be a JSON array');
    ctx.db.hazard.insert({
      id: 0,
      case_id,
      hazard,
      location,
      confidence,
      confirmed_by_tenant: false,
      evidence_ids_json,
      created_at: ctx.timestamp,
    });
  }
);

export const confirm_hazard = spacetimedb.reducer(
  { id: t.u32(), confirmed: t.bool() },
  (ctx, { id, confirmed }) => {
    const row = ctx.db.hazard.id.find(id);
    if (!row) throw new SenderError(`unknown hazard id: ${id}`);
    ctx.db.hazard.id.update({ ...row, confirmed_by_tenant: confirmed });
  }
);

export const start_run = spacetimedb.reducer(
  { case_id: t.string(), agent: t.string(), hazard: t.string() },
  (ctx, { case_id, agent, hazard }) => {
    checkAgent(agent);
    checkRunHazard(hazard);
    requireCase(ctx, case_id);
    const existing = findRun(ctx, case_id, agent, hazard);
    const fields = { status: 'running', payload_json: '', error: '', updated_at: ctx.timestamp };
    if (existing) {
      ctx.db.agent_run.id.update({ ...existing, ...fields });
    } else {
      ctx.db.agent_run.insert({ id: 0, case_id, agent, hazard, ...fields });
    }
  }
);

export const finish_run = spacetimedb.reducer(
  { case_id: t.string(), agent: t.string(), hazard: t.string(), payload_json: t.string() },
  (ctx, { case_id, agent, hazard, payload_json }) => {
    checkAgent(agent);
    checkRunHazard(hazard);
    const run = findRun(ctx, case_id, agent, hazard);
    if (!run) throw new SenderError(`no run for ${case_id}/${agent}/${hazard || '-'}`);
    if (run.status !== 'running') throw new SenderError(`run is ${run.status}, not running`);
    checkJson(payload_json, 'payload_json');
    ctx.db.agent_run.id.update({ ...run, status: 'done', payload_json, error: '', updated_at: ctx.timestamp });
  }
);

export const fail_run = spacetimedb.reducer(
  { case_id: t.string(), agent: t.string(), hazard: t.string(), error: t.string() },
  (ctx, { case_id, agent, hazard, error }) => {
    checkAgent(agent);
    checkRunHazard(hazard);
    const run = findRun(ctx, case_id, agent, hazard);
    if (!run) throw new SenderError(`no run for ${case_id}/${agent}/${hazard || '-'}`);
    if (run.status !== 'running') throw new SenderError(`run is ${run.status}, not running`);
    if (error.trim().length === 0) throw new SenderError('empty error');
    ctx.db.agent_run.id.update({ ...run, status: 'failed', error, updated_at: ctx.timestamp });
  }
);

// ---------- legal reducers ----------

export const add_legal_source = spacetimedb.reducer(
  {
    url: t.string(),
    cite: t.string(),
    kind: t.string(),
    jurisdiction: t.string(),
    excerpt: t.string(),
    verified: t.bool(),
  },
  (ctx, { url, cite, kind, jurisdiction, excerpt, verified }) => {
    if (!url.startsWith('https://')) throw new SenderError(`url must be https: ${url}`);
    if (!LEGAL_SOURCE_KINDS.has(kind)) throw new SenderError(`unknown source kind: ${kind}`);
    if (cite.trim().length === 0) throw new SenderError('empty cite');
    // One row per citation: several sections of one statute can share a url.
    const key = `${cite} | ${url}`;
    const row = { key, url, cite, kind, jurisdiction, excerpt, verified, fetched_at: ctx.timestamp };
    if (ctx.db.legal_source.key.find(key)) {
      ctx.db.legal_source.key.update(row);
    } else {
      ctx.db.legal_source.insert(row);
    }
  }
);
