// Home: live case page. Pure SpacetimeDB client.
// Subscribes to hazard and agent_run for one case, renders on every change,
// and writes only through the confirm_hazard reducer.
//
// URL: /?case=case_demo_002   (default case_demo_002)
//      /?case=...&db=ws://127.0.0.1:3000   (offline fallback, see docs/stack-findings.md "Integration")

import { DbConnection, type EventContext } from "./module_bindings";
import type { Infer } from "spacetimedb";
import HazardRow from "./module_bindings/hazard_table";
import AgentRunRow from "./module_bindings/agent_run_table";

type Hazard = Infer<typeof HazardRow>;
type AgentRun = Infer<typeof AgentRunRow>;

const params = new URLSearchParams(location.search);
const CASE_ID = params.get("case") ?? "case_demo_002";
const DB_URI = params.get("db") ?? "wss://maincloud.spacetimedb.com";
const DB_NAME = "home";
const TOKEN_KEY = "stdb_token";

// Agents shown under each hazard, in order. Adding one here is the only UI change
// when a new per-hazard agent starts writing agent_run rows.
const AGENTS: { name: string; label: string }[] = [
  { name: "health", label: "Health" },
  { name: "legal", label: "Tenant law" },
];

// Internal tag -> what the tenant sees. "mold" is never a finding (contracts section 1).
const HAZARD_LABELS: Record<string, string> = {
  dampness_mold: "Visible dampness or possible mold growth",
  water_leak: "Water leak",
  rodents: "Signs of rodents",
  cockroaches: "Signs of cockroaches",
  exposed_wiring: "Exposed wiring",
  lead_paint: "Peeling or deteriorated paint (possible lead)",
  inadequate_heat: "Inadequate heat",
  carbon_monoxide: "Carbon monoxide concern",
  gas_leak: "Possible gas leak",
};

const hazards = new Map<number, Hazard>();
const runs = new Map<string, AgentRun>(); // key: agent|hazard
const expanded = new Set<string>();
const pending = new Set<number>(); // hazard ids with a confirm in flight
let status = "connecting";
let conn: DbConnection | null = null;

const runKey = (agent: string, hazard: string) => `${agent}|${hazard}`;

