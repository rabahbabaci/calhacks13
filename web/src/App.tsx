import { useEffect, useRef, useState } from "react";
import { api, type ClinicGroup, type ClinicRow, type Clinics, type HazardDetail, type Home, type Me, type Message, type Preview, type StepGroup, type Tile } from "./api";
import { clearMailToken, googleRedirectUri, listLandlordMail, requestGoogleMailToken, sendThroughGmail, startGoogleSignIn, takeGoogleReturn } from "./googleSignIn";
import { Analyze } from "./Analyze";
import { Model } from "./Model";

const GOOGLE_CLIENT_ID = import.meta.env.VITE_GOOGLE_CLIENT_ID || "";

function GoogleMark() {
  return (
    <svg className="google-mark" viewBox="0 0 24 24" aria-hidden="true">
      <path fill="#4285F4" d="M23.5 12.3c0-.8-.1-1.6-.2-2.3H12v4.4h6.4c-.3 1.5-1.2 2.8-2.5 3.6v3h4.1c2.4-2.2 3.5-5.4 3.5-8.7z" />
      <path fill="#34A853" d="M12 24c3.2 0 5.9-1.1 7.9-2.9l-4.1-3c-1.1.8-2.6 1.2-3.8 1.2-2.9 0-5.4-2-6.3-4.6H1.5v3.1C3.5 21.4 7.5 24 12 24z" />
      <path fill="#FBBC05" d="M5.7 14.7c-.2-.7-.4-1.5-.4-2.2s.1-1.5.4-2.2V7.2H1.5C.7 8.8.2 10.4.2 12.5s.5 3.7 1.3 5.3l4.2-3.1z" />
      <path fill="#EA4335" d="M12 4.8c1.8 0 3.3.6 4.6 1.8l3.4-3.4C17.9 1.1 15.2 0 12 0 7.5 0 3.5 2.6 1.5 6.5l4.2 3.1C6.6 6.9 9.1 4.8 12 4.8z" />
    </svg>
  );
}

const SEV: Record<string, string> = { low: "var(--low)", moderate: "var(--moderate)", high: "var(--high)", urgent: "var(--urgent)" };

function shellLine(home: { shell?: { kind: string; source_url: string; square_feet?: number | null } }) {
  const shell = home.shell;
  if (!shell || shell.kind === "schematic") return "Built from scratch.";
  if (shell.kind === "listing") return `Built from scratch, scaled to ${shell.square_feet} sq ft.`;
  return shell.source_url ? `Floor plan from ${shell.source_url}.` : "Floor plan.";
}

type Screen = "model" | "steps" | "tracking" | "clinics";

const DEFECT_TESTS: Record<string, string[]> = {
  lead_paint: ["lead"],
  dampness_mold: ["mold", "allergy"],
  water_leak: ["mold", "allergy"],
  cockroaches: ["allergy"],
  rodents: ["allergy"],
  carbon_monoxide: ["carboxyhemoglobin"],
};

function parts(value?: string) {
  return (value || "").split(",").map((part) => part.trim()).filter(Boolean);
}

function titleCase(value: string) {
  return value.replace(/\w\S*/g, (word) => word.charAt(0).toUpperCase() + word.slice(1));
}

function clinicFacts(c: ClinicRow) {
  const assumed = parts(c.assumed);
  const labels: Record<string, string> = {
    "blood lead": "Blood lead",
    carboxyhemoglobin: "Carboxyhemoglobin",
    allergy: "Allergy",
    ent: "Ear, nose, and throat",
  };
  const tests = [...new Set([
    ...assumed.map((kind) => labels[kind] || "").filter(Boolean),
    ...parts(c.tests).map(titleCase),
  ])];
  let testsBasis = "";
  if (assumed.includes("blood lead")) testsBasis = "Usual for primary care";
  else if (assumed.includes("carboxyhemoglobin")) testsBasis = "Usual for a hospital emergency department";
  else if (assumed.includes("ent") || ((c.source_url || "").includes("npiregistry.cms.hhs.gov") && assumed.includes("allergy"))) {
    testsBasis = "NPI specialty listing";
  } else if (assumed.includes("allergy")) testsBasis = "Allergy clinic";
  else if (c.tests) testsBasis = "Stated on the source page";

  const insurance = [...new Set([...parts(c.assumed_insurance), ...parts(c.insurance)])];
  let insuranceBasis = "";
  if (c.assumed_insurance && (c.source_url || "").includes("findahealthcenter.hrsa.gov")) {
    insuranceBasis = "Usual for a health center";
  } else if (c.assumed_insurance && (c.source_url || "").includes("npiregistry.cms.hhs.gov")) {
    insuranceBasis = "Public enrollment file";
  } else if (c.insurance) insuranceBasis = "Stated on the source page";

  return { tests, testsBasis, insurance, insuranceBasis };
}

