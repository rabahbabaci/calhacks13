import type { Home, Message, Tile } from "./api";

/* The case at a glance: what to do next, where the case stands, and what was found.
   Everything here comes from /api/home. No agent names: the tenant sees steps, not plumbing. */

type Mark = "done" | "now" | "later";

type Step = { id: string; title: string; detail: string; mark: Mark };

const SEV: Record<string, string> = { low: "var(--low)", moderate: "var(--moderate)", high: "var(--high)", urgent: "var(--urgent)" };

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

  function go() {
    if (action.go === "hazard" && action.hazardId) onHazard(action.hazardId);
    else if (action.go === "steps" && action.tile) onStep(action.tile);
    else if (action.go === "tracking") onScreen("tracking");
  }

  return (
    <section className="case">
      <article className="card case-next">
        <p className="step-kicker">Up next</p>
        <h2>{action.title}</h2>
        {action.blurb && <p>{action.blurb}</p>}
        {action.button && <button className="primary" onClick={go}>{action.button}</button>}
      </article>

      <article className="card">
        <header className="case-head">
          <h2>Your case</h2>
          <span className="status">{done} of {steps.length} done</span>
        </header>
        <ol className="case-steps" aria-label="Case progress">
          {steps.map((step) => (
            <li key={step.id} className={`is-${step.mark}`}>
              <span className="case-mark" aria-hidden="true" />
              <div>
                <strong>{step.title}</strong>
                <small>{step.detail}</small>
              </div>
            </li>
          ))}
        </ol>
      </article>

      <article className="card">
        <header className="case-head">
          <h2>Your home</h2>
          <button className="ghost" onClick={() => onScreen("model")}>See the 3D model</button>
        </header>
        <div className="rooms">
          {(home.hazards || []).map((h) => (
            <button key={h.hazard_id} className="hazard-btn" onClick={() => onHazard(h.hazard_id)}>
              <span><strong>{h.label}</strong><span>{h.room}{h.confirmed ? " · confirmed" : ""}</span></span>
              <em className="pill" style={{ background: SEV[h.severity] }}>{h.severity}</em>
            </button>
          ))}
        </div>
        {landlord && (
          <p className="note case-landlord">
            <b>{landlord.company}</b>
            {contact ? ` · ${contact}` : ""}
            {` · ${landlord.open} open, ${landlord.closed} closed public record${landlord.open + landlord.closed === 1 ? "" : "s"}`}
            {home.runs?.property === "running" ? " · still looking up records" : ""}
          </p>
        )}
      </article>
    </section>
  );
}
