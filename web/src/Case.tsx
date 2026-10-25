import type { Home, Message, Tile } from "./api";
import { SeverityBadge } from "./severity";

/* The case at a glance: what to do next, where the case stands, and what was found.
   Everything here comes from /api/home. No agent names: the tenant sees steps, not plumbing. */

type Mark = "done" | "now" | "later";

type Step = { id: string; title: string; detail: string; mark: Mark };

function when(value?: string) {
  if (!value || value === "just now") return "just now";
  const stamp = Date.parse(value);
  if (!Number.isFinite(stamp)) return value;
  return new Date(stamp).toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

function firstSent(messages?: Message[]) {
  return (messages || []).find((item) => item.direction === "out");
}

function lastReply(messages?: Message[]) {
  const rows = messages || [];
  for (let i = rows.length - 1; i >= 0; i -= 1) {
    if (rows[i].direction === "in") return rows[i];
  }
  return undefined;
}

function tileById(home: Home, id: string) {
  return (home.tiles || []).find((tile) => tile.id === id);
}

export function caseSteps(home: Home): Step[] {
  const hazards = home.hazards || [];
  const confirmed = hazards.filter((h) => h.confirmed).length;
  const sent = firstSent(home.tracking?.messages);
  const reply = lastReply(home.tracking?.messages);
  const aid = tileById(home, "phone-legal-aid");
  const complaint = tileById(home, "form-complaint");
  const filed = (aid && aid.status === "done") || (complaint && complaint.status === "done");

  const steps: Step[] = [
    {
      id: "walkthrough",
      title: "Walkthrough reviewed",
      detail: hazards.length ? `${hazards.length} problem${hazards.length === 1 ? "" : "s"} found in ${home.address || "your home"}` : "Nothing was found in the walkthrough",
      mark: "done",
    },
    {
      id: "confirm",
      title: "Problems confirmed",
      detail: confirmed ? `${confirmed} of ${hazards.length} confirmed by you` : "Tap each problem and confirm what you see",
      mark: confirmed ? "done" : "later",
    },
    {
      id: "notice",
      title: "Written notice sent",
      detail: sent ? `Sent ${when(sent.at)} to ${sent.counterparty}` : "A formal notice from your own email",
      mark: sent ? "done" : "later",
    },
    {
      id: "reply",
      title: "Landlord reply",
      detail: reply ? `Replied ${when(reply.at)}` : sent ? "Waiting for a written reply" : "Tracked here after the notice",
      mark: reply ? "done" : "later",
    },
    {
      id: "aid",
      title: "Legal aid and complaint",
      detail: filed ? "You called legal aid or filed" : "If the landlord does not fix it",
      mark: filed ? "done" : "later",
    },
  ];
  const current = steps.find((step) => step.mark !== "done");
  if (current) current.mark = "now";
  return steps;
}

export type NextAction = { title: string; blurb: string; button: string; go: "hazard" | "steps" | "tracking" | ""; tile?: Tile; hazardId?: string };

export function nextAction(home: Home): NextAction {
  const hazards = home.hazards || [];
  const unconfirmed = hazards.find((h) => !h.confirmed);
  const sent = firstSent(home.tracking?.messages);
  const reply = lastReply(home.tracking?.messages);
  const tile = home.next?.tile_id ? tileById(home, home.next.tile_id) : undefined;
  if (tile) {
    return {
      title: tile.title,
      blurb: home.next?.blurb || "",
      button: tile.kind === "email" ? "Review and send" : tile.kind === "phone" ? "Review and call" : "Review the form",
      go: "steps",
      tile,
    };
  }
  if (home.next?.kind === "waiting" && sent && !reply) {
    return { title: "Waiting for the landlord", blurb: home.next.blurb, button: "See the thread", go: "tracking" };
  }
  if (unconfirmed && !hazards.some((h) => h.confirmed)) {
    return {
      title: "Confirm what you see",
      blurb: "Health and legal notes load for each problem you confirm. Nothing is sent until you say so.",
      button: `Open: ${unconfirmed.label}`,
      go: "hazard",
      hazardId: unconfirmed.hazard_id,
    };
  }
  if (unconfirmed) {
    return { title: "More to confirm", blurb: `${unconfirmed.label} in the ${unconfirmed.room} is not confirmed yet.`, button: "Open it", go: "hazard", hazardId: unconfirmed.hazard_id };
  }
  return { title: "Nothing to do right now", blurb: home.next?.blurb || "Check back when the landlord writes.", button: "", go: "" };
}

function initials(name: string) {
  return name.split(/\s+/).filter(Boolean).slice(0, 2).map((word) => word[0]?.toUpperCase() || "").join("") || "?";
}

export function Case({ home, onHazard, onStep, onScreen }: {
  home: Home;
  onHazard: (id: string) => void;
  onStep: (tile: Tile) => void;
  onScreen: (screen: "model" | "steps" | "tracking") => void;
}) {
  const steps = caseSteps(home);
  const action = nextAction(home);
  const landlord = home.landlord;
  const contact = landlord?.contact?.email || landlord?.contact?.phone || "";
  const done = steps.filter((step) => step.mark === "done").length;
  const hazards = home.hazards || [];
  const messages = (home.tracking?.messages || []).filter((m) => !m.role || m.role === "landlord");
  const stats = [
    { label: "Problems found", value: String(hazards.length) },
    { label: "Confirmed by you", value: `${hazards.filter((h) => h.confirmed).length} of ${hazards.length}` },
    { label: "Notices sent", value: String(messages.filter((m) => m.direction === "out").length) },
    { label: "Landlord replies", value: String(messages.filter((m) => m.direction === "in").length) },
  ];

  function go() {
    if (action.go === "hazard" && action.hazardId) onHazard(action.hazardId);
    else if (action.go === "steps" && action.tile) onStep(action.tile);
    else if (action.go === "tracking") onScreen("tracking");
  }

  return (
    <section className="dash" aria-labelledby="dash-title">
      <header className="dash-head">
        <p className="kicker">Your home</p>
        <h2 id="dash-title">{home.address || "Your home"}</h2>
      </header>

      <article className="dash-next">
        <div>
          <p className="kicker">Up next</p>
          <h3>{action.title}</h3>
          {action.blurb && <p>{action.blurb}</p>}
        </div>
        {action.button && <button className="primary" onClick={go}>{action.button}</button>}
      </article>

      <ul className="dash-stats" aria-label="Case at a glance">
        {stats.map((stat) => (
          <li key={stat.label}><b>{stat.value}</b><span>{stat.label}</span></li>
        ))}
      </ul>

      <div className="dash-grid">
        <article className="dash-card">
          <header className="dash-card-head">
            <h3>Progress</h3>
            <span className="status">{done} of {steps.length} done</span>
          </header>
          <ol className="dash-steps" aria-label="Case progress">
            {steps.map((step, i) => (
              <li key={step.id} className={`is-${step.mark}`}>
                <span className="dash-mark" aria-hidden="true">{step.mark === "done" ? "✓" : i + 1}</span>
                <div>
                  <strong>
                    {step.title}
                    <span className="dash-state">{step.mark === "done" ? "Done" : step.mark === "now" ? "Now" : "Later"}</span>
                  </strong>
                  <small>{step.detail}</small>
                </div>
              </li>
            ))}
          </ol>
        </article>

        <div className="dash-side">
          <article className="dash-card">
            <header className="dash-card-head">
              <h3>Problems</h3>
              <button className="ghost" onClick={() => onScreen("model")}>See the 3D model</button>
            </header>
            <div className="rooms">
              {hazards.map((h, i) => (
                <button key={h.hazard_id} className="hazard-btn" onClick={() => onHazard(h.hazard_id)}>
                  <span className="hazard-main">
                    <b className="hazard-num" aria-label={`Marker ${i + 1}`}>{i + 1}</b>
                    <span><strong>{h.label}</strong><span className="hazard-room">{h.room}{h.confirmed ? " · confirmed" : ""}</span></span>
                  </span>
                  <SeverityBadge level={h.severity} />
                </button>
              ))}
              {hazards.length === 0 && <p className="note">Nothing was found in the walkthrough.</p>}
            </div>
          </article>

          {landlord && (
            <article className="dash-card dash-landlord">
              <header className="dash-card-head"><h3>Landlord</h3></header>
              <div className="landlord-head">
                <span className="initials">{initials(landlord.company || "")}</span>
                <span>
                  <strong>{landlord.company}</strong>
                  {contact && <small>{contact}</small>}
                </span>
              </div>
              <div className="counts">
                <div className="count"><b>{landlord.open}</b><span>open records</span></div>
                <div className="count"><b>{landlord.closed}</b><span>closed records</span></div>
              </div>
              {home.runs?.property === "running" && <p className="note">Still looking up public records.</p>}
            </article>
          )}
        </div>
      </div>
    </section>
  );
}