function HazardPhoto({ photo, tag }: { photo?: string; tag: string }) {
  const fallback = "/hazards/" + tag + ".svg";
  const [src, setSrc] = useState(photo || fallback);
  useEffect(() => {
    if (!photo) {
      setSrc(fallback);
      return;
    }
    let objectUrl = "";
    fetch(photo, { credentials: "include" })
      .then((res) => {
        if (!res.ok) throw new Error("no photo");
        return res.blob();
      })
      .then((blob) => {
        objectUrl = URL.createObjectURL(blob);
        setSrc(objectUrl);
      })
      .catch(() => setSrc(fallback));
    return () => {
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [photo, fallback]);
  return <img className="hazard-photo" src={src} alt="Cited walkthrough frame, zoomed on this hazard" />;
}

function HealthGroup({ title, items }: { title: string; items: string[] }) {
  const [open, setOpen] = useState(false);
  if (!items.length) return null;
  const limit = 3;
  const extra = items.length > limit;
  const shown = open || !extra ? items : items.slice(0, limit);
  return (
    <article className="info-tile health-group">
      <h3>{title}<span>{items.length}</span></h3>
      <ul>
        {shown.map((text) => <li key={text}>{text}</li>)}
      </ul>
      {extra && (
        <button className="ghost step-open" onClick={() => setOpen(!open)}>
          {open ? "Show less" : `Show ${items.length - limit} more`}
        </button>
      )}
    </article>
  );
}

function clinicNeedKeys(c: ClinicRow) {
  const keys = new Set<string>();
  for (const kind of parts(c.assumed)) {
    if (kind === "blood lead") keys.add("lead");
    else if (kind === "allergy" || kind === "ent") {
      keys.add("mold");
      keys.add("allergy");
    } else if (kind === "carboxyhemoglobin") keys.add("carboxyhemoglobin");
  }
  for (const test of parts(c.tests)) {
    const low = test.toLowerCase();
    keys.add(low);
    if (/\blead\b/.test(low)) keys.add("lead");
    if (/\bmold\b/.test(low)) keys.add("mold");
    if (/\ballergy\b/.test(low)) keys.add("allergy");
  }
  return keys;
}

function clinicMatchesTag(c: ClinicRow, tag: string) {
  const needed = DEFECT_TESTS[tag] || [];
  return needed.some((name) => clinicNeedKeys(c).has(name));
}

function clinicsStillWarming(clinics: Clinics) {
  if (!clinics.warming) return false;
  const grouped = clinics.groups || [];
  return !clinics.clinics.length && !grouped.some((group) => group.clinics.length);
}

function groupedClinics(home: Home, clinics: Clinics, focusId: string) {
  if (clinics.groups?.length) {
    return clinics.groups.map((group) => ({
      ...group,
      hazards: group.hazards || [],
      clinics: group.clinics || [],
    }));
  }
  const groups: ClinicGroup[] = [];
  const byTag = new Map<string, ClinicGroup>();
  let focus = "";
  for (const hazard of home.hazards || []) {
    const tag = hazard.hazard_id.split("|")[0];
    if (hazard.hazard_id === focusId) focus = tag;
    let group = byTag.get(tag);
    if (!group) {
      group = { tag, label: hazard.label, reason: "", hazards: [], clinics: [] };
      byTag.set(tag, group);
      groups.push(group);
    }
    group.hazards.push({
      hazard_id: hazard.hazard_id,
      room: hazard.room,
      location: hazard.location,
      label: hazard.label,
    });
  }
  for (const group of groups) {
    group.clinics = clinics.clinics.filter((row) => clinicMatchesTag(row, group.tag));
    if (focus && group.tag === focus && clinics.reason) group.reason = clinics.reason;
  }
  const kept = groups.filter((group) => group.clinics.length || (DEFECT_TESTS[group.tag] || []).length);
  kept.sort((a, b) => Number(b.tag === focus) - Number(a.tag === focus));
  return kept;
}

type MapPoint = { id: string; name: string; maps: string; lat: number; lon: number };

const GEO_CACHE: Record<string, { lat: number; lon: number } | null> = {};

function coordsFromMaps(url?: string) {
  if (!url) return null;
  try {
    const query = decodeURIComponent(new URL(url).searchParams.get("query") || "").trim();
    const match = query.match(/^(-?\d+(?:\.\d+)?),\s*(-?\d+(?:\.\d+)?)$/);
    if (!match) return null;
    const lat = Number(match[1]);
    const lon = Number(match[2]);
    if (Math.abs(lat) > 90 || Math.abs(lon) > 180) return null;
    return { lat, lon };
  } catch {
    return null;
  }
}

function coordsOf(c: ClinicRow) {
  if (typeof c.lat === "number" && typeof c.lon === "number" && Number.isFinite(c.lat) && Number.isFinite(c.lon)) {
    return { lat: c.lat, lon: c.lon };
  }
  return coordsFromMaps(c.maps);
}

async function geocodeAddress(address: string) {
  const key = address.trim();
  if (!key) return null;
  if (key in GEO_CACHE) return GEO_CACHE[key];
  const url = "https://geocode.arcgis.com/arcgis/rest/services/World/GeocodeServer/findAddressCandidates?"
    + new URLSearchParams({ f: "json", singleLine: key, countryCode: "USA", maxLocations: "1" });
  try {
    const res = await fetch(url);
    const data = await res.json();
    const loc = data?.candidates?.[0]?.location;
    const point = loc && typeof loc.y === "number" && typeof loc.x === "number"
      ? { lat: loc.y as number, lon: loc.x as number }
      : null;
    GEO_CACHE[key] = point;
    return point;
  } catch {
    GEO_CACHE[key] = null;
    return null;
  }
}

function project(lon: number, lat: number, zoom: number) {
  const n = 2 ** zoom;
  const x = ((lon + 180) / 360) * n;
  const rad = (lat * Math.PI) / 180;
  const y = (1 - Math.log(Math.tan(rad) + 1 / Math.cos(rad)) / Math.PI) / 2 * n;
  return { x, y };
}

function zoomFor(points: MapPoint[], width: number, height: number) {
  if (points.length <= 1) return 14;
  for (let zoom = 16; zoom >= 10; zoom -= 1) {
    const xs = points.map((p) => project(p.lon, p.lat, zoom).x);
    const ys = points.map((p) => project(p.lon, p.lat, zoom).y);
    if ((Math.max(...xs) - Math.min(...xs)) * 256 < width * 0.84
      && (Math.max(...ys) - Math.min(...ys)) * 256 < height * 0.84) {
      return zoom;
    }
  }
  return 10;
}

const TILE_LAYERS = [
  (z: number, x: number, y: number) => `https://tile.openstreetmap.org/${z}/${x}/${y}.png`,
  (z: number, x: number, y: number) => `https://a.tile.openstreetmap.org/${z}/${x}/${y}.png`,
  (z: number, x: number, y: number) => `https://b.tile.openstreetmap.org/${z}/${x}/${y}.png`,
];

function ClinicMap({ clinics }: { clinics: ClinicRow[] }) {
  const box = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(640);
  const [points, setPoints] = useState<MapPoint[]>([]);
  const [picked, setPicked] = useState("");
  const [layer, setLayer] = useState(0);
  const height = 280;

  useEffect(() => {
    const el = box.current;
    if (!el) return;
    const update = () => setWidth(el.clientWidth || 640);
    update();
    const obs = new ResizeObserver(update);
    obs.observe(el);
    return () => obs.disconnect();
  }, []);

  const roster = clinics.map((c) => [c.name, c.address, c.maps, c.lat, c.lon].join("|")).join("\n");
  useEffect(() => {
    let cancelled = false;
    const known: MapPoint[] = [];
    const pending: ClinicRow[] = [];
    const seen = new Set<string>();
    for (const c of clinics) {
      const id = (c.address || c.maps || c.name).trim();
      if (!id || seen.has(id)) continue;
      seen.add(id);
      const point = coordsOf(c);
      if (point) known.push({ id, name: c.name, maps: c.maps, ...point });
      else pending.push(c);
    }
    setPoints(known);
    setPicked("");
    setLayer(0);
    Promise.all(pending.map(async (c) => {
      const point = await geocodeAddress(c.address || "");
      if (!point) return null;
      return { id: (c.address || c.name).trim(), name: c.name, maps: c.maps, ...point };
    })).then((rows) => {
      if (cancelled) return;
      const extra = rows.filter((row): row is MapPoint => Boolean(row));
      if (extra.length) setPoints((cur) => [...cur, ...extra]);
    });
    return () => { cancelled = true; };
  }, [roster]);

  const zoom = zoomFor(points, width, height);
  const midLat = points.length ? points.reduce((sum, p) => sum + p.lat, 0) / points.length : 0;
  const midLon = points.length ? points.reduce((sum, p) => sum + p.lon, 0) / points.length : 0;
  const center = project(midLon, midLat, zoom);
  const originX = center.x * 256 - width / 2;
  const originY = center.y * 256 - height / 2;
  const n = 2 ** zoom;
  const tiles = [];
  if (points.length) {
    const x0 = Math.floor(originX / 256);
    const y0 = Math.floor(originY / 256);
    const x1 = Math.floor((originX + width) / 256);
    const y1 = Math.floor((originY + height) / 256);
    for (let x = x0; x <= x1; x += 1) {
      for (let y = y0; y <= y1; y += 1) {
        if (x < 0 || y < 0 || x >= n || y >= n) continue;
        tiles.push({
          key: `${layer}-${zoom}-${x}-${y}`,
          left: x * 256 - originX,
          top: y * 256 - originY,
          src: TILE_LAYERS[layer](zoom, x, y),
        });
      }
    }
  }

  return (
    <div ref={box} className={"clinic-map" + (points.length ? "" : " is-empty")}>
      {points.length === 0 && <p>Placing sites on the map.</p>}
      {tiles.map((tile) => (
        <img
          key={tile.key}
          className="clinic-tile"
          src={tile.src}
          alt=""
          referrerPolicy="no-referrer"
          style={{ left: tile.left, top: tile.top }}
          onError={() => setLayer((cur) => (cur + 1 < TILE_LAYERS.length ? cur + 1 : cur))}
        />
      ))}
      {points.map((point) => {
        const { x, y } = project(point.lon, point.lat, zoom);
        const active = picked === point.id;
        return (
          <button
            key={point.id}
            type="button"
            className={"clinic-dot" + (active ? " is-on" : "")}
            style={{ left: x * 256 - originX, top: y * 256 - originY }}
            aria-label={point.name}
            title={point.name}
            onClick={() => setPicked(active ? "" : point.id)}
          >
            {active ? <span>{point.name}</span> : null}
          </button>
        );
      })}
      {points.length > 0 && (
        <small className="clinic-map-note">© OpenStreetMap · {points.length} site{points.length === 1 ? "" : "s"}</small>
      )}
    </div>
  );
}

function ClinicCard({ c }: { c: ClinicRow }) {
  const facts = clinicFacts(c);
  return (
    <article className="tile">
      <h2>{c.name}</h2>
      <p>{c.address}</p>
      {c.maps && <p><a href={c.maps} target="_blank" rel="noreferrer">Google Maps</a></p>}
      <div className="clinic-facts">
        <ClinicFact label="Tests" value={facts.tests} basis={facts.testsBasis} />
        <ClinicFact label="Insurance" value={facts.insurance} basis={facts.insuranceBasis} />
        {c.funding ? <ClinicFact label="Funding" value={[c.funding]} basis="Stated on the source page" /> : null}
      </div>
      {c.source_url && <p><a href={c.source_url} target="_blank" rel="noreferrer">Source</a></p>}
    </article>
  );
}

function ClinicGroup({ group, startOpen }: { group: ClinicGroup; startOpen: boolean }) {
  const [open, setOpen] = useState(startOpen);
  const [more, setMore] = useState(false);
  const limit = 3;
  const rows = group.clinics || [];
  const hazards = group.hazards || [];
  const extra = rows.length > limit;
  const shown = more || !extra ? rows : rows.slice(0, limit);
  const places = hazards.map((item) => [item.room, item.location].filter(Boolean).join(", ")).join(" · ");
  return (
    <div className="step-group">
      <button
        type="button"
        className="clinic-toggle"
        aria-expanded={open}
        onClick={() => setOpen(!open)}
      >
        <span>
          <strong>{group.label}</strong>
          {places ? <span>{places}</span> : null}
        </span>
        <em>{rows.length}</em>
      </button>
      {open && (
        <>
          {group.reason ? <p>{group.reason}</p> : null}
          {rows.length === 0 && (
            <p className="note">No cited center matches a test this problem needs.</p>
          )}
          {rows.length > 0 && <ClinicMap clinics={rows} />}
          {shown.length > 0 && (
            <div className="tiles">
              {shown.map((c) => (
                <ClinicCard key={c.source_url + "|" + c.name + "|" + c.address} c={c} />
              ))}
            </div>
          )}
          {extra && (
            <button className="ghost step-open" onClick={() => setMore(!more)}>
              {more ? "Show less" : `Show ${rows.length - limit} more`}
            </button>
          )}
        </>
      )}
    </div>
  );
}

function ClinicsPage({ home, clinics, focusId, onBack }: { home: Home; clinics: Clinics; focusId: string; onBack: () => void }) {
  const groups = groupedClinics(home, clinics, focusId);
  return (
    <section>
      <button className="close" onClick={onBack}>Back</button>
      <h2>Nearby testing</h2>
      {clinics.note && <p className="note">{clinics.note}</p>}
      {clinics.warming && groups.length === 0 && (
        <p className="note">Still checking the health-center list for this ZIP.</p>
      )}
      {groups.map((group, index) => (
        <ClinicGroup key={group.tag} group={group} startOpen={index === 0} />
      ))}
      {groups.length === 0 && (clinics.clinics || []).length > 0 && (
        <div className="tiles">
          {(clinics.clinics || []).map((c) => (
            <ClinicCard key={c.source_url + "|" + c.name + "|" + c.address} c={c} />
          ))}
        </div>
      )}
      {clinics.search_url && (
        <p><a href={clinics.search_url} target="_blank" rel="noreferrer">Search health centers</a></p>
      )}
    </section>
  );
}

function ClinicFact({ label, value, basis }: { label: string; value: string[]; basis: string }) {
  return (
    <div className="clinic-fact">
      <span>{label}</span>
      <b>{value.length ? value.join(", ") : "Not listed"}</b>
      {value.length > 0 && basis ? <small>{basis}</small> : null}
    </div>
  );
}

function mailTime(row: { at?: string }) {
  const raw = row.at || "";
  if (!raw || raw === "just now") return Date.now();
  const stamp = Date.parse(raw);
  return Number.isFinite(stamp) ? stamp : Date.now();
}

function threadMessages(messages?: Message[]) {
  const rows = messages || [];
  const first = rows.find((item) => item.direction === "out");
  if (!first) return [];
  const start = mailTime(first);
  return rows
    .filter((item) => (!item.role || item.role === "landlord") && mailTime(item) >= start)
    .sort((left, right) => {
      const delta = mailTime(left) - mailTime(right);
      if (delta !== 0) return delta;
      return (left.direction === "out" ? 0 : 1) - (right.direction === "out" ? 0 : 1);
    });
}

function stepKind(tile: Tile) {
  if (tile.id === "phone-legal-aid") return "Call";
  if (tile.id.startsWith("record|")) return "City record";
  if (tile.id === "email-followup") {
    if (/demand/i.test(tile.title)) return "Demand";
    if (/confirm/i.test(tile.title)) return "Confirm";
    if (/schedule/i.test(tile.title)) return "Schedule";
    if (/record/i.test(tile.title)) return "Record";
    if (/information/i.test(tile.title)) return "Reply";
    return "Reply";
  }
  if (tile.kind === "email") return "Notice";
  if (tile.kind === "phone") return "Call";
  return "City form";
}

function stepBlurb(tile: Tile, started: boolean) {
  if (tile.why) return tile.why;
  if (tile.id === "email-followup") return "The landlord wrote back. Reply in writing so the record stays complete.";
  if (tile.kind === "email" && tile.status === "done") return "This written notice was sent. Waiting for a reply. The thread is on Tracking.";
  if (tile.kind === "email" && started) return "Send written notice of this condition. It continues the thread on Tracking.";
  if (tile.kind === "email") return "Send written notice of this condition. Nothing is sent until you press Send.";
  if (tile.id === "phone-legal-aid") return "Call the listed legal aid number. This only dials. Confirm eligibility at intake. Filing comes after this call, if you decide to file.";
  if (tile.kind === "phone") return "Call for this urgent problem. This only dials.";
  if (tile.id.startsWith("record|")) return "The city already has this record. Confirm a problem on Home and it is included in the complaint step. This is not a second form.";
  if (tile.id === "form-complaint") return "File under the cited statute or city complaint after you have called legal aid and decided to file.";
  return "File one city complaint. Include any open city record listed in the draft. Opening the form does not file it.";
}

function stepAction(tile: Tile) {
  if (tile.id === "email-followup") return /demand/i.test(tile.title) ? "Review and send the demand" : "Review and send the reply";
  if (tile.kind === "email") return tile.status === "done" ? "Review the notice" : "Review and send the notice";
  if (tile.kind === "phone") return "Review and call";
  if (tile.id.startsWith("record|")) return "See the record";
  return tile.status === "done" ? "Review the packet" : "Review the form";
}

function landlordEmails(home: Home, extra?: string) {
  const people = [home.landlord?.contact, home.landlord?.owner, home.landlord?.manager];
  return [...new Set([...people.map((person) => person?.email || ""), extra || ""].map((item) => item.trim()).filter(Boolean))];
}

function tileHazardId(tile: Tile) {
  return tile.hazard_id || (tile.id.includes("|") ? tile.id.split("|").slice(1).join("|") : "");
}

function isFlowTile(tile: Tile) {
  return tile.id === "form-complaint" || tile.id === "phone-legal-aid" || tile.id === "email-followup";
}

function visibleTiles(home: Home) {
  const tiles = home.tiles || [];
  const suggestedId = home.next?.tile_id || "";
  return tiles.filter((tile) =>
    tile.id !== "log-reply"
    && tile.id !== "evidence-photos"
    && tile.kind !== "evidence"
    && !(tile.id.startsWith("record|") && tiles.some((item) => item.id === "form-complaint"))
    && (!isFlowTile(tile) || tile.id === suggestedId || tile.status === "done")
  );
}

function groupedSteps(home: Home) {
  const tiles = visibleTiles(home);
  const suggestedId = home.next?.tile_id || "";
  const suggested = tiles.find((tile) => tile.id === suggestedId) || null;
  const used = new Set<string>();
  if (suggested) used.add(suggested.id);
  const groups = (home.groups || []).map((group) => {
    const ids = new Set(group.hazards.map((item) => item.hazard_id));
    const actions = tiles.filter((tile) => !used.has(tile.id) && ids.has(tileHazardId(tile)));
    actions.forEach((tile) => used.add(tile.id));
    return { group, actions };
  });
  return { groups, shared: tiles.filter((tile) => !used.has(tile.id)), suggested };
}

function stepPlace(tile: Tile, group?: StepGroup) {
  const hid = tileHazardId(tile);
  const place = group?.hazards.find((item) => item.hazard_id === hid);
  if (!place) return "";
  return [place.room, place.location].filter(Boolean).join(" · ");
}

export function App() {
  const [signedIn, setSignedIn] = useState(false);
  const [me, setMe] = useState<Me | null>(null);
  const [live, setLive] = useState(false);
  const [signingIn, setSigningIn] = useState(false);
  const [home, setHome] = useState<Home | null>(null);
  const [screen, setScreen] = useState<Screen>("model");
  const [error, setError] = useState("");
  const [address, setAddress] = useState("123 Example St, Oakland, CA 94612");
  const [file, setFile] = useState<File | null>(null);
  const [openId, setOpenId] = useState<string | null>(null);
  const [detail, setDetail] = useState<HazardDetail | null>(null);
  const [clinics, setClinics] = useState<Clinics | null>(null);
  const [clinicFocus, setClinicFocus] = useState("");
  const [finding, setFinding] = useState(false);
  const [preview, setPreview] = useState<{ id: string; preview: Preview } | null>(null);
  const [to, setTo] = useState("");
  const [sending, setSending] = useState(false);

  async function refresh() {
    const next = await api.home();
    setHome(next);
    return next;
  }

  async function syncLandlordMail(nextHome?: Home | null) {
    const current = nextHome || home;
    const thread = threadMessages(current?.tracking?.messages);
    const first = thread.find((item) => item.direction === "out");
    const addresses = current ? landlordEmails(current, to) : [];
    if (!GOOGLE_CLIENT_ID || !addresses.length || !first) return;
    try {
      const token = await requestGoogleMailToken();
      const messages = await listLandlordMail(token, addresses, new Date(mailTime(first)));
      if (!messages.length) return;
      setHome(await api.mail(messages));
    } catch {
      return;
    }
  }

  async function finishSignIn() {
    const next = await api.me();
    setMe(next);
    setLive(Boolean(next.live));
    if (!next.signed_in) throw new Error("Sign-in did not stick. Try again, or continue as a demo tenant.");
    await refresh();
    setSignedIn(true);
  }

  useEffect(() => {
    const returning = takeGoogleReturn();
    api.me().then(async (next) => {
      setMe(next);
      setLive(Boolean(next.live));
      if (returning.error) {
        setError(returning.error);
        return;
      }
      if (returning.token) {
        await loginWithGoogle(returning.token);
        return;
      }
      if (!next.signed_in) return;
      await refresh();
      setSignedIn(true);
    }).catch((err) => setError(String(err.message || err)));
  }, []);

  async function startGoogle() {
    setError("");
    setSigningIn(true);
    try {
      await api.logout().catch(() => undefined);
      clearMailToken();
      setSignedIn(false);
      setHome(null);
      startGoogleSignIn({ mail: true });
    } catch (err) {
      setSigningIn(false);
      setError(String((err as Error).message || err));
    }
  }

  async function startGoogleIdentityOnly() {
    setError("");
    setSigningIn(true);
    try {
      await api.logout().catch(() => undefined);
      clearMailToken();
      setSignedIn(false);
      setHome(null);
      startGoogleSignIn({ mail: false });
    } catch (err) {
      setSigningIn(false);
      setError(String((err as Error).message || err));
    }
  }

  async function login() {
    setError("");
    setSigningIn(true);
    try {
      await api.login();
      await finishSignIn();
    } catch (err) {
      setError(String((err as Error).message || err));
      setSignedIn(false);
    } finally {
      setSigningIn(false);
    }
  }

  async function loginWithGoogle(credential: string) {
    setError("");
    setSigningIn(true);
    try {
      await api.google(credential);
      await finishSignIn();
    } catch (err) {
      setError(String((err as Error).message || err));
      setSignedIn(false);
    } finally {
      setSigningIn(false);
    }
  }

  async function logout() {
    setError("");
    await api.logout();
    window.google?.accounts.id.disableAutoSelect();
    clearMailToken();
    setSignedIn(false);
    setMe(null);
    setLive(false);
    setHome(null);
    setOpenId(null);
    setDetail(null);
    setScreen("model");
  }

  async function onboard() {
    setError("");
    const next = await api.onboard(address, file);
    setHome(next);
    setScreen("model");
  }

  function closeHazard() {
    setOpenId(null);
    setDetail(null);
  }

  async function openHazard(id: string) {
    setOpenId(id);
    setScreen("model");
    setDetail(await api.hazard(id));
  }

  async function findTesting(id: string) {
    setError("");
    setFinding(true);
    setClinicFocus(id);
    closeHazard();
    setScreen("clinics");
    setClinics({
      note: "Looking up testing sites near this address.",
      reason: "",
      search_url: "",
      clinics: [],
      groups: [],
      warming: true,
    });
    try {
      let next = await api.clinics(id);
      setClinics(next);
      for (let i = 0; i < 10 && clinicsStillWarming(next); i += 1) {
        await new Promise((resolve) => window.setTimeout(resolve, 1200));
        next = await api.clinics(id);
        setClinics(next);
      }
    } catch (err) {
      setError(String((err as Error).message || err));
    } finally {
      setFinding(false);
    }
  }

  async function confirm() {
    if (!openId) return;
    setDetail(await api.confirm(openId));
    await refresh();
  }

  async function showPreview(tile: Tile) {
    const result = await api.preview(tile.id);
    setPreview({ id: tile.id, preview: result.preview });
    setTo(result.preview.to || "");
  }

  async function send() {
    if (!preview) return;
    const dest = to.trim();
    if (!dest) {
      setError("Enter the landlord email first.");
      return;
    }
    setError("");
    setSending(true);
    try {
      const draft = preview.preview;
      if (!GOOGLE_CLIENT_ID) {
        throw new Error("Google is not configured, so this app cannot send Gmail.");
      }
      const token = await requestGoogleMailToken();
      await sendThroughGmail(token, dest, draft.subject, draft.body);
      await api.send(preview.id, dest);
      setPreview(null);
      const next = await refresh();
      await syncLandlordMail(next);
    } catch (err) {
      setError(String((err as Error).message || err));
    } finally {
      setSending(false);
    }
  }

  const runState = JSON.stringify(home?.runs || {});
  useEffect(() => {
    if (!home || home.needs_onboarding) return;
    const busy = home.analyzing || Object.values(home.runs || {}).some((status) => status === "running");
    if (!busy) return;
    const timer = window.setInterval(() => {
      refresh().then(() => {
        if (openId && screen === "model") return api.hazard(openId).then(setDetail);
      }).catch((err) => setError(String(err.message || err)));
    }, 2500);
    return () => window.clearInterval(timer);
  }, [runState, openId, screen]);

  const analyzing = Boolean(home?.analyzing);
  const hasCase = Boolean(home && !home.needs_onboarding && home.layout);
  const mailKey = home && hasCase ? landlordEmails(home, to).join(",") : "";
  useEffect(() => {
    if (!hasCase || !mailKey) return;
    syncLandlordMail();
    const timer = window.setInterval(() => { syncLandlordMail(); }, 30000);
    return () => window.clearInterval(timer);
  }, [hasCase, mailKey, screen]);
  const showAnalyze = analyzing && screen === "model";
  const showModel = hasCase && screen === "model" && !analyzing;
  const colorOf = (id: string) => {
    const hazard = home?.hazards?.find((h) => h.hazard_id === id);
    return ({ low: "#3d8b6e", moderate: "#a6842c", high: "#d06a2b", urgent: "#b1332a" })[hazard?.severity || "moderate"] || "#a6842c";
  };
  const labelOf = (id: string) => home?.hazards?.find((h) => h.hazard_id === id)?.label || "Hazard";

  return (
    <div className="app">
      <header className="top">
        <h1>Home</h1>
        {signedIn && (
          <div className="top-actions">
            {hasCase && (
              <nav className="nav-tabs">
                <button aria-current={screen === "model" ? "page" : undefined} onClick={() => { closeHazard(); setScreen("model"); }}>Home</button>
                <button aria-current={screen === "steps" ? "page" : undefined} onClick={() => { setPreview(null); closeHazard(); setScreen("steps"); }}>Next steps</button>
                <button aria-current={screen === "tracking" ? "page" : undefined} onClick={() => { closeHazard(); setScreen("tracking"); }}>Tracking</button>
                {clinics && (
                  <button aria-current={screen === "clinics" ? "page" : undefined} onClick={() => { closeHazard(); setScreen("clinics"); }}>Nearby testing</button>
                )}
              </nav>
            )}
            <button className="ghost" onClick={logout}>Sign out</button>
          </div>
        )}
      </header>
      <main>
        {error && (
          <p className="error">
            {error}
            {/Gmail API has not been used|it is disabled/i.test(error) && (
              <>
                {" "}
                <a href="https://console.developers.google.com/apis/api/gmail.googleapis.com/overview?project=196493991248" target="_blank" rel="noreferrer">Enable the Gmail API</a>
                , wait a minute, then press Send again.
              </>
            )}
          </p>
        )}
        {!signedIn && (
          <section className="card login">
            <h2>Sign in</h2>
            <p>See what is going on in the place you rent, then decide what to do next.</p>
            <div className="auth-actions">
              {GOOGLE_CLIENT_ID && me?.google && (
                <>
                  <button className="auth-btn auth-google" type="button" onClick={startGoogle} disabled={signingIn}>
                    <GoogleMark />
                    Continue with Google
                  </button>
                  <button className="auth-btn auth-demo" type="button" onClick={startGoogleIdentityOnly} disabled={signingIn}>
                    Sign in without Gmail
                  </button>
                </>
              )}
              {me?.dev_login && (
                <button className="auth-btn auth-demo" type="button" onClick={login} disabled={signingIn}>
                  Continue as a demo tenant
                </button>
              )}
            </div>
            {signingIn && <p className="note">Signing you in…</p>}
            {GOOGLE_CLIENT_ID && me?.google && (
              <>
                <p className="note">Continue with Google asks to send and read landlord mail. Because Home is still in Google testing, the account you use must be listed as a test user. Add <code>divitchopra25@gmail.com</code> under APIs &amp; Services → OAuth consent screen → Audience → Test users, then try again.</p>
                <p className="note">If Google still blocks Gmail, use Sign in without Gmail. You can use the app, but Next steps cannot send until Gmail is granted.</p>
                <p className="note">If Google says <code>redirect_uri_mismatch</code>, add this exact URI under Authorized redirect URIs (not only JavaScript origins): <code>{googleRedirectUri()}</code></p>
              </>
            )}
            {me?.dev_login && (
              <p className="note">Demo sign-in stays on this computer. No password, and nothing is sent to Google.</p>
            )}
            {me && !me.dev_login && !(GOOGLE_CLIENT_ID && me.google) && (
              <p className="note">Sign-in is not set up: set GOOGLE_CLIENT_ID on the server and VITE_GOOGLE_CLIENT_ID in web/.env.local, or HOME_DEV_LOGIN=1 for the demo tenant.</p>
            )}
          </section>
        )}
        {signedIn && !home && !error && <p className="note">Opening your home…</p>}
        {signedIn && showAnalyze && home && <Analyze home={home} />}
        {signedIn && home?.needs_onboarding && (
          <section className="card">
            <h2>Your home</h2>
            <label className="field">Address
              <input value={address} onChange={(e) => setAddress(e.target.value)} />
            </label>
            <label className="field">Video of the home
              <input type="file" accept="video/*,image/*" capture="environment"
                onChange={(e) => setFile(e.target.files?.[0] || null)} />
            </label>
            <button className="primary" onClick={onboard}>Start the analysis</button>
            <p className="note">{live
              ? "Each sign-in starts from scratch. We watch the walkthrough, look up the building, and draw the home. You confirm each hazard before health and legal notes load."
              : "The file is stored on this computer. The rooms and icons come from a saved walkthrough, so this step does not call Claude."}</p>
          </section>
        )}
        {showModel && home && (
          <>
            <p className="note">{home.address}. Tap a colored icon, or a room below.</p>
            <p className="note">{shellLine(home)}</p>
            {home.runs?.property === "running" && <p className="note">Looking up the landlord and public records for this address.</p>}
            {home.runs?.health === "running" && <p className="note">Loading cited health notes for the problem you confirmed.</p>}
            {home.runs?.legal === "running" && <p className="note">Loading cited legal notes for the problem you confirmed.</p>}
            {home.errors?.property && <p className="error">{home.errors.property}</p>}
            <Model rooms={home.layout!.rooms} colorOf={colorOf} labelOf={labelOf} onPick={openHazard} />
            <div className="rooms">
              {home.hazards!.map((h) => (
                <button key={h.hazard_id} className="hazard-btn" onClick={() => openHazard(h.hazard_id)}>
                  <span><strong>{h.label}</strong><span>{h.room}</span></span>
                  <em className="pill" style={{ background: SEV[h.severity] }}>{h.severity}</em>
                </button>
              ))}
            </div>
          </>
        )}
        {hasCase && screen === "steps" && home && (
          <StepsPage
            home={home}
            preview={preview}
            to={to}
            setTo={setTo}
            sending={sending}
            showPreview={showPreview}
            send={send}
            setPreview={setPreview}
            refresh={refresh}
          />
        )}
        {hasCase && screen === "clinics" && clinics && home && (
          <ClinicsPage home={home} clinics={clinics} focusId={clinicFocus} onBack={() => setScreen("model")} />
        )}
        {hasCase && screen === "tracking" && home && (
          <section className="card">
            <h2>Evidence</h2>
            <label className="field">Add a photo or clip
              <input type="file" accept="video/*,image/*" capture="environment" onChange={async (e) => {
                const picked = e.target.files?.[0];
                if (picked) setHome(await api.evidence(picked));
              }} />
            </label>
            {(home.tracking?.evidence || []).map((item) => (
              <p key={item.evidence_id} className="msg">{item.evidence_id} · {item.kind} · {item.at}</p>
            ))}
            {(home.tracking?.evidence || []).length === 0 && <p className="note">No extra evidence yet.</p>}
            <h2>Correspondence</h2>
            <Correspondence messages={home.tracking?.messages} />
          </section>
        )}
      </main>
      {detail && (
        <div className="sheet-back" onClick={closeHazard}>
          <aside className="sheet" onClick={(e) => e.stopPropagation()} role="dialog" aria-label={detail.hazard.label}>
            <button className="close" onClick={closeHazard}>Close</button>
            {detail.hazard.urgent_message && <p className="urgent">{detail.hazard.urgent_message}</p>}
            <HazardPhoto photo={detail.hazard.photo} tag={detail.hazard.hazard_id.split("|")[0]} />
            {!detail.hazard.confirmed && (
              <div className="sheet-ask">
                <p>Is this what you see in the home? Health and legal notes load after you confirm.</p>
                <button className="primary" onClick={confirm}>This is in my home</button>
              </div>
            )}
            <button className="primary" onClick={() => findTesting(detail.hazard.hazard_id)} disabled={finding}>
              {finding ? "Finding nearby tests…" : "Find testing nearby"}
            </button>
            <h2>{detail.hazard.label}</h2>
            <p><em className="pill" style={{ background: SEV[detail.hazard.severity] }}>{detail.hazard.severity}</em></p>
            <section className="block">
              <article className="info-tile">
                <h3>Where <span className="status">{detail.blocks.place.status}</span></h3>
                <p>{detail.hazard.room}</p>
                <p>{detail.hazard.location}</p>
                <p className="cite">Frame {detail.hazard.frame}</p>
              </article>
            </section>
            <section className="block">
              <h3>Health <span className="status">{detail.blocks.health.status}</span></h3>
              {detail.blocks.health.status === "waiting" && (
                <p>Confirm this is in your home. The health notes load after that.</p>
              )}
              {detail.blocks.health.status === "running" && <p>Looking up cited health notes for this problem.</p>}
              {detail.blocks.health.status === "failed" && (
                <>
                  <p className="error">{detail.blocks.health.error || "Health notes could not be loaded."}</p>
                  <button className="primary" onClick={confirm}>This is in my home</button>
                </>
              )}
              {detail.blocks.health.status === "done" && (
                <>
                  <HealthGroup title="Risks" items={detail.blocks.health.risks} />
                  <HealthGroup title="What to do now" items={detail.blocks.health.precautions} />
                  <HealthGroup
                    title="See a clinician if"
                    items={detail.blocks.health.see_a_doctor?.length ? detail.blocks.health.see_a_doctor : (detail.blocks.health.see_a_doctor_if ? [detail.blocks.health.see_a_doctor_if] : [])}
                  />
                  <p className="disclaimer">{detail.blocks.health.disclaimer}</p>
                </>
              )}
            </section>
            <section className="block">
              <h3>This landlord <span className="status">{detail.blocks.landlord.status}</span></h3>
              {detail.blocks.landlord.status === "running" && <p>Looking up public records for this building.</p>}
              {detail.blocks.landlord.status === "failed" && <p className="error">{detail.blocks.landlord.error || "Public records could not be loaded."}</p>}
              <article className="info-tile landlord-head">
                {detail.blocks.landlord.logo_url
                  ? <img className="logo" src={detail.blocks.landlord.logo_url} alt="" />
                  : <span className="initials">{detail.blocks.landlord.initials}</span>}
                <strong>{detail.blocks.landlord.company}</strong>
              </article>
              <div className="counts">
                <div className="count"><b>{detail.blocks.landlord.open}</b><span>open</span></div>
                <div className="count"><b>{detail.blocks.landlord.closed}</b><span>closed</span></div>
              </div>
              {detail.blocks.landlord.records.length === 0 && <p>No matching public record.</p>}
              <RecordTiles records={detail.blocks.landlord.records} />
            </section>
            <section className="block">
              <h3>The law <span className="status">{detail.blocks.law.status}</span></h3>
              {detail.blocks.law.status === "waiting" && <p>Confirm this is in your home. The citations load after that.</p>}
              {detail.blocks.law.status === "running" && <p>Looking up cited legal notes for this problem.</p>}
              {detail.blocks.law.status === "failed" && (
                <>
                  <p className="error">{detail.blocks.law.error || "Legal notes could not be loaded."}</p>
                  <button className="primary" onClick={confirm}>This is in my home</button>
                </>
              )}
              {detail.blocks.law.status === "done" && (
                <>
                  <div className="counts">
                    <div className="count"><b>{detail.blocks.law.duties.length}</b><span>duties</span></div>
                  </div>
                  {detail.blocks.law.duties.map((row) => (
                    <article key={row.text} className="law-tile"><span className="kind">Duty</span><p>{row.text}</p><span className="cite">{row.cites.join(" · ")}</span></article>
                  ))}
                  <p className="note">What you can do is on Next steps.</p>
                  <p className="disclaimer">{detail.blocks.law.disclaimer}</p>
                </>
              )}
            </section>
            {!detail.hazard.confirmed && (
              <div className="sheet-confirm">
                <button className="primary" onClick={confirm}>This is in my home</button>
              </div>
            )}
          </aside>
        </div>
      )}
    </div>
  );
}

function StepsPage({
  home, preview, to, setTo, sending, showPreview, send, setPreview, refresh,
}: {
  home: Home;
  preview: { id: string; preview: Preview } | null;
  to: string;
  setTo: (v: string) => void;
  sending: boolean;
  showPreview: (tile: Tile) => void;
  send: () => void;
  setPreview: (v: { id: string; preview: Preview } | null) => void;
  refresh: () => Promise<Home | void>;
}) {
  const { groups, shared, suggested } = groupedSteps(home);
  const thread = threadMessages(home.tracking?.messages);
  const started = thread.some((item) => item.direction === "out");
  const lastIn = Boolean(started && thread[thread.length - 1]?.direction === "in");
  let n = 0;
  const renderTile = (tile: Tile, about: string) => {
    n += 1;
    const index = n;
    return (
      <article key={tile.id} className="tile">
        <header>
          <p className="step-kicker">{index}. {stepKind(tile)}</p>
          <span className={"status" + (tile.status === "done" ? " is-done" : "")}>{tile.status === "done" ? "Done" : "Ready"}</span>
        </header>
        <h2>{tile.title}</h2>
        {about ? <p className="note">{about}</p> : null}
        <p>{stepBlurb(tile, started)}</p>
        {tile.changed && <p className="note">{tile.changed}</p>}
        <button className="ghost step-open" onClick={() => showPreview(tile)}>{stepAction(tile)}</button>
        {preview?.id === tile.id && (
          <div className="preview">
            <PreviewBody preview={preview.preview} to={to} setTo={setTo} showTo={tile.kind === "email"} />
            <div className="row">
              {tile.kind === "email" && tile.status !== "done" && (
                <>
                  <button className="send" onClick={send} disabled={sending}>{sending ? "Sending…" : "Send"}</button>
                  <p className="note">{tile.id === "email-followup"
                    ? "This reply is added to the Tracking thread after you send it."
                    : started
                      ? "This continues the written record. Only mail after your first notice is stored on Tracking."
                      : "This starts the written record. Nothing else with the landlord is stored until you send."}</p>
                </>
              )}
              {tile.kind === "form" && !tile.id.startsWith("record|") && preview.preview.form?.url && (
                <a className="primary" href={preview.preview.form.url} target="_blank" rel="noreferrer">Open form</a>
              )}
              {tile.kind === "form" && !tile.id.startsWith("record|") && (
                <a className="primary" href={"/api/tiles/" + encodeURIComponent(tile.id) + "/packet.pdf"}>Download</a>
              )}
              {tile.kind === "phone" && preview.preview.phone && <a className="primary" href={"tel:" + preview.preview.phone}>Call {preview.preview.phone}</a>}
              {tile.id === "phone-legal-aid" && preview.preview.form?.url && (
                <a className="primary" href={preview.preview.form.url} target="_blank" rel="noreferrer">Open listing</a>
              )}
              {tile.id === "phone-legal-aid" && tile.status !== "done" && (
                <button className="ghost" style={{ color: "#1c211e", borderColor: "var(--line)" }} onClick={async () => { await api.filed(tile.id); setPreview(null); await refresh(); }}>I called this</button>
              )}
              {tile.kind === "form" && !tile.id.startsWith("record|") && tile.status !== "done" && (
                <button className="ghost" style={{ color: "#1c211e", borderColor: "var(--line)" }} onClick={async () => { await api.filed(tile.id); setPreview(null); await refresh(); }}>I filed this</button>
              )}
            </div>
          </div>
        )}
      </article>
    );
  };

  return (
    <section>
      <h2>Next steps</h2>
      <p className="note">{home.next?.blurb
        || (started
          ? (lastIn
            ? "The landlord replied. The next step follows what they wrote."
            : "Your notice was sent. Wait for a written reply. Sent and received mail after that first send is on Tracking.")
          : "Each card is something you can do. The hazard sheet only lists what the landlord has to do. Home does not send or file until you press the button.")}</p>
      {suggested && (
        <div className="step-group">
          <h3>Up next</h3>
          {renderTile(suggested, suggested.kind === "email" ? stepPlace(suggested, groups.find((row) => row.group.hazards.some((item) => item.hazard_id === tileHazardId(suggested)))?.group) : "")}
        </div>
      )}
      {groups.length === 0 && shared.length === 0 && !suggested && (
        <p className="note">Confirm a problem on Home first. After you tap “This is in my home”, the steps for that problem show up here.</p>
      )}
      {groups.length === 0 && shared.length > 0 && !shared.some((tile) => tile.kind === "email") && !suggested && (
        <p className="note">The city already has an open record. Confirm a problem on Home to add the landlord email, the fix options, and one complaint form that includes that record.</p>
      )}
      {groups.filter(({ actions }) => actions.length > 0 || home.runs?.legal === "running").map(({ group, actions }) => (
        <div className="step-group" key={group.tag}>
          <h3>{group.label}</h3>
          <p className="step-places">{group.hazards.map((item) => [item.room, item.location].filter(Boolean).join(", ")).join(" · ")}</p>
          {actions.map((tile) => renderTile(tile, stepPlace(tile, group)))}
          {home.runs?.legal === "running" && actions.length === 0 && (
            <p className="note">The steps for this problem are still loading.</p>
          )}
        </div>
      ))}
      {shared.length > 0 && (
        <div className="step-group">
          {shared.map((tile) => renderTile(tile, ""))}
        </div>
      )}
    </section>
  );
}

function Correspondence({ messages }: { messages?: Message[] }) {
  const thread = threadMessages(messages);
  if (!thread.length) {
    return (
      <p className="note">
        Correspondence starts after you send the first written notice from Next steps.
        Mail with the landlord from before that send is not shown.
      </p>
    );
  }
  return (
    <>
      <p className="note">Each tile is one email after your first notice. Sent, then received, then sent, then received.</p>
      {thread.map((item) => (
        <article key={item.id} className={"mail-tile " + (item.direction === "out" ? "is-sent" : "is-received")}>
          <header>
            <p className="step-kicker">{item.direction === "out" ? "Sent" : "Received"}</p>
            <span className={"status" + (item.direction === "out" ? " is-done" : "")}>{item.status}</span>
          </header>
          <h2>{item.subject || "(no subject)"}</h2>
          <p className="note">{item.direction === "out" ? "To" : "From"} {item.counterparty}</p>
          {item.body ? <p className="mail-body">{item.body}</p> : null}
          <p className="cite">{item.at}</p>
        </article>
      ))}
    </>
  );
}

function RecordTiles({ records }: { records: HazardDetail["blocks"]["landlord"]["records"] }) {
  const [openId, setOpenId] = useState("");
  return (
    <>
      {(records || []).map((record, index) => {
        const id = record.id || `rec-${index + 1}`;
        const open = openId === id;
        return (
          <button
            key={id}
            type="button"
            className={"record-tile " + (record.closed ? "closed" : "open")}
            aria-expanded={open}
            onClick={() => setOpenId(open ? "" : id)}
          >
            {record.date ? <span>{record.date}</span> : <span>{record.status || "Record"}</span>}
            <strong>{record.title}</strong>
            {open && <p>{record.fix || record.status || "No fix recorded."}</p>}
          </button>
        );
      })}
    </>
  );
}

function PreviewBody({
  preview, to, setTo, showTo,
}: {
  preview: Preview;
  to: string;
  setTo: (v: string) => void;
  showTo?: boolean;
}) {
  return (
    <>
      {preview.note && <p className="note">{preview.note}</p>}
      {preview.subject && <p><b>{preview.subject}</b></p>}
      {preview.body && <p>{preview.body}</p>}
      {preview.filled.map((row) => (
        <div key={row.label} className="filled"><b>{row.label}</b><span>{row.value}</span></div>
      ))}
      {preview.still_needed.length > 0 && (
        <>
          <p>Still yours to fill in</p>
          <ul>{preview.still_needed.map((item) => <li key={item}>{item}</li>)}</ul>
        </>
      )}
      {preview.form && (
        <p>Official form: <a href={preview.form.url} target="_blank" rel="noreferrer">{preview.form.name}</a></p>
      )}
      {preview.steps.length > 0 && (
        <ol className="steps">{preview.steps.map((step) => <li key={step}>{step}</li>)}</ol>
      )}
      {preview.phone && !preview.form && <p>Dials {preview.phone}</p>}
      {showTo && (
        <label className="field">To
          <input value={to} onChange={(e) => setTo(e.target.value)} placeholder="landlord@email.com" type="email" autoComplete="email" />
        </label>
      )}
    </>
  );
}
