import { useEffect, useState } from "react";
import type { Home } from "./api";

type Stage = {
  id: string;
  title: string;
  doing: string;
  done: string;
  wait: string;
};

const STAGES: Stage[] = [
  {
    id: "hazard",
    title: "Walkthrough",
    doing: "Watching the rooms, frame by frame",
    done: "Hazards marked on the walls and floors",
    wait: "Queued until the video is opened",
  },
  {
    id: "property",
    title: "The building",
    doing: "Searching public records for this address",
    done: "Owner and records compiled",
    wait: "Queued behind the parcel lookup",
  },
  {
    id: "plan",
    title: "Floor plan",
    doing: "Drawing the home from what we saw",
    done: "A walkable model of this unit",
    wait: "Waiting for rooms to be named",
  },
  {
    id: "health",
    title: "Health notes",
    doing: "Reading what these conditions can mean",
    done: "Cited risks and what to watch for",
    wait: "Starts after you confirm a hazard",
  },
  {
    id: "legal",
    title: "The law here",
    doing: "Searching the pack for this city",
    done: "Duties and next steps for this address",
    wait: "Starts after you confirm a hazard",
  },
];

function statusOf(runs: Record<string, string> | undefined, id: string) {
  return runs?.[id] || "waiting";
}

function clock(seconds: number) {
  const minutes = Math.floor(seconds / 60);
  const rest = seconds % 60;
  return `${String(minutes).padStart(2, "0")}:${String(rest).padStart(2, "0")}`;
}

const MARK: Record<string, string> = { done: "✓", running: "…", failed: "✕", waiting: "○" };
const WORD: Record<string, string> = { done: "Done", running: "Working", failed: "Failed", waiting: "Waiting" };

export function Analyze({ home }: { home: Home }) {
  const [elapsed, setElapsed] = useState(0);
  useEffect(() => {
    const time = window.setInterval(() => setElapsed((n) => n + 1), 1000);
    return () => window.clearInterval(time);
  }, []);
  const stages = STAGES.map((stage) => ({ ...stage, status: statusOf(home.runs, stage.id) }));
  const finished = stages.filter((stage) => stage.status === "done" || stage.status === "failed").length;
  const running = stages.filter((stage) => stage.status === "running");
  const active = running[0] || stages.find((stage) => stage.status === "waiting");
  return (
    <section className="analyze card">
      <div className="analyze-meta">
        <p className="kicker">Reading this home</p>
        <span aria-label={`${elapsed} seconds elapsed`}>{clock(elapsed)}</span>
      </div>
      <h2>{home.address || "Your address"}</h2>
      <p className="analyze-now" aria-live="polite">{active ? active.doing : "The walkthrough is ready."}</p>
      <div className={"analyze-bar" + (running.length ? " is-moving" : "")} role="progressbar" aria-label="Steps finished" aria-valuemin={0} aria-valuemax={stages.length} aria-valuenow={finished}>
        {/* Finished steps count fully, running ones half, so the bar grows as work starts, not only when it ends. */}
        <b style={{ width: `${Math.max(6, ((finished + running.length * 0.5) / stages.length) * 100)}%` }} />
      </div>
      <p className="note">
        {finished} of {stages.length} steps finished
        {running.length > 1 ? ` · ${running.length} running at once` : ""}
      </p>
      <ol className="analyze-stages">
        {stages.map((stage) => (
          <li key={stage.id} className={`is-${stage.status}`}>
            <span className="analyze-mark" aria-hidden="true">
              {stage.status === "running"
                ? <span className="dots"><i /><i /><i /></span>
                : MARK[stage.status] || "○"}
            </span>
            <div>
              <strong>{stage.title} <span className="sr-only">— {WORD[stage.status] || stage.status}</span></strong>
              <small>
                {stage.status === "running" && stage.doing}
                {stage.status === "waiting" && stage.wait}
                {stage.status === "done" && stage.done}
                {stage.status === "failed" && (home.errors?.[stage.id] || "This step could not finish")}
              </small>
            </div>
            <span className="analyze-word">{WORD[stage.status] || stage.status}</span>
          </li>
        ))}
      </ol>
    </section>
  );
}