function esc(s: unknown): string {
  return String(s ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function sqlQuote(s: string): string {
  return s.replace(/'/g, "''");
}

// ---------- connection ----------

function connect() {
  const saved = localStorage.getItem(TOKEN_KEY) ?? undefined;
  conn = DbConnection.builder()
    .withUri(DB_URI)
    .withDatabaseName(DB_NAME)
    .withToken(saved)
    .withAutomaticReconnect()
    .onConnect((c, _identity, token) => {
      localStorage.setItem(TOKEN_KEY, token);
      status = "connected";
      render();
      c.subscriptionBuilder()
        .onApplied(() => {
          status = "live";
          render();
        })
        .onError((_ctx) => {
          status = "subscription error";
          render();
        })
        .subscribe([
          `SELECT * FROM hazard WHERE case_id = '${sqlQuote(CASE_ID)}'`,
          `SELECT * FROM agent_run WHERE case_id = '${sqlQuote(CASE_ID)}'`,
        ]);
    })
    .onConnectError((_ctx, err) => {
      status = `connection failed: ${err.message}`;
      render();
    })
    .onDisconnect(() => {
      status = "reconnecting";
      render();
    })
    .build();

  // onInsert also fires for the initial snapshot. onUpdate needs the primary key, which both tables have.
  conn.db.hazard.onInsert((_ctx: EventContext, row: Hazard) => {
    hazards.set(row.id, row);
    render();
  });
  conn.db.hazard.onUpdate((_ctx: EventContext, _old: Hazard, row: Hazard) => {
    hazards.set(row.id, row);
    pending.delete(row.id);
    render();
  });
  conn.db.hazard.onDelete((_ctx: EventContext, row: Hazard) => {
    hazards.delete(row.id);
    render();
  });
  conn.db.agentRun.onInsert((_ctx: EventContext, row: AgentRun) => {
    runs.set(runKey(row.agent, row.hazard), row);
    render();
  });
  conn.db.agentRun.onUpdate((_ctx: EventContext, _old: AgentRun, row: AgentRun) => {
    runs.set(runKey(row.agent, row.hazard), row);
    render();
  });
  conn.db.agentRun.onDelete((_ctx: EventContext, row: AgentRun) => {
    runs.delete(runKey(row.agent, row.hazard));
    render();
  });
}

async function confirm(id: number, confirmed: boolean) {
  if (!conn) return;
  pending.add(id);
  render();
  try {
    await conn.reducers.confirmHazard({ id, confirmed });
  } catch (e) {
    pending.delete(id);
    alert(`Could not save: ${(e as Error).message}`);
    render();
  }
}

// ---------- payload rendering ----------

type Claim = { text?: string };
type Source = { url?: string; title?: string; cite?: string; publisher?: string; id?: string };

function list(title: string, items: unknown): string {
  if (!Array.isArray(items) || items.length === 0) return "";
  const lis = items
    .map((c) => (typeof c === "string" ? c : (c as Claim).text))
    .filter(Boolean)
    .map((t) => `<li>${esc(t)}</li>`)
    .join("");
  return lis ? `<h4>${esc(title)}</h4><ul>${lis}</ul>` : "";
}

function sources(items: unknown): string {
  if (!Array.isArray(items) || items.length === 0) return "";
  const lis = (items as Source[])
    .map((s) => {
      const label = s.title ?? s.cite ?? s.url ?? "";
      const tail = s.publisher ? ` (${esc(s.publisher)})` : "";
      return s.url
        ? `<li><a href="${esc(s.url)}" target="_blank" rel="noopener">${esc(label)}</a>${tail}</li>`
        : `<li>${esc(label)}${tail}</li>`;
    })
    .join("");
  return `<h4>Sources</h4><ul class="sources">${lis}</ul>`;
}

function renderPayload(agent: string, run: AgentRun): string {
  let p: Record<string, unknown>;
  try {
    p = JSON.parse(run.payloadJson || "{}");
  } catch {
    return `<p class="muted">Output could not be read.</p>`;
  }
  if (p.not_assessed) {
    return `<p class="muted">Not assessed: ${esc(p.reason ?? "no reason given")}</p>`;
  }
  let html = "";
  if (p.urgent && p.urgent_message) {
    html += `<p class="urgent">${esc(p.urgent_message)}</p>`;
  }
  if (agent === "health") {
    html += list("Precautions", p.precautions);
    html += list("Health risks", p.health_risks);
    html += list("See a doctor if", p.see_a_doctor_if);
  } else if (agent === "legal") {
    html += list("What the landlord has to do", p.landlord_duty);
    html += list("What you can do", p.tenant_options);
    html += list("Deadlines", p.deadlines);
    html += list("Ask legal aid about", p.ask_legal_aid);
  } else {
    html += `<pre>${esc(JSON.stringify(p, null, 2))}</pre>`;
  }
  html += sources(p.sources);
  if (p.disclaimer) html += `<p class="disclaimer">${esc(p.disclaimer)}</p>`;
  return html || `<p class="muted">Nothing to show.</p>`;
}

// ---------- page ----------

function statusLabel(run: AgentRun | undefined): { cls: string; text: string } {
  if (!run) return { cls: "waiting", text: "waiting" };
  if (run.status === "done") return { cls: "done", text: "done" };
  if (run.status === "failed") return { cls: "failed", text: "failed" };
  if (run.status === "running") return { cls: "running", text: "running" };
  return { cls: "waiting", text: run.status };
}

function urgentFirst(h: Hazard): string {
  // Before any agent finishes, the hazard card still leads with the fixed safety message if one exists.
  for (const a of AGENTS) {
    const run = runs.get(runKey(a.name, h.hazard));
    if (run?.status !== "done") continue;
    try {
      const p = JSON.parse(run.payloadJson || "{}");
      if (p.urgent && p.urgent_message) return `<p class="urgent">${esc(p.urgent_message)}</p>`;
    } catch {
      /* ignore */
    }
  }
  return "";
}

function renderHazard(h: Hazard): string {
  const label = HAZARD_LABELS[h.hazard] ?? h.hazard.replace(/_/g, " ");
  const conf = Math.round(h.confidence * 100);
  const isPending = pending.has(h.id);
  const confirmBlock = h.confirmedByTenant
    ? `<span class="pill confirmed">Confirmed by you</span>`
    : `<button class="confirm" data-id="${h.id}" ${isPending ? "disabled" : ""}>${isPending ? "Saving..." : "Yes, this is what I see"}</button>
       <button class="dismiss" data-id="${h.id}" data-confirmed="false" ${isPending ? "disabled" : ""}>Not this</button>`;

  const agentRows = AGENTS.map((a) => {
    const run = runs.get(runKey(a.name, h.hazard));
    const st = statusLabel(run);
    const key = `${h.id}|${a.name}`;
    const open = expanded.has(key) && run?.status === "done";
    const body = open ? `<div class="payload">${renderPayload(a.name, run!)}</div>` : "";
    const err = run?.status === "failed" ? `<div class="payload error">${esc(run.error)}</div>` : "";
    const clickable = run?.status === "done" ? "clickable" : "";
    return `
      <div class="agent ${clickable}" data-key="${key}">
        <div class="agent-head">
          <span class="agent-name">${esc(a.label)}</span>
          <span class="status ${st.cls}">${st.text}</span>
          ${run?.status === "done" ? `<span class="chev">${open ? "▾" : "▸"}</span>` : ""}
        </div>
        ${body}${err}
      </div>`;
  }).join("");

  return `
    <section class="hazard ${h.confirmedByTenant ? "confirmed" : ""}">
      <header>
        <div>
          <h2>${esc(label)}</h2>
          <div class="meta">${esc(h.location)} · detected with ${conf}% confidence</div>
        </div>
        <div class="actions">${confirmBlock}</div>
      </header>
      ${h.confirmedByTenant ? urgentFirst(h) : ""}
      ${h.confirmedByTenant ? `<div class="agents">${agentRows}</div>` : `<p class="muted">Confirm this to get health and legal information for it.</p>`}
    </section>`;
}

function render() {
  const app = document.getElementById("app")!;
  const rows = [...hazards.values()].sort((a, b) => a.id - b.id);
  const total = rows.length;
  const doneRuns = rows.filter((h) => h.confirmedByTenant).length * AGENTS.length;
  const finished = [...runs.values()].filter((r) => r.status === "done").length;
  app.innerHTML = `
    <header class="top">
      <h1>Home</h1>
      <div class="case">Case <code>${esc(CASE_ID)}</code> · <span class="conn ${status === "live" ? "live" : ""}">${esc(status)}</span>
        ${doneRuns ? ` · ${finished}/${doneRuns} checks done` : ""}</div>
    </header>
    ${total === 0 ? `<p class="muted">${status === "live" ? "No hazards found for this case yet." : "Loading..."}</p>` : rows.map(renderHazard).join("")}
    <footer class="muted">Organized case information, not legal or medical advice. Demo data only.</footer>`;

  app.querySelectorAll<HTMLButtonElement>("button.confirm").forEach((b) =>
    b.addEventListener("click", () => confirm(Number(b.dataset.id), true)),
  );
  app.querySelectorAll<HTMLButtonElement>("button.dismiss").forEach((b) =>
    b.addEventListener("click", () => confirm(Number(b.dataset.id), false)),
  );
  app.querySelectorAll<HTMLElement>(".agent.clickable .agent-head").forEach((el) =>
    el.addEventListener("click", () => {
      const key = el.parentElement!.dataset.key!;
      expanded.has(key) ? expanded.delete(key) : expanded.add(key);
      render();
    }),
  );
}

render();
connect();
