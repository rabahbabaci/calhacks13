import { useEffect, useState } from "react";
import type { Home } from "./api";

type Stage = {
  id: string;
  title: string;
  doing: string;
  done: string;
  wait: string;
  notes: string[];
};

const STAGES: Stage[] = [
  {
    id: "hazard",
    title: "Walkthrough",
    doing: "Watching the rooms, frame by frame",
    done: "Hazards marked on the walls and floors",
    wait: "Queued until the video is opened",
    notes: [
      "Pulling stills from the walkthrough",
      "Looking for dampness, leaks, and peeling paint",
      "Naming each room as it appears",
      "Keeping only what is visible in a cited frame",
    ],
  },
  {
    id: "property",
    title: "The building",
    doing: "Searching public records for this address",
    done: "Owner and records compiled",
    wait: "Queued behind the parcel lookup",
    notes: [
      "Matching the parcel and mailing address",
      "Looking up the company that manages the building",
      "Reading code cases and complaints",
      "Finding a phone, email, or leasing office",
    ],
  },
  {
    id: "plan",
    title: "Floor plan",
    doing: "Drawing the home from what we saw",
    done: "A walkable model of this unit",
    wait: "Waiting for rooms to be named",
    notes: [
      "Laying out hallways and bedrooms",
      "Placing furniture by room type",
      "Putting discolorations on the cited walls",
      "Scaling the shell to the listing if we have one",
    ],
  },
  {
    id: "health",
    title: "Health notes",
    doing: "Reading what these conditions can mean",
    done: "Cited risks and what to watch for",
    wait: "Starts after you confirm a hazard",
    notes: [
      "Checking each confirmed hazard",
      "Copying only what the source pages state",
      "Listing when to see a doctor",
      "Finding nearby tests for this city",
    ],
  },
  {
    id: "legal",
    title: "The law here",
    doing: "Searching the pack for this city",
    done: "Duties and next steps for this address",
    wait: "Starts after you confirm a hazard",
    notes: [
      "Opening the statewide pack for the identified city",
      "Keeping landlord duties that cite a source",
      "Drafting the notice and complaint options",
      "Leaving out anything the pack says never to say",
    ],
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

export function Analyze({ home }: { home: Home }) {
  const [tick, setTick] = useState(0);
  const [elapsed, setElapsed] = useState(0);

  useEffect(() => {
    const notes = window.setInterval(() => setTick((n) => n + 1), 2400);
    const time = window.setInterval(() => setElapsed((n) => n + 1), 1000);
    return () => {
      window.clearInterval(notes);
      window.clearInterval(time);
    };
  }, []);

  const stages = STAGES.map((stage) => ({ ...stage, status: statusOf(home.runs, stage.id) }));
  const finished = stages.filter((stage) => stage.status === "done" || stage.status === "failed").length;
  const running = stages.filter((stage) => stage.status === "running");
  const active = running[0] || stages.find((stage) => stage.status === "waiting");
  const note = active ? active.notes[tick % active.notes.length] : "Opening the home.";
  const rooms = ["hall", "live", "bed", "bath"];

  return (
    <section className="analyze" aria-live="polite">
      <div className="analyze-sky" aria-hidden="true">
        <i className="analyze-dust" />
        <i className="analyze-dust is-two" />
        <div className="analyze-house">
          <b className="analyze-scan" />
          {rooms.map((room, index) => {
            const lit = finished > index || Boolean(active);
            return <i key={room} className={`analyze-room is-${room}${lit ? " is-on" : ""}`} style={{ animationDelay: `${index * 180}ms` }} />;
          })}
        </div>
      </div>
      <div className="analyze-meta">
        <p className="analyze-kicker">Reading this home</p>
        <span>{clock(elapsed)}</span>
      </div>
      <h2>{home.address || "Your address"}</h2>
      <p className="analyze-now">{active ? active.doing : "The walkthrough is ready."}</p>
      <p className="analyze-note">{note}</p>
      <div className="analyze-bar" role="progressbar" aria-valuemin={0} aria-valuemax={stages.length} aria-valuenow={finished}>
        <b style={{ width: `${Math.max(10, (finished / stages.length) * 100)}%` }} />
      </div>
      <p className="analyze-count">
        {finished} of {stages.length} finished
        {running.length > 1 ? ` · ${running.length} running together` : ""}
      </p>
      <ol className="analyze-stages">
        {stages.map((stage) => (
          <li key={stage.id} className={`is-${stage.status}`}>
            <span className="analyze-mark" aria-hidden="true" />
            <div>
              <strong>{stage.title}</strong>
              <small>
                {stage.status === "running" && stage.doing}
                {stage.status === "waiting" && stage.wait}
                {stage.status === "done" && stage.done}
                {stage.status === "failed" && (home.errors?.[stage.id] || "This step could not finish")}
              </small>
            </div>
          </li>
        ))}
      </ol>
    </section>
  );
}
