import { schema, table, t, SenderError } from 'spacetimedb/server';

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

function checkHazard(hazard: string) {
  if (!HAZARDS.has(hazard)) throw new SenderError(`unknown hazard: ${hazard}`);
}

function checkPublisher(publisher: string) {
  if (!PUBLISHERS.has(publisher)) throw new SenderError(`unknown publisher: ${publisher}`);
}

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

const spacetimedb = schema({ health_source, health_chunk, health_assessment });
export default spacetimedb;

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
    try {
      JSON.parse(payload_json);
    } catch {
      throw new SenderError('payload_json is not valid JSON');
    }
    ctx.db.health_assessment.insert({
      id: 0,
      case_id,
      hazard,
      payload_json,
      created_at: ctx.timestamp,
    });
  }
);
